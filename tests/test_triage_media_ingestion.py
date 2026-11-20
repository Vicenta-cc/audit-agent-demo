"""Prepared ordinary selections retain task-local media and source evidence."""
import json
from pathlib import Path
import time

import pytest

from backend.audit_agent.ingestion import IngestionStore
from backend.audit_agent.scoped_selection import digest


def source(task):
    now = time.time()
    return dict(schema_version=1, observation_id=f"observation-{task}",
                source_task_id=task, source_batch_id=f"batch-{task}", collected_at=now,
                platform="dy", source_account_id=f"account-{task}",
                route_mode="proxy_required", proxy_id="shared-proxy",
                binding_revision=1, config_revision=1,
                observed_exit_ip="203.0.113.9", observed_exit_at=now)


def prepared(task, *, desc="prepared", attempt="detail-one"):
    item = dict(aweme_id="42", desc=desc, collection_route_provenance=source(task))
    item["triage_media_binding"] = dict(job_id=task, platform="dy", content_key="42",
        content_digest=digest(item), source_dir=f"detail_attempts/{attempt}")
    return item


def ingest(store, root, task, item, *, batch="one", comment="current", media_source_task_id=None):
    folder = root / task
    folder.mkdir(exist_ok=True)
    path = folder / f"{batch}.json"
    comments = [dict(aweme_id="42", comment_id=comment, content=comment)]
    path.write_text(json.dumps(dict(task_id=task, platform="dy", keyword=task,
                                   items=[item], comments=comments)), encoding="utf-8")
    options = {"media_source_task_id": media_source_task_id} if media_source_task_id else {}
    return store.ingest_batch(path, folder / "raw", **options)


@pytest.mark.parametrize("old_prepared", [False, True])
def test_prepared_selection_keeps_own_evidence_when_content_already_shared(tmp_path, old_prepared):
    store = IngestionStore(tmp_path / "ingestion.sqlite3")
    old = prepared("old", desc="old") if old_prepared else dict(
        aweme_id="42", desc="old", collection_route_provenance=source("old"))
    [original] = ingest(store, tmp_path, "old", old, comment="old-comment")
    original_path = Path(original["raw_item_path"])
    original_bytes = original_path.read_bytes()
    with store._connect() as conn:
        shared_before = dict(conn.execute("SELECT * FROM contents").fetchone())

    new = prepared("new", desc="new")
    [queued] = ingest(store, tmp_path, "new", new, comment="new-comment")

    assert queued["item"] == new
    assert queued["comments"][0]["comment_id"] == "new-comment"
    assert queued["collection_route_provenance"] == new["collection_route_provenance"]
    assert Path(queued["raw_item_path"]).parent == tmp_path / "new" / "raw"
    assert original_path.read_bytes() == original_bytes
    with store._connect() as conn:
        assert dict(conn.execute("SELECT * FROM contents").fetchone()) == shared_before
    for restored in (store.pending_for_task("new"), store.validated_refs_for_task("new")):
        assert restored[0]["item"] == new
        assert restored[0]["comments"] == queued["comments"]
        assert restored[0]["raw_item_path"] == queued["raw_item_path"]
    assert store.validated_refs_for_task("old")[0]["item"] == old


@pytest.mark.parametrize("status", ["analyzing", "completed", "skipped"])
def test_new_prepared_batch_cannot_replace_already_claimed_task_evidence(tmp_path, status):
    store = IngestionStore(tmp_path / "ingestion.sqlite3")
    old = dict(aweme_id="42", desc="original", collection_route_provenance=source("job"))
    [original] = ingest(store, tmp_path, "job", old, comment="original-comment")
    with store._connect() as conn:
        conn.execute("UPDATE task_contents SET analyze_status=?, result_path='saved-result'", (status,))
        membership = dict(conn.execute("SELECT * FROM task_contents").fetchone())
    original_bytes = Path(original["raw_item_path"]).read_bytes()

    assert ingest(store, tmp_path, "job", prepared("job", desc="replacement"),
                  batch="two", comment="replacement-comment") == []

    with store._connect() as conn:
        assert dict(conn.execute("SELECT * FROM task_contents").fetchone()) == membership
    assert Path(original["raw_item_path"]).read_bytes() == original_bytes
    assert store.validated_refs_for_task("job")[0]["item"] == old


def test_same_binding_cannot_silently_replace_committed_comments(tmp_path):
    store = IngestionStore(tmp_path / "ingestion.sqlite3")
    item = prepared("job")
    [original] = ingest(store, tmp_path, "job", item, comment="original")
    original_bytes = Path(original["raw_item_path"]).read_bytes()
    with pytest.raises(ValueError, match="prepared.*evidence"):
        ingest(store, tmp_path, "job", item, batch="two", comment="changed")
    assert Path(original["raw_item_path"]).read_bytes() == original_bytes
    assert store.pending_for_task("job")[0]["comments"][0]["comment_id"] == "original"


