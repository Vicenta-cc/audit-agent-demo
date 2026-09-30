from __future__ import annotations

import sqlite3
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

import backend.audit_agent.pipeline as pipeline_module
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_adapter import (CrawlerAuthenticationError, CrawlerRateLimitError,
                                                 CrawlerVerificationError, CrawlOutput)
from backend.audit_agent.triage import CandidateScore
from backend.task_admission.resources import acquire_account_handle


class AdmissionStub:
    """Just the task_admissions columns the reserve rule reads."""

    def __init__(self, path: Path, waiting=0):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS task_admissions (task_id TEXT, state TEXT, waiting_reason TEXT,"
                       " decision TEXT NOT NULL DEFAULT 'ADMITTED')")
            db.execute("DELETE FROM task_admissions")
            db.execute("INSERT INTO task_admissions (task_id, state, waiting_reason) VALUES ('self','RESERVED','')")
            db.execute("INSERT INTO task_admissions (task_id, state, waiting_reason) VALUES ('running','RESERVED','')")
            db.execute("INSERT INTO task_admissions (task_id, state, waiting_reason) VALUES ('done','COMPLETED','account_busy')")
            for index in range(waiting):
                db.execute("INSERT INTO task_admissions (task_id, state, waiting_reason) VALUES (?, 'RESERVED', 'account_busy')", (f"q{index}",))

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = CrawlerAccountStore(tmp_path / "accounts.sqlite3")
    cipher = AuthStateCipher(Fernet.generate_key())
    logs: list[str] = []
    monkeypatch.setattr(pipeline_module, "crawler_account_store", store)
    monkeypatch.setattr(pipeline_module, "auth_state_cipher", cipher)
    monkeypatch.setattr(pipeline_module.settings, "crawler_browser_profile_root", tmp_path / "profiles")
    monkeypatch.setattr(pipeline_module.settings, "triage_accounts_per_task", 3)
    monkeypatch.setattr(pipeline_module.settings, "app_auth_mode", "disabled")
    monkeypatch.setattr(pipeline_module.settings, "crawler_risk_cooldown_base_seconds", 1800)
    monkeypatch.setattr(pipeline_module.settings, "crawler_risk_cooldown_max_seconds", 21600)
    monkeypatch.setattr(pipeline_module.settings, "crawler_new_account_days", 3)
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda _job, message, **_k: logs.append(message))

    def add(name, scope="private"):
        account = store.create(platform="dy", display_name=name, access_scope=scope)
        store.save_auth_state(account["id"], cipher.encrypt({"account": name}))
        with store._connect() as conn:     # 不是新登录，避免慢速模式干扰断言
            conn.execute("UPDATE crawler_accounts SET auth_state_updated_at='2026-01-01T00:00:00' WHERE id=?",
                         (account["id"],))
        return store.get(account["id"])

    return SimpleNamespace(store=store, cipher=cipher, logs=logs, add=add, tmp=tmp_path)


def _pipeline(env, *, waiting=0, crawler=None):
    pipeline = pipeline_module.AuditPipeline.__new__(pipeline_module.AuditPipeline)
    pipeline.job_id = "job-rot"
    pipeline._admission_execution = (AdmissionStub(env.tmp / f"adm-{waiting}.sqlite3", waiting), "self", "tok")
    pipeline.crawler = crawler
    return pipeline


def _names(rotation):
    return [item["display_name"] for item in rotation]


def _rotation(env, pipeline, primary, leases):
    return pipeline._triage_account_rotation("dy", primary, {"account": primary["display_name"]}, leases)


def test_rotation_takes_up_to_three_idle_permitted_accounts(env):
    accounts = [env.add(name) for name in "ABCDE"]
    with ExitStack() as leases:
        rotation = _rotation(env, _pipeline(env), accounts[0], leases)
        assert len(rotation) == 3 and rotation[0]["id"] == accounts[0]["id"]
        assert [item["auth_state"] for item in rotation] == [{"account": n} for n in _names(rotation)]
        # extra accounts stay leased for the whole sweep
        held = [a for a in accounts if a["id"] in {r["id"] for r in rotation[1:]}]
        assert all(acquire_account_handle("dy", a["id"]) is None for a in held)
    assert any(line.startswith("本任务轮换账号：") and "、" in line for line in env.logs)
    for account in accounts[1:]:     # released afterwards
        handle = acquire_account_handle("dy", account["id"])
        assert handle is not None
        handle.close()


