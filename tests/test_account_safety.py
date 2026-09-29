from datetime import datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from backend.audit_agent.account_rotation import AccountRotationManager, risk_cooldown_seconds
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.config import settings
from backend.audit_agent.crawler_account_store import CrawlerAccountStore, account_in_slow_mode
from backend.audit_agent.crawler_adapter import CrawlOutput, MediaCrawlerAdapter


@pytest.fixture
def safety_settings(monkeypatch):
    monkeypatch.setattr(settings, "crawler_risk_cooldown_base_seconds", 1800)
    monkeypatch.setattr(settings, "crawler_risk_cooldown_max_seconds", 21600)
    monkeypatch.setattr(settings, "crawler_new_account_days", 3)
    monkeypatch.setattr(settings, "crawler_slow_factor", 3)
    monkeypatch.setattr(settings, "crawler_account_requests_per_minute", 20)
    monkeypatch.setattr(settings, "crawler_account_min_interval", 3.0)


def _store_with_account(tmp_path, name="a", *, logged_in_days_ago=10):
    store = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    cipher = AuthStateCipher(encryption_key=Fernet.generate_key())
    account = store.create(platform="dy", display_name=name)
    store.save_auth_state(account["id"], cipher.encrypt({"cookies": [], "origins": []}))
    login_at = (datetime.now() - timedelta(days=logged_in_days_ago)).isoformat(timespec="seconds")
    with store._connect() as conn:
        conn.execute("UPDATE crawler_accounts SET auth_state_updated_at=? WHERE id=?", (login_at, account["id"]))
    return store, cipher, store.get(account["id"])


def _cooldown_seconds(account):
    return (datetime.fromisoformat(account["cooldown_until"]) - datetime.now()).total_seconds()


def test_risk_cooldown_escalates_and_is_capped(safety_settings):
    assert [risk_cooldown_seconds(n) for n in (1, 2, 3, 4, 5, 6)] == [1800, 3600, 7200, 14400, 21600, 21600]


def test_repeated_risk_within_a_day_escalates_the_cooldown(tmp_path, safety_settings):
    store, cipher, account = _store_with_account(tmp_path)
    manager = AccountRotationManager(store, cipher)
    assert manager.cool_down_after_risk(account["id"]) == 1800
    stored = store.get(account["id"])
    assert stored["risk_count"] == 1 and stored["failure_kind"] == "verify"
    assert 1790 < _cooldown_seconds(stored) <= 1800
    assert manager.cool_down_after_risk(account["id"]) == 3600
    assert store.get(account["id"])["risk_count"] == 2
    for _ in range(5):
        seconds = manager.cool_down_after_risk(account["id"])
    assert seconds == 21600


def test_risk_older_than_a_day_restarts_the_count(tmp_path, safety_settings):
    store, cipher, account = _store_with_account(tmp_path)
    manager = AccountRotationManager(store, cipher)
    manager.cool_down_after_risk(account["id"])
    manager.cool_down_after_risk(account["id"])
    stale = (datetime.now() - timedelta(hours=25)).isoformat(timespec="seconds")
    with store._connect() as conn:
        conn.execute("UPDATE crawler_accounts SET last_risk_at=? WHERE id=?", (stale, account["id"]))
    assert manager.cool_down_after_risk(account["id"]) == 1800
    assert store.get(account["id"])["risk_count"] == 1


def test_clean_task_resets_the_risk_count(tmp_path, safety_settings):
    store, cipher, account = _store_with_account(tmp_path)
    AccountRotationManager(store, cipher).cool_down_after_risk(account["id"])
    store.reset_risk(account["id"])
    assert store.get(account["id"])["risk_count"] == 0
    assert AccountRotationManager(store, cipher).cool_down_after_risk(account["id"]) == 1800