def test_same_prepared_capture_replays_without_rewriting_original_payload(tmp_path):
    store = IngestionStore(tmp_path / "ingestion.sqlite3")
    item = prepared("job")
    [original] = ingest(store, tmp_path, "job", item)
    path = Path(original["raw_item_path"])
    original_bytes = path.read_bytes()
    [replayed] = ingest(store, tmp_path, "job", item, batch="two")
    assert path.read_bytes() == original_bytes
    assert replayed["item"] == original["item"]
    assert replayed["comments"] == original["comments"]
    assert replayed["raw_item_path"] == original["raw_item_path"]


def test_failed_prepared_capture_can_receive_new_local_attempt_without_mutating_shared(tmp_path):
    store = IngestionStore(tmp_path / "ingestion.sqlite3")
    [original] = ingest(store, tmp_path, "job", prepared("job"), comment="old")
    path = Path(original["raw_item_path"])
    original_bytes = path.read_bytes()
    with store._connect() as conn:
        conn.execute("UPDATE task_contents SET analyze_status='failed'")
        shared_before = dict(conn.execute("SELECT * FROM contents").fetchone())
    new = prepared("job", desc="new", attempt="detail-two")
    [replaced] = ingest(store, tmp_path, "job", new, batch="two", comment="new")
    assert replaced["item"] == new
    assert replaced["comments"][0]["comment_id"] == "new"
    assert replaced["raw_item_path"] != original["raw_item_path"]
    assert path.read_bytes() == original_bytes
    with store._connect() as conn:
        assert dict(conn.execute("SELECT * FROM contents").fetchone()) == shared_before
    assert store.pending_for_task("job")[0]["item"] == new


@pytest.mark.parametrize("field,value", [
    ("job_id", "foreign"), ("platform", "xhs"), ("content_key", "other"),
    ("content_digest", "0" * 64), ("source_dir", "../../foreign"),
])
def test_invalid_prepared_identity_does_not_publish_task_content(tmp_path, field, value):
    store = IngestionStore(tmp_path / "ingestion.sqlite3")
    item = prepared("job")
    item["triage_media_binding"][field] = value
    with pytest.raises((ValueError, RuntimeError)):
        ingest(store, tmp_path, "job", item)
    assert store.validated_refs_for_task("job") == []
    assert list((tmp_path / "job" / "raw").glob("*.json")) == []


def test_unprepared_content_keeps_existing_shared_reuse_contract(tmp_path):
    store = IngestionStore(tmp_path / "ingestion.sqlite3")
    old = dict(aweme_id="42", desc="old", collection_route_provenance=source("old"))
    [original] = ingest(store, tmp_path, "old", old, comment="old-comment")
    new = dict(aweme_id="42", desc="new", collection_route_provenance=source("new"))
    [reused] = ingest(store, tmp_path, "new", new, comment="new-comment")
    assert reused["item"] == old
    assert reused["comments"] == original["comments"]
    assert reused["raw_item_path"] == original["raw_item_path"]


@pytest.mark.parametrize("legacy_shared_marker", [False, True])
def test_prepared_then_ordinary_reuse_does_not_inherit_foreign_media(tmp_path, legacy_shared_marker):
    store = IngestionStore(tmp_path / "ingestion.sqlite3")
    item = prepared("first")
    [original] = ingest(store, tmp_path, "first", item)
    with store._connect() as conn:
        if legacy_shared_marker:
            conn.execute("UPDATE contents SET raw_item_path=?", (original["raw_item_path"],))
        shared_path = conn.execute("SELECT raw_item_path FROM contents").fetchone()[0]
    shared_bytes = Path(shared_path).read_bytes()
    if not legacy_shared_marker:
        assert shared_path != original["raw_item_path"]
        assert "triage_media_binding" not in json.loads(shared_bytes)["item"]
    ordinary = dict(aweme_id="42", desc="ordinary")
    [queued] = ingest(store, tmp_path, "second", ordinary)
    expected = {k: v for k, v in item.items() if k != "triage_media_binding"}
    for refs in ([queued], store.pending_for_task("second"), store.validated_refs_for_task("second"), store.refs_for_task("second")):
        assert refs[0]["item"] == expected
        assert refs[0]["raw_item_path"] == shared_path
        assert refs[0]["comments"] == original["comments"]
    assert Path(shared_path).read_bytes() == shared_bytes
    assert store.validated_refs_for_task("first")[0]["item"] == item


def test_explicit_media_source_preserves_authorized_binding_after_restore(tmp_path):
    store = IngestionStore(tmp_path / "ingestion.sqlite3")
    item = prepared("source")
    ingest(store, tmp_path, "source", item)
    [queued] = ingest(store, tmp_path, "consumer", item, media_source_task_id="source")
    for refs in ([queued], store.pending_for_task("consumer"), store.validated_refs_for_task("consumer"), store.refs_for_task("consumer")):
        assert refs[0]["item"] == item
        assert refs[0]["raw_item_path"] == queued["raw_item_path"]
    assert json.loads(Path(queued["raw_item_path"]).read_text())["item"] == item


def test_batch_cannot_authorize_foreign_media_source(tmp_path):
    store = IngestionStore(tmp_path / "ingestion.sqlite3")
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps(dict(task_id="consumer", platform="dy",
        media_source_task_id="source", items=[prepared("source")], comments=[])))
    with pytest.raises((ValueError, RuntimeError)):
        store.ingest_batch(batch, tmp_path / "raw")
    assert store.pending_for_task("consumer") == []