def test_rotation_leaves_one_idle_account_per_waiting_task(env):
    accounts = [env.add(name) for name in "ABCDE"]
    with ExitStack() as leases:
        rotation = _rotation(env, _pipeline(env, waiting=2), accounts[0], leases)
        assert len(rotation) == 3
        idle = [a for a in accounts[1:] if a["id"] not in {r["id"] for r in rotation}]
        assert len(idle) == 2
    with ExitStack() as leases:        # 4 idle incl. primary, 2 waiting → only 1 extra
        busy = acquire_account_handle("dy", accounts[4]["id"])
        rotation = _rotation(env, _pipeline(env, waiting=2), accounts[0], leases)
        busy.close()
        assert len(rotation) == 2


def test_rotation_of_one_when_only_the_leased_account_is_idle(env):
    accounts = [env.add(name) for name in "ABC"]
    handles = [acquire_account_handle("dy", a["id"]) for a in accounts[1:]]
    try:
        with ExitStack() as leases:
            rotation = _rotation(env, _pipeline(env), accounts[0], leases)
        assert _names(rotation) == ["A"]
    finally:
        for handle in handles:
            handle.close()


def test_rotation_never_includes_other_users_private_accounts(env, monkeypatch):
    own, public, foreign = env.add("own"), env.add("public", "public"), env.add("foreign")
    monkeypatch.setattr(pipeline_module.settings, "app_auth_mode", "required")
    monkeypatch.setattr(pipeline_module, "_authorized_crawler_account_ids",
                        lambda job_id: frozenset({own["id"], public["id"]}))
    with ExitStack() as leases:
        rotation = _rotation(env, _pipeline(env), own, leases)
    assert _names(rotation) == ["own", "public"]
    assert foreign["id"] not in {r["id"] for r in rotation}


def test_accounts_per_task_of_one_keeps_single_account(env, monkeypatch):
    monkeypatch.setattr(pipeline_module.settings, "triage_accounts_per_task", 1)
    accounts = [env.add(name) for name in "ABC"]
    with ExitStack() as leases:
        assert _names(_rotation(env, _pipeline(env), accounts[0], leases)) == ["A"]


# ---- serial rotation inside the triage sweep ---------------------------------------------


class RotationCrawler:
    def __init__(self, fail=None):
        self.fail = dict(fail or {})     # (account_id, keyword) -> exception, raised once
        self.search_accounts: list[tuple[str, str]] = []
        self.detail_accounts: list[tuple[str, str]] = []
        self.search_kwargs: list[dict] = []

    def run_search(self, *, platform, keyword, save_root, account_id="", **kwargs):
        self.search_kwargs.append({**kwargs, "account_id": account_id})
        if (account_id, keyword) in self.fail:
            raise self.fail.pop((account_id, keyword))
        self.search_accounts.append((keyword, account_id))
        items = [{"aweme_id": f"{keyword}-1", "desc": "上分", "source_keyword": keyword}]
        return CrawlOutput(platform=platform, contents=items, comments=[], output_dir=Path(save_root))

    def run_detail(self, platform, content_id, *, source_keyword, save_root=None, account_id="", **kwargs):
        self.detail_accounts.append((source_keyword, account_id))
        item = {"aweme_id": content_id, "desc": "full", "source_keyword": source_keyword}
        return CrawlOutput(platform=platform, contents=[item], comments=[], output_dir=Path(save_root))

    def _load_platform_output(self, save_root, platform):
        items = [{"aweme_id": f"{kw}-1", "source_keyword": ""} for kw, _ in self.detail_accounts]
        return CrawlOutput(platform=platform, contents=items, comments=[], output_dir=Path(save_root))


class ScoreAll:
    def terms_for(self, category_ids):
        return []

    def score(self, content_key, rank, item, comments, terms, *, search_keyword="", rules=None):
        return CandidateScore(content_key, rank, 300, "rule", "", [], None, 0)


class NoIngestion:
    db_path = Path("/tmp/unused.sqlite3")

    def analyzed_content_keys(self, platform, keys):
        return set()


