import pytest
from pydantic import ValidationError

from backend.audit_agent.config import settings
from backend.audit_agent.task_settings import TaskSettingsStore, TaskSettingsConflict


def test_unsaved_settings_match_service_execution_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "investigation_max_posts", 30)
    monkeypatch.setattr(settings, "m3_comments_per_post", 123)

    current = TaskSettingsStore(tmp_path / "audit.sqlite3").get()

    assert current["revision"] == 0
    assert current["parameters"]["max_notes"] == 1
    assert current["parameters"]["max_total_notes"] == 5
    assert current["parameters"]["max_comments"] == 123
    assert current["parameters"]["search_sort"] == "general"
    assert current["parameters"]["max_items_per_minute"] == 5
    assert current["parameters"]["analysis_batch_size"] == 5


def test_settings_persist_and_reject_stale_writes(tmp_path):
    store = TaskSettingsStore(tmp_path / 'audit.sqlite3')
    assert store.get()['revision'] == 0
    saved = store.save({'max_notes': 2, 'analysis_batch_size': 1, 'search_sort': 'latest'}, 0)
    assert saved['parameters']['search_sort'] == 'latest'
    assert TaskSettingsStore(store.db_path).get() == saved
    with pytest.raises(TaskSettingsConflict):
        store.save({'max_notes': 3}, 0)
    assert store.get() == saved


@pytest.mark.parametrize('value', ['', 'hot', 1, None])
def test_settings_reject_unknown_search_sort(tmp_path, value):
    store = TaskSettingsStore(tmp_path / 'audit.sqlite3')
    with pytest.raises(ValidationError):
        store.save({'search_sort': value}, 0)


@pytest.mark.parametrize('value', [0, 31, 1.5, '2', True])
def test_settings_keep_supported_collection_boundary(tmp_path, value):
    store = TaskSettingsStore(tmp_path / 'audit.sqlite3')
    with pytest.raises(ValidationError):
        store.save({'max_notes': value}, 0)
    assert store.get()['revision'] == 0


def test_one_live_cap_clamps_both_saved_post_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "investigation_max_posts", 10)
    saved = TaskSettingsStore(tmp_path / "audit.sqlite3").save(
        {"max_notes": 30, "max_total_notes": 30, "analyze_limit": 30},
        0,
    )

    assert saved["parameters"]["max_notes"] == 10
    assert saved["parameters"]["max_total_notes"] == 10
    assert saved["parameters"]["analyze_limit"] == 10
