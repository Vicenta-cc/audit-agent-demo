import json
from pathlib import Path

from backend.audit_agent.ingestion import IngestionStore
from backend.audit_agent.job_store import JobStore


def _batch(path: Path, task_id: str, item: dict) -> None:
    path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "platform": "dy",
                "keyword": "美食",
                "items": [item],
                "comments": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_task_checkpoint_is_persisted_without_changing_start_page(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.create(job_id="j1", platform="dy", start_page=0, max_notes=3)
    store.update("j1", crawl_checkpoint_keyword="美食", crawl_checkpoint_page=2)
    job = store.get("j1")
    assert job["start_page"] == 0
    assert job["crawl_checkpoint_keyword"] == "美食"
    assert job["crawl_checkpoint_page"] == 2


def test_complete_content_is_reused_across_tasks_and_invalid_payload_fails_closed(tmp_path):
    db = tmp_path / "audit.sqlite3"
    ingestion = IngestionStore(db)
    item = {"aweme_id": "aweme-1", "desc": "历史内容"}
    first_batch = tmp_path / "first.json"
    _batch(first_batch, "task-a", item)
    first_refs = ingestion.ingest_batch(first_batch, tmp_path / "raw-a")
    assert len(first_refs) == 1
    original_path = Path(first_refs[0]["raw_item_path"])
    assert ingestion.reusable_content_keys("dy", ["aweme-1"]) == {"aweme-1"}

    second_batch = tmp_path / "second.json"
    _batch(second_batch, "task-b", item)
    second_refs = ingestion.ingest_batch(second_batch, tmp_path / "raw-b")
    assert len(second_refs) == 1  # task B audits the shared payload in its own task
    refs = ingestion.refs_for_task("task-b")
    assert len(refs) == 1
    assert Path(refs[0]["raw_item_path"]) == original_path

    original_path.write_text("{}", encoding="utf-8")
    assert ingestion.reusable_content_keys("dy", ["aweme-1"]) == set()