def _sweep(env, monkeypatch, crawler, rotation, keywords="词1,词2,词3,词4"):
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.settings, "triage_official_verify_patterns", ("官方", "局$"))
    pipeline = _pipeline(env, crawler=crawler)
    pipeline.ingestion = NoIngestion()
    pipeline._triage_engine = ScoreAll()
    request = SimpleNamespace(platform="dy", keyword=keywords, lexicon_category="", keyword_source="keyword",
                              max_comments=0, get_sub_comment=False, collect_comments=False, collect_media=False,
                              max_items_per_minute=5, search_sort="general", max_notes=1)
    save_root = env.tmp / "crawler"
    save_root.mkdir(exist_ok=True)
    return pipeline._run_triaged_search(
        request=request, save_root=save_root, start_page=1, max_total_notes=10, crawler_concurrency=1,
        account_auth_state=rotation[0]["auth_state"], crawler_account_id=rotation[0]["id"],
        rotation=rotation, content_callback=None, stream_items=False,
        stop_checker=lambda: False, started_callback=None, progress_callback=None,
    )


def _as_rotation(accounts):
    return [{"id": a["id"], "display_name": a["display_name"], "auth_state": {"account": a["display_name"]}}
            for a in accounts]


def test_keywords_rotate_serially_across_accounts_for_search_and_detail(env, monkeypatch):
    a, b, c = (env.add(name) for name in "ABC")
    crawler = RotationCrawler()
    _sweep(env, monkeypatch, crawler, _as_rotation([a, b, c]))
    expected = [("词1", a["id"]), ("词2", b["id"]), ("词3", c["id"]), ("词4", a["id"])]
    assert crawler.search_accounts == expected
    assert crawler.detail_accounts == expected
    assert crawler.search_kwargs[0]["auth_state"] == {"account": "A"}
    assert crawler.search_kwargs[1]["auth_state"] == {"account": "B"}
    # 官方号不补资料请求：候选采集带上判定规则里的官方识别片段
    assert all(kw["skip_profile_verify_regex"] == "官方|局$" for kw in crawler.search_kwargs)


def test_verification_retires_account_and_retries_same_keyword_on_next(env, monkeypatch):
    a, b, c = (env.add(name) for name in "ABC")
    crawler = RotationCrawler(fail={(b["id"], "词2"): CrawlerVerificationError("verify")})
    output = _sweep(env, monkeypatch, crawler, _as_rotation([a, b, c]))
    assert crawler.search_accounts == [("词1", a["id"]), ("词2", c["id"]), ("词3", a["id"]), ("词4", c["id"])]
    assert crawler.detail_accounts == crawler.search_accounts
    assert len(output.contents) == 4
    cooled = env.store.get(b["id"])
    assert cooled["risk_count"] == 1 and cooled["failure_kind"] == "verify"
    assert 1790 < (datetime.fromisoformat(cooled["cooldown_until"]) - datetime.now()).total_seconds() <= 1800
    assert "账号 B 触发平台验证，退出本任务轮换" in env.logs


def test_clean_sweep_resets_risk_count_of_accounts_used(env, monkeypatch):
    a, b = env.add("A"), env.add("B")
    with env.store._connect() as conn:
        conn.execute("UPDATE crawler_accounts SET risk_count=2 WHERE id IN (?, ?)", (a["id"], b["id"]))
    _sweep(env, monkeypatch, RotationCrawler(), _as_rotation([a, b]))
    assert env.store.get(a["id"])["risk_count"] == 0
    assert env.store.get(b["id"])["risk_count"] == 0


def test_authentication_error_expires_account_and_rotation_continues(env, monkeypatch):
    a, b = env.add("A"), env.add("B")
    crawler = RotationCrawler(fail={(a["id"], "词1"): CrawlerAuthenticationError("auth")})
    _sweep(env, monkeypatch, crawler, _as_rotation([a, b]), keywords="词1,词2")
    assert crawler.search_accounts == [("词1", b["id"]), ("词2", b["id"])]
    assert env.store.get(a["id"])["status"] == "expired"


def test_all_accounts_failing_raises_and_marks_risk_as_recorded(env, monkeypatch):
    a, b = env.add("A"), env.add("B")
    crawler = RotationCrawler(fail={(a["id"], "词1"): CrawlerVerificationError("verify"),
                                    (b["id"], "词1"): CrawlerVerificationError("verify")})
    with pytest.raises(CrawlerVerificationError) as raised:
        _sweep(env, monkeypatch, crawler, _as_rotation([a, b]))
    assert raised.value.account_risk_recorded is True
    assert env.store.get(a["id"])["risk_count"] == 1
    assert env.store.get(b["id"])["risk_count"] == 1


