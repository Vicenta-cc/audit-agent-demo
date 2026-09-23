from __future__ import annotations

import json
from pathlib import Path

from backend.audit_agent.crawler_adapter import CrawlOutput, MediaCrawlerAdapter
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
