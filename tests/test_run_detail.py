from __future__ import annotations

import json
from pathlib import Path
import pytest

from backend.audit_agent.crawler_adapter import CrawlOutput, MediaCrawlerAdapter, CrawlerCollectionIncompleteError
from backend.audit_agent.ingestion import IngestionStore


def test_analyzed_content_keys_only_returns_completed(tmp_path: Path):
    store = IngestionStore(tmp_path / "a.sqlite3")
    batch = tmp_path / "b.json"
    batch.write_text(json.dumps({"task_id": "t", "platform": "dy", "keyword": "x",
                                 "items": [{"aweme_id": "p1"}, {"aweme_id": "p2"}], "comments": []}), encoding="utf-8")
    store.ingest_batch(batch, tmp_path / "raw")
    store.mark_content_status("dy", "p1", "completed", task_id="t")
    assert store.analyzed_content_keys("dy", ["p1", "p2", "p9"]) == {"p1"}


def test_run_detail_builds_single_id_command_and_tags_source_keyword(tmp_path: Path, monkeypatch):
    adapter = MediaCrawlerAdapter()
    captured = {}

    def fake_run_command(*, command, save_root, platform, max_notes, content_callback=None, **kwargs):
        captured["command"] = command
        contents = [{"aweme_id": "777", "desc": "x"}, {"aweme_id": "888", "desc": "other"}]
        if content_callback:
            content_callback(contents, [])
        return CrawlOutput(platform=platform, contents=contents, comments=[], output_dir=save_root)

    monkeypatch.setattr(adapter, "_run_command", fake_run_command)
    seen = []
    output = adapter.run_detail("dy", "777", source_keyword="上分", max_comments=300, max_concurrency=1,
                                max_items_per_minute=5, get_sub_comment=False, save_root=tmp_path,
                                content_callback=lambda contents, comments: seen.extend(contents))
    cmd = captured["command"]
    assert cmd[cmd.index("--type") + 1] == "detail"
    assert cmd[cmd.index("--specified_id") + 1] == "777"
    assert cmd[cmd.index("--max_comments_count_singlenotes") + 1] == "300"
    assert cmd[cmd.index("--get_media") + 1] == "true"
    assert [c["aweme_id"] for c in output.contents] == ["777"] and output.contents[0]["source_keyword"] == "上分"
    assert [c["aweme_id"] for c in seen] == ["777"] and seen[0]["source_keyword"] == "上分"


def _detail(adapter, root, **kwargs):
    return adapter.run_detail("dy", "777", source_keyword="上分", max_comments=300,
                              max_concurrency=1, max_items_per_minute=5,
                              get_sub_comment=False, save_root=root, **kwargs)


@pytest.mark.parametrize("count", [0, 2, 160, 300])
def test_detail_delivers_only_after_comments_complete(tmp_path, monkeypatch, count):
    adapter = MediaCrawlerAdapter()
    delivered, progress = [], []
    comments = [{"aweme_id": "777", "comment_id": str(i), "content": "评论"} for i in range(count)]

    def collect(**kwargs):
        # Model real detail mode: post exists while comments are still pending.
        assert kwargs["content_callback"] is None
        assert kwargs["progress_callback"] is None
        assert kwargs["save_root"] != tmp_path
        assert not delivered and not progress
        # Include duplicate/foreign rows as shared-output defense.
        return CrawlOutput("dy", [{"aweme_id": "777"}, {"aweme_id": "777", "desc": "final"}],
                           comments + comments[:1] + [{"aweme_id": "888", "comment_id": "foreign"}], kwargs["save_root"])

    monkeypatch.setattr(adapter, "_run_command", collect)
    result = _detail(adapter, tmp_path, stream_items=True,
                     content_callback=lambda c, m: delivered.append((c, m)),
                     progress_callback=lambda *x: progress.append(x))
    assert len(delivered) == 1
    assert delivered[0] == (result.contents, comments)
    assert progress == [(1, 1)]
    assert result.contents[0]["desc"] == "final"
    # Reopening after restart must preserve the same complete payload.
    for reader in (adapter.load_latest_output, adapter._load_platform_output):
        reopened = reader(tmp_path, "dy")
        assert reopened.contents == result.contents
        assert reopened.comments == comments


@pytest.mark.parametrize("failure", ["error", "empty", "stopped"])
def test_detail_partial_attempt_never_reaches_ingestion(tmp_path, monkeypatch, failure):
    adapter = MediaCrawlerAdapter()
    delivered = []
    stop = {"value": False}

    def collect(**kwargs):
        directory = kwargs["save_root"] / "douyin" / "jsonl"
        directory.mkdir(parents=True)
        (directory / "detail_contents_partial.jsonl").write_text('{"aweme_id":"777"}\n')
        assert kwargs["content_callback"] is None
        if failure == "error":
            raise CrawlerCollectionIncompleteError("comment fetch failed")
        if failure == "stopped":
            stop["value"] = True
            assert kwargs["stop_checker"]()
            stop["value"] = False  # Stop must remain latched even if control changes.
        return CrawlOutput("dy", [] if failure == "empty" else [{"aweme_id": "777"}], [], kwargs["save_root"])

    monkeypatch.setattr(adapter, "_run_command", collect)
    kwargs = dict(content_callback=lambda *x: delivered.append(x), stop_checker=lambda: stop["value"])
    if failure == "stopped":
        assert _detail(adapter, tmp_path, **kwargs).contents == []
    else:
        with pytest.raises(CrawlerCollectionIncompleteError):
            _detail(adapter, tmp_path, **kwargs)
    assert delivered == []
    assert adapter._load_platform_output(tmp_path, "dy").contents == []
    assert list((tmp_path / "detail_attempts").rglob("*.jsonl"))  # diagnostics retained


def test_detail_retry_uses_fresh_attempt_and_replaces_old_comments(tmp_path, monkeypatch):
    adapter = MediaCrawlerAdapter()
    attempts = []

    def collect(**kwargs):
        root = kwargs["save_root"]
        assert not (root / "douyin").exists()
        attempts.append(root)
        media = root / "douyin" / "images" / "777"
        media.mkdir(parents=True)
        (media / "image.jpg").write_bytes(b"image")
        return CrawlOutput("dy", [{"aweme_id": "777"}],
                           [{"aweme_id": "777", "comment_id": str(len(attempts))}], root)

    monkeypatch.setattr(adapter, "_run_command", collect)
    _detail(adapter, tmp_path)
    result = _detail(adapter, tmp_path)
    assert attempts[0] != attempts[1]
    assert adapter._load_platform_output(tmp_path, "dy").comments == result.comments
    assert (tmp_path / "douyin" / "images" / "777" / "image.jpg").read_bytes() == b"image"


def test_complete_snapshot_survives_delivery_failure(tmp_path, monkeypatch):
    adapter = MediaCrawlerAdapter()
    monkeypatch.setattr(adapter, "_run_command", lambda **kw: CrawlOutput(
        "dy", [{"aweme_id": "777"}], [{"aweme_id": "777", "comment_id": "c"}], kw["save_root"]))

    def fail_delivery(*args):
        raise RuntimeError("ingestion unavailable")

    with pytest.raises(CrawlerCollectionIncompleteError, match="交付入库失败"):
        _detail(adapter, tmp_path, content_callback=fail_delivery)
    assert adapter._load_platform_output(tmp_path, "dy").comments == [{"aweme_id": "777", "comment_id": "c"}]