def test_keyword_failing_on_two_accounts_stops_without_touching_the_third(env, monkeypatch):
    a, b, c = (env.add(name) for name in "ABC")
    crawler = RotationCrawler(fail={(a["id"], "词1"): CrawlerVerificationError("verify"),
                                    (b["id"], "词1"): CrawlerVerificationError("verify")})
    with pytest.raises(CrawlerVerificationError) as raised:
        _sweep(env, monkeypatch, crawler, _as_rotation([a, b, c]))
    assert raised.value.account_risk_recorded is True
    assert [kw["account_id"] for kw in crawler.search_kwargs] == [a["id"], b["id"]]
    assert crawler.search_accounts == [] and crawler.detail_accounts == []
    assert env.store.get(a["id"])["risk_count"] == 1
    assert env.store.get(b["id"])["risk_count"] == 1
    untouched = env.store.get(c["id"])
    assert untouched["risk_count"] == 0 and not untouched["cooldown_until"]


def test_single_retry_is_per_keyword(env, monkeypatch):
    a, b, c, d = (env.add(name) for name in "ABCD")
    crawler = RotationCrawler(fail={(a["id"], "词1"): CrawlerVerificationError("verify"),
                                    (c["id"], "词2"): CrawlerVerificationError("verify")})
    _sweep(env, monkeypatch, crawler, _as_rotation([a, b, c, d]), keywords="词1,词2,词3")
    assert crawler.search_accounts == [("词1", b["id"]), ("词2", d["id"]), ("词3", b["id"])]


def test_rate_limit_is_not_rotated(env, monkeypatch):
    a, b = env.add("A"), env.add("B")
    crawler = RotationCrawler(fail={(a["id"], "词1"): CrawlerRateLimitError("gate")})
    with pytest.raises(CrawlerRateLimitError):
        _sweep(env, monkeypatch, crawler, _as_rotation([a, b]))
    assert crawler.search_accounts == []
    assert env.store.get(a["id"])["risk_count"] == 0


def _run_pipeline(env, monkeypatch, crawler, primary, engine=None, settings_auto_analyze=False, **overrides):
    from backend.audit_agent.ingestion import AuditResultStore, IngestionStore
    from backend.audit_agent.job_store import JobStore

    jobs = JobStore(env.tmp / "jobs.sqlite3")
    config = {'platform': 'dy', 'display_name': 'rotation', 'crawl_mode': 'search', 'keyword': '词1,词2',
              'keyword_source': 'keyword', 'lexicon_category': 'soft', 'library_ids': [], 'capabilities': ['text'],
              'scoring_template': 'balanced', 'rule_snapshot': {}, 'lexicon_keywords': [], 'creator_url': '',
              'creator_id': '', 'start_page': 0, 'max_notes': 1, 'max_comments': 0, 'max_concurrency': 1,
              'max_items_per_minute': 1, 'crawler_account_id': primary['id'], 'get_sub_comment': False,
              'analyze_limit': 0, 'run_crawler': True, 'source_output_id': None, 'analysis_batch_size': 1,
              'prompt_profile_snapshot': {}, 'policy_id': ''}
    config.update(overrides)
    jobs.create(job_id="job-rot", **{k: v for k, v in config.items() if not k.startswith("_")})
    monkeypatch.setattr(pipeline_module, "job_store", jobs)
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    monkeypatch.setattr(pipeline_module.settings, "outputs_dir", env.tmp / "outputs")
    monkeypatch.setattr(pipeline_module.settings, "auto_analyze_crawled_content", settings_auto_analyze)
    pipeline = pipeline_module.AuditPipeline.__new__(pipeline_module.AuditPipeline)
    pipeline.job_id = "job-rot"
    pipeline.crawler = crawler
    pipeline.ingestion = IngestionStore(env.tmp / "ingestion.sqlite3")
    pipeline.audit_results = AuditResultStore(env.tmp / "ingestion.sqlite3")
    pipeline.qwen = SimpleNamespace(provider_failure='')
    pipeline.prompt_profile_snapshot = {}
    pipeline.audit_config_revision_id = ''
    pipeline.rule_snapshot = {}
    pipeline.authoritative_m3 = False
    pipeline._triage_engine = engine or ScoreAll()
    pipeline.run(SimpleNamespace(**config, _authoritative_m3_contract=False))
    return jobs.get("job-rot")


