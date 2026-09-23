import hashlib
import json
from pathlib import Path
import sqlite3

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from backend.api.local_reaudit import ReauditSnapshot, create_local_reaudit_router


@pytest.fixture
def setup(tmp_path):
    folder = tmp_path / "recheck"
    folder.mkdir()
    output = tmp_path / "data/outputs/m3-fixture"
    output.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"source_job": "m3-fixture"}))
    (folder / "summary.json").write_text(json.dumps({"finished": 2, "total": 2, "results": [{"note_id": "2", "error": "data_inspection_failed"}, {"note_id": "3"}]}))
    failure = folder / "outputs/m3-fixture/post_failures"
    failure.mkdir(parents=True)
    (failure / "2-1.json").write_text(json.dumps({"note_id": "2", "error_code": "audit_content_blocked", "stage": "comment_audit"}))
    database = folder / "audit_index.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript('''CREATE TABLE task_contents(id INTEGER,task_id TEXT,content_id INTEGER,analyze_status TEXT,raw_item_path TEXT);
            CREATE TABLE contents(id INTEGER,note_id TEXT,title TEXT,url TEXT,comments_count INTEGER);
            CREATE TABLE audit_results(job_id TEXT,content_id INTEGER,result_json TEXT);''')
        for n in range(1, 4):
            raw = output / f"{n}.json"
            raw.write_text(json.dumps({"item": {"desc": "<script>alert(1)</script>", "nickname": "作者"}, "comments": [{"comment_id": "c", "content": "原始评论"}]}))
            db.execute("INSERT INTO contents VALUES(?,?,?,?,?)", (n, str(n), f"帖子{n}", "https://www.douyin.com/video/1", 1))
            db.execute("INSERT INTO task_contents VALUES(?,?,?,?,?)", (n, "m3-fixture", n, "failed" if n == 2 else "completed", str(raw)))
            if n != 2:
                db.execute("INSERT INTO audit_results VALUES(?,?,?)", ("m3-fixture", n, json.dumps({"summary": "总结", "risk_level": "none", "comments": [{"content": "已审核", "audit_status": "completed", "risk_score": 0}]})))
    media = output / "crawler/douyin/images/1"
    media.mkdir(parents=True)
    (media / "image.jpg").write_bytes(b"fixture-image")
    calls = []
    allowed = [True]

    def require_job(job_id, principal):
        calls.append((job_id, principal))
        if not allowed[0]:
            raise HTTPException(404, "Job not found")

    config = {"runtime_root": str(tmp_path), "reaudit_views": {"review": str(folder)}}
    app = FastAPI()
    app.include_router(create_local_reaudit_router(config, lambda: "owner", require_job))
    return TestClient(app), folder, output, calls, allowed, config


def test_readonly_listing_and_separate_outcomes(setup):
    client, folder, _, calls, _, _ = setup
    db = folder / "audit_index.sqlite3"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    data = client.get("/api/local-reaudits/review").json()
    assert data["counts"] == {"total": 3, "completed": 2, "failed": 1, "retained": 1}
    assert data["items"][1]["risk_level"] is None
    assert data["items"][0]["origin"] == "retained"
    assert calls == [("m3-fixture", "owner")]
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_failed_detail_keeps_raw_comments_unjudged(setup):
    client = setup[0]
    data = client.get("/api/local-reaudits/review/posts/2").json()
    assert data["comments_audited"] is False
    assert data["comments"][0] == {"comment_id": "c", "content": "原始评论"}
    assert data["failure"]["code"] == "audit_content_blocked"
    assert data["evidence"] == []
    assert "<script>" in data["body"]  # Stored as data, rendered only using textContent.


@pytest.mark.parametrize("suffix", ["", "/view", "/posts/1", "/posts/1/media/0"])
def test_every_endpoint_enforces_original_job_access(setup, suffix):
    client, _, _, _, allowed, _ = setup
    allowed[0] = False
    assert client.get("/api/local-reaudits/review" + suffix).status_code == 404


def test_media_only_known_post_index_and_no_mutation_routes(setup):
    client = setup[0]
    assert client.get("/api/local-reaudits/review/posts/1/media/0").content == b"fixture-image"
    for path in ["/posts/1/media/-1", "/posts/1/media/99", "/posts/999", "/posts/999/media/0"]:
        assert client.get("/api/local-reaudits/review" + path).status_code == 404
    assert client.get("/api/local-reaudits/unknown").status_code == 404
    assert client.post("/api/local-reaudits/review").status_code == 405


def test_symlink_escape_cannot_serve_arbitrary_file(setup, tmp_path):
    client, _, output, *_ = setup
    secret = tmp_path / "secret.jpg"
    secret.write_text("private")
    image = output / "crawler/douyin/images/1/image.jpg"
    image.unlink()
    image.symlink_to(secret)
    assert client.get("/api/local-reaudits/review/posts/1/media/0").status_code == 409


def test_view_has_safe_rendering_and_no_cache(setup):
    response = setup[0].get("/api/local-reaudits/review/view")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "innerHTML" not in response.text
    assert "textContent" in response.text


def test_incomplete_or_outside_snapshot_rejected(setup, tmp_path):
    _, folder, _, _, _, _ = setup
    with pytest.raises(ValueError, match="belong"):
        ReauditSnapshot(folder, tmp_path / "other-runtime")
    (folder / "summary.json").write_text(json.dumps({"finished": 0, "total": 1, "results": []}))
    with pytest.raises(ValueError, match="completed"):
        ReauditSnapshot(folder, tmp_path)