def test_risk_columns_are_added_to_an_existing_database(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE crawler_accounts (id TEXT PRIMARY KEY, platform TEXT NOT NULL, display_name TEXT NOT NULL,"
                     " platform_account_id TEXT, status TEXT NOT NULL DEFAULT 'login_required', last_validated_at TEXT,"
                     " last_used_at TEXT, last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    CrawlerAccountStore(path)
    CrawlerAccountStore(path)       # idempotent
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(crawler_accounts)")}
    assert {"risk_count", "last_risk_at"} <= columns


def test_slow_mode_for_new_login_and_recently_blocked_accounts(tmp_path, safety_settings):
    store, cipher, normal = _store_with_account(tmp_path, "normal")
    assert account_in_slow_mode(normal) is False
    assert account_in_slow_mode({**normal, "auth_state_updated_at": (datetime.now() - timedelta(days=1)).isoformat()}) is True
    assert account_in_slow_mode({**normal, "risk_count": 1}) is True


class RecordingAdapter(MediaCrawlerAdapter):
    def _build_runner(self):
        return ["python", "main.py"]

    def _run_command(self, **kwargs):
        return CrawlOutput(platform=kwargs["platform"], contents=[], comments=[], output_dir=kwargs["save_root"],
                           command=kwargs["command"])


def _flag(command, name):
    return command[command.index(name) + 1]


def _commands_for(adapter, account_id, save_root: Path):
    search = adapter.run_search(platform="dy", keyword="测试", start_page=0, max_notes=1, max_comments=0,
                                max_concurrency=1, max_items_per_minute=9, get_sub_comment=False,
                                save_root=save_root / "s", account_id=account_id).command
    creator = adapter.run_creator(platform="dy", creator_id="c1", max_notes=1, max_comments=0, max_concurrency=1,
                                  max_items_per_minute=9, get_sub_comment=False, save_root=save_root / "c",
                                  account_id=account_id).command
    try:
        adapter.run_detail("dy", "d1", source_keyword="测试", max_comments=0, max_concurrency=1,
                           max_items_per_minute=9, get_sub_comment=False, save_root=save_root / "d",
                           account_id=account_id)
    except Exception as exc:     # the recording adapter returns no target post
        assert "d1" in str(exc)
    detail = adapter.last_detail_command
    return search, creator, detail


@pytest.mark.parametrize("case", ["normal", "new_login", "recently_blocked"])
def test_every_account_command_carries_per_account_pacing(tmp_path, safety_settings, case):
    store, _cipher, account = _store_with_account(tmp_path, logged_in_days_ago=1 if case == "new_login" else 10)
    if case == "recently_blocked":
        with store._connect() as conn:
            conn.execute("UPDATE crawler_accounts SET risk_count=1 WHERE id=?", (account["id"],))

    class DetailRecordingAdapter(RecordingAdapter):
        def _run_command(self, **kwargs):
            if "--specified_id" in kwargs["command"]:
                self.last_detail_command = kwargs["command"]
            return super()._run_command(**kwargs)

    adapter = DetailRecordingAdapter(tmp_path, account_store=store)
    slow = case != "normal"
    for command in _commands_for(adapter, account["id"], tmp_path):
        assert _flag(command, "--account_requests_per_minute") == ("6" if slow else "20")
        assert float(_flag(command, "--account_min_interval")) == (9.0 if slow else 3.0)
        assert _flag(command, "--crawler_max_items_per_minute") == ("3" if slow else "9")


def test_commands_without_an_account_carry_no_account_pacing(tmp_path, safety_settings):
    adapter = RecordingAdapter(tmp_path, account_store=CrawlerAccountStore(tmp_path / "a.sqlite3"))
    command = adapter.run_search(platform="dy", keyword="测试", start_page=0, max_notes=1, max_comments=0,
                                 max_concurrency=1, max_items_per_minute=9, get_sub_comment=False,
                                 save_root=tmp_path / "s").command
    assert "--account_requests_per_minute" not in command
    assert _flag(command, "--crawler_max_items_per_minute") == "9"


def test_candidate_command_carries_official_profile_skip_regex(tmp_path):
    adapter = RecordingAdapter(tmp_path, account_store=CrawlerAccountStore(tmp_path / "a.sqlite3"))
    command = adapter.run_search(platform="dy", keyword="测试", start_page=0, max_notes=1, max_comments=0,
                                 max_concurrency=1, max_items_per_minute=9, get_sub_comment=False,
                                 save_root=tmp_path / "s", fetch_author_profile=True,
                                 skip_profile_verify_regex="官方|局$").command
    assert _flag(command, "--dy_skip_profile_verify_regex") == "官方|局$"
    plain = adapter.run_search(platform="dy", keyword="测试", start_page=0, max_notes=1, max_comments=0,
                               max_concurrency=1, max_items_per_minute=9, get_sub_comment=False,
                               save_root=tmp_path / "p").command
    assert "--dy_skip_profile_verify_regex" not in plain