class ScoreNone(ScoreAll):
    def score(self, content_key, rank, item, comments, terms, *, search_keyword="", rules=None):
        return CandidateScore(content_key, rank, 0, "none", "", [], None, 0)


# 新任务冻结 auto_analyze=True 走流式分析；旧快照没有 auto_analyze，走「按冻结条数选取」
_PATHS = pytest.mark.parametrize("snapshot", [
    {"auto_analyze": True, "analyze_limit": 2},
    {"_confirmed_analyze_limit": 2, "analyze_limit": 2},
], ids=["stream", "confirmed-selection"])


@_PATHS
def test_all_terms_without_suspicious_candidates_end_normally(env, monkeypatch, snapshot):
    # 选择模式下所有词都没有可疑候选：任务正常结束，不是 no_valid_content_selected 失败
    a = env.add("A")
    crawler = RotationCrawler()
    job = _run_pipeline(env, monkeypatch, crawler, a, engine=ScoreNone(), max_total_notes=2,
                        settings_auto_analyze=True, **snapshot)
    assert job["status"] == "completed"
    assert not job["error"]
    assert job["control"]["no_suspicious_content"] is True
    assert crawler.detail_accounts == []
    messages = [log["message"] for log in job["logs"]]
    assert any(m.startswith("未发现可疑内容：2 个搜索词均无可疑候选") for m in messages)
    assert not any("no_valid_content_selected" in m for m in messages)


class RaisingSearchCrawler(RotationCrawler):
    def __init__(self, failing_words):
        super().__init__()
        self.failing_words = set(failing_words)

    def run_search(self, *, platform, keyword, save_root, account_id="", **kwargs):
        if keyword in self.failing_words:
            raise RuntimeError("MediaCrawler failed with exit code 1")
        return super().run_search(platform=platform, keyword=keyword, save_root=save_root,
                                  account_id=account_id, **kwargs)


class EmptyDetailCrawler(RotationCrawler):
    def run_detail(self, platform, content_id, *, source_keyword, save_root=None, account_id="", **kwargs):
        return CrawlOutput(platform=platform, contents=[], comments=[], output_dir=Path(save_root))


class ZeroCandidateCrawler(RotationCrawler):
    def run_search(self, *, platform, keyword, save_root, account_id="", **kwargs):
        self.search_accounts.append((keyword, account_id))
        return CrawlOutput(platform=platform, contents=[], comments=[], output_dir=Path(save_root))


@_PATHS
def test_already_audited_suspicious_content_ends_normally_with_truthful_wording(env, monkeypatch, snapshot):
    # 可疑候选都已被此前任务审核：没有新内容，正常结束，但措辞说明是「无新的」
    from backend.audit_agent.ingestion import IngestionStore
    monkeypatch.setattr(IngestionStore, "analyzed_content_keys", lambda self, platform, keys: set(keys))
    a = env.add("A")
    crawler = RotationCrawler()
    job = _run_pipeline(env, monkeypatch, crawler, a, engine=ScoreAll(), max_total_notes=2,
                        settings_auto_analyze=True, **snapshot)
    assert job["status"] == "completed"
    assert job["control"]["no_suspicious_content"] is True
    assert crawler.detail_accounts == []
    messages = [log["message"] for log in job["logs"]]
    assert any(m.startswith("未发现新的可疑内容：2 个搜索词无新的可疑候选（其中 2 个词的可疑内容此前已审核）")
               for m in messages)
    assert not any(m.startswith("未发现可疑内容") for m in messages)


class VerifyOnFirstAccountDetailCrawler(RotationCrawler):
    """The only suspicious pick hits verification on account A; the retry on B has no alternative."""
    def __init__(self, first_account_id):
        super().__init__()
        self.first_account_id = first_account_id

    def run_search(self, *, platform, keyword, save_root, account_id="", **kwargs):
        if keyword != "词1":
            self.search_kwargs.append({**kwargs, "account_id": account_id})
            return CrawlOutput(platform=platform, contents=[{"aweme_id": f"{keyword}-1", "desc": "正常",
                                                            "source_keyword": keyword}],
                               comments=[], output_dir=Path(save_root))
        return super().run_search(platform=platform, keyword=keyword, save_root=save_root,
                                  account_id=account_id, **kwargs)

    def run_detail(self, platform, content_id, *, source_keyword, save_root=None, account_id="", **kwargs):
        if account_id == self.first_account_id:
            raise CrawlerVerificationError("verify")
        return super().run_detail(platform, content_id, source_keyword=source_keyword, save_root=save_root,
                                  account_id=account_id, **kwargs)


