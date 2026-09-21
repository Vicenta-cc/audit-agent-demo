import pytest
from pydantic import ValidationError

from backend.audit_agent.config import settings
from backend.audit_agent.task_settings import TaskSettingsStore, TaskSettingsConflict


def test_unsaved_settings_match_service_execution_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "m3_posts_per_keyword", 4)
    monkeypatch.setattr(settings, "m3_comments_per_post", 123)
    monkeypatch.setattr(settings, "m3_analyze_limit", 4)

    current = TaskSettingsStore(tmp_path / "audit.sqlite3").get()

    assert current["revision"] == 0
    assert current["parameters"]["max_notes"] == 4
    assert current["parameters"]["max_total_notes"] == 5
    assert current["parameters"]["max_comments"] == 123
    assert current["parameters"]["max_items_per_minute"] == 5
    assert current["parameters"]["analysis_batch_size"] == 5


def test_settings_persist_and_reject_stale_writes(tmp_path):
    store = TaskSettingsStore(tmp_path / 'audit.sqlite3')
    assert store.get()['revision'] == 0
    saved = store.save({'max_notes': 2, 'analysis_batch_size': 1}, 0)
    assert TaskSettingsStore(store.db_path).get() == saved
    with pytest.raises(TaskSettingsConflict):
        store.save({'max_notes': 3}, 0)
    assert store.get() == saved


@pytest.mark.parametrize('value', [0, 6, 1.5, '2', True])
def test_settings_keep_small_collection_boundary(tmp_path, value):
    store = TaskSettingsStore(tmp_path / 'audit.sqlite3')
    with pytest.raises(ValidationError):
        store.save({'max_notes': value}, 0)
    assert store.get()['revision'] == 0