class ScoreOnlyWord1(ScoreAll):
    def score(self, content_key, rank, item, comments, terms, *, search_keyword="", rules=None):
        value = 300 if search_keyword == "词1" else 0
        return CandidateScore(content_key, rank, value, "rule" if value else "none", "", [], None, 0)


@_PATHS
def test_pick_lost_to_verification_is_not_reported_as_no_suspicious_content(env, monkeypatch, snapshot):
    a, b = env.add("A"), env.add("B")
    crawler = VerifyOnFirstAccountDetailCrawler(a["id"])
    job = _run_pipeline(env, monkeypatch, crawler, a, engine=ScoreOnlyWord1(), max_total_notes=2,
                        settings_auto_analyze=True, **snapshot)
    messages = [log["message"] for log in job["logs"]]
    assert any("改用账号" in m for m in messages)
    assert crawler.detail_accounts == []
    assert job["control"]["no_suspicious_content"] is False
    assert not any(m.startswith("未发现") for m in messages)
    if "_confirmed_analyze_limit" in snapshot:
        assert job["status"] == "failed"
        assert job["error"].startswith("no_valid_content_selected:")
    else:
        assert job["status"] == "completed"


@_PATHS
@pytest.mark.parametrize("crawler, engine", [
    (lambda: RaisingSearchCrawler({"词1", "词2"}), ScoreNone),     # 采集失败，不是「无可疑」
    (EmptyDetailCrawler, ScoreAll),                                 # 选中了但精采无产出
    (ZeroCandidateCrawler, ScoreNone),                              # 搜不到候选
    (lambda: RaisingSearchCrawler({"词2"}), ScoreNone),             # 一词干净跳过、一词出错
], ids=["crawler-error", "detail-empty", "zero-candidates", "mixed-clean-and-error"])
def test_failures_are_not_reported_as_no_suspicious_content(env, monkeypatch, snapshot, crawler, engine):
    a = env.add("A")
    job = _run_pipeline(env, monkeypatch, crawler(), a, engine=engine(), max_total_notes=2,
                        settings_auto_analyze=True, **snapshot)
    assert job["control"]["no_suspicious_content"] is False
    messages = [log["message"] for log in job["logs"]]
    assert not any(m.startswith("未发现可疑内容") for m in messages)
    if "_confirmed_analyze_limit" in snapshot:
        assert job["status"] == "failed"
        assert job["error"].startswith("no_valid_content_selected:")
    else:
        # 流式路径由 worker 的完成门槛判为 no_valid_content_selected（见 test_investigation_creation_m3）
        assert job["status"] == "completed"


def test_partial_selection_keeps_the_flag_off(env, monkeypatch):
    a = env.add("A")
    job = _run_pipeline(env, monkeypatch, RotationCrawler(), a, max_total_notes=2)
    assert job["status"] == "completed"
    assert job["control"]["no_suspicious_content"] is False


def test_pipeline_run_counts_each_account_risk_once_when_rotation_is_exhausted(env, monkeypatch):
    a, b = env.add("A"), env.add("B")
    crawler = RotationCrawler(fail={(a["id"], "词1"): CrawlerVerificationError("verify"),
                                    (b["id"], "词1"): CrawlerVerificationError("verify")})
    job = _run_pipeline(env, monkeypatch, crawler, a)
    assert job["status"] == "failed"
    assert job["error"].startswith("crawler_account_verification_required:")
    assert env.store.get(a["id"])["risk_count"] == 1
    assert env.store.get(b["id"])["risk_count"] == 1
    assert crawler.search_accounts == []


@pytest.mark.parametrize("per_task", [2, 3])
def test_pipeline_run_stops_after_two_accounts_fail_one_keyword(env, monkeypatch, per_task):
    # 外层账号循环不得换上第三个账号重跑同一个词：一个高风险词最多连累两个账号
    monkeypatch.setattr(pipeline_module.settings, "triage_accounts_per_task", per_task)
    a, b, c, d = (env.add(name) for name in "ABCD")
    crawler = RotationCrawler(fail={(acc["id"], "词1"): CrawlerVerificationError("verify") for acc in (a, b, c, d)})
    job = _run_pipeline(env, monkeypatch, crawler, a)
    assert job["status"] == "failed"
    assert job["error"].startswith("crawler_account_verification_required:")
    tried = [kw["account_id"] for kw in crawler.search_kwargs]
    assert len(tried) == 2 and tried[0] == a["id"] and len(set(tried)) == 2
    for account in (a, b, c, d):
        stored = env.store.get(account["id"])
        if account["id"] in tried:
            assert stored["risk_count"] == 1
        else:
            assert stored["risk_count"] == 0 and not stored["cooldown_until"]
    assert not any("继续尝试剩余" in log["message"] for log in job["logs"])


def test_rotation_releases_leases_when_acquisition_fails(env, monkeypatch):
    accounts = [env.add(name) for name in "ABC"]
    original = env.store.available_accounts
    calls = {"n": 0}

    def flaky(platform, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:       # recheck after the second extra lease
            raise RuntimeError("database is locked")
        return original(platform, **kwargs)

    monkeypatch.setattr(env.store, "available_accounts", flaky)
    with pytest.raises(RuntimeError):
        with ExitStack() as leases:
            _rotation(env, _pipeline(env), accounts[0], leases)
    for account in accounts[1:]:
        handle = acquire_account_handle("dy", account["id"])
        assert handle is not None
        handle.close()
    from backend.task_admission.resources import inherited_fds
    assert inherited_fds() == ()


def test_creator_task_under_triage_mode_resets_risk_count(env, monkeypatch):
    class CreatorCrawler(RotationCrawler):
        def run_creator(self, *, platform, save_root, account_id="", **kwargs):
            self.search_kwargs.append({**kwargs, "account_id": account_id})
            item = {"aweme_id": "c-1", "desc": "x"}
            return CrawlOutput(platform=platform, contents=[item], comments=[], output_dir=Path(save_root),
                               command=["fake"])

    a = env.add("A")
    with env.store._connect() as conn:
        conn.execute("UPDATE crawler_accounts SET risk_count=1 WHERE id=?", (a["id"],))
    crawler = CreatorCrawler()

    from backend.audit_agent.job_store import JobStore
    real_create = JobStore.create

    def creator_create(self, **kwargs):
        return real_create(self, **{**kwargs, "crawl_mode": "creator", "creator_id": "u1"})

    monkeypatch.setattr(JobStore, "create", creator_create)
    original_run = pipeline_module.AuditPipeline.run

    def run_as_creator(self, request, crawl_epoch=None):
        request.crawl_mode = "creator"
        request.creator_id = "u1"
        return original_run(self, request, crawl_epoch)

    monkeypatch.setattr(pipeline_module.AuditPipeline, "run", run_as_creator)
    job = _run_pipeline(env, monkeypatch, crawler, a)
    assert [kw["account_id"] for kw in crawler.search_kwargs] == [a["id"]], job.get("error")
    assert env.store.get(a["id"])["risk_count"] == 0


def test_cancelled_waiting_task_reserves_no_account(env):
    accounts = [env.add(name) for name in "ABC"]
    pipeline = _pipeline(env, waiting=1)
    admission_store = pipeline._admission_execution[0]
    with sqlite3.connect(admission_store.path) as db:
        db.execute("UPDATE task_admissions SET decision='CANCELLED' WHERE task_id='q0'")
    with ExitStack() as leases:
        assert len(_rotation(env, pipeline, accounts[0], leases)) == 3


def test_each_keyword_logs_the_account_it_uses(env, monkeypatch):
    a, b, c = (env.add(name) for name in "ABC")
    crawler = RotationCrawler(fail={(b["id"], "词2"): CrawlerVerificationError("verify")})
    _sweep(env, monkeypatch, crawler, _as_rotation([a, b, c]), keywords="词1,词2,词3")
    assert [line for line in env.logs if "使用账号" in line] == [
        "词「词1」使用账号 A", "词「词2」使用账号 B", "词「词2」使用账号 C", "词「词3」使用账号 A"]


def test_only_accounts_that_crawled_successfully_get_their_risk_reset(env, monkeypatch):
    a, b, c = (env.add(name) for name in "ABC")
    with env.store._connect() as conn:
        conn.execute("UPDATE crawler_accounts SET risk_count=1")

    class BrokenForB(RotationCrawler):
        def run_search(self, *, platform, keyword, save_root, account_id="", **kwargs):
            if account_id == b["id"]:
                raise RuntimeError("MediaCrawler failed with exit code 2")
            return super().run_search(platform=platform, keyword=keyword, save_root=save_root,
                                      account_id=account_id, **kwargs)

    _sweep(env, monkeypatch, BrokenForB(), _as_rotation([a, b, c]), keywords="词1,词2,词3")
    assert [env.store.get(x["id"])["risk_count"] for x in (a, b, c)] == [0, 1, 0]


class TwoCandidateCrawler(RotationCrawler):
    """词1 has two suspicious candidates; precise collection of p1 on account A hits verification."""

    def __init__(self, failing_account):
        super().__init__()
        self.failing_account = failing_account
        self.searches = 0
        self.details: list[tuple[str, str]] = []

    def run_search(self, *, platform, keyword, save_root, account_id="", **kwargs):
        self.searches += 1
        items = [{"aweme_id": "p1", "desc": "上分", "source_keyword": keyword},
                 {"aweme_id": "p2", "desc": "上分", "source_keyword": keyword}]
        return CrawlOutput(platform=platform, contents=items, comments=[], output_dir=Path(save_root))

    def run_detail(self, platform, content_id, *, source_keyword, save_root=None, account_id="", **kwargs):
        if account_id == self.failing_account and content_id == "p1":
            raise CrawlerVerificationError("verify during detail")
        self.details.append((content_id, account_id))
        self.detail_accounts.append((source_keyword, account_id))
        item = {"aweme_id": content_id, "desc": "full", "source_keyword": source_keyword}
        return CrawlOutput(platform=platform, contents=[item], comments=[], output_dir=Path(save_root))

    def _load_platform_output(self, save_root, platform):
        items = [{"aweme_id": cid, "source_keyword": ""} for cid, _ in self.details]
        return CrawlOutput(platform=platform, contents=items, comments=[], output_dir=Path(save_root))


class RankedEngine(ScoreAll):
    def score(self, content_key, rank, item, comments, terms, *, search_keyword="", rules=None):
        return CandidateScore(content_key, rank, 400 - rank, "rule", "", [], None, 0)


def test_detail_verification_retries_with_the_next_candidate(env, monkeypatch):
    import json
    a, b = env.add("A"), env.add("B")
    crawler = TwoCandidateCrawler(failing_account=a["id"])
    monkeypatch.setattr(pipeline_module.settings, "triage_mode", "select")
    pipeline = _pipeline(env, crawler=crawler)
    pipeline.ingestion = NoIngestion()
    engine = RankedEngine()
    scored = []
    engine_score = engine.score
    engine.score = lambda *args, **kw: scored.append(args[0]) or engine_score(*args, **kw)
    pipeline._triage_engine = engine
    request = SimpleNamespace(platform="dy", keyword="词1", lexicon_category="", keyword_source="keyword",
                              max_comments=0, get_sub_comment=False, collect_comments=False, collect_media=False,
                              max_items_per_minute=5, search_sort="general", max_notes=1)
    save_root = env.tmp / "crawler"
    save_root.mkdir()
    output = pipeline._run_triaged_search(
        request=request, save_root=save_root, start_page=1, max_total_notes=10, crawler_concurrency=1,
        account_auth_state=None, crawler_account_id=a["id"], rotation=_as_rotation([a, b]),
        content_callback=None, stream_items=False, stop_checker=lambda: False, started_callback=None,
        progress_callback=None,
    )
    assert crawler.details == [("p2", b["id"])]
    assert [item["aweme_id"] for item in output.contents] == ["p2"]
    assert crawler.searches == 1 and scored == ["p1", "p2"]      # 沿用已打分的候选，不重搜不重打分
    [candidates_file] = save_root.glob("candidates/*/candidates.json")
    saved = json.loads(candidates_file.read_text(encoding="utf-8"))
    assert "p1" not in json.dumps(saved.get("collected_keys", []))
    assert env.store.get(a["id"])["risk_count"] == 1
