"""Read-only, explicitly registered local re-audit snapshots. Not a report publisher."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse


class PrivateJSONResponse(JSONResponse):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.headers["Cache-Control"] = "private, no-store"


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise HTTPException(409, "Snapshot asset missing or outside registered directory")
    return resolved


class ReauditSnapshot:
    def __init__(self, folder: Path, runtime_root: Path):
        self.folder = folder.resolve()
        if not self.folder.is_relative_to(runtime_root.resolve()):
            raise ValueError("Re-audit snapshot must belong to this runtime")
        self.manifest = _json(_inside(self.folder / "manifest.json", self.folder))
        self.summary = _json(_inside(self.folder / "summary.json", self.folder))
        self.job_id = self.manifest["source_job"]
        if not re.fullmatch(r"m3-[a-zA-Z0-9-]+", self.job_id):
            raise ValueError("Invalid source job")
        self.source_output = runtime_root / "data/outputs" / self.job_id
        self.output = self.folder / "outputs" / self.job_id
        self.database = _inside(self.folder / "audit_index.sqlite3", self.folder)
        self.retested = {entry["note_id"]: entry for entry in self.summary["results"]}
        if self.summary["finished"] != self.summary["total"]:
            raise ValueError("Only completed re-audit snapshots may be displayed")

    def rows(self):
        with sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("""
                SELECT c.note_id,c.title,c.url,c.comments_count,tc.analyze_status,
                       tc.raw_item_path,a.result_json
                FROM task_contents tc JOIN contents c ON c.id=tc.content_id
                LEFT JOIN audit_results a ON a.job_id=tc.task_id AND a.content_id=tc.content_id
                WHERE tc.task_id=? ORDER BY tc.id
            """, (self.job_id,))]

    def data(self, row):
        raw = _json(_inside(Path(row["raw_item_path"]), self.source_output))
        result = json.loads(row["result_json"]) if row["result_json"] and row["analyze_status"] == "completed" else {}
        return raw, result

    def failure(self, note_id):
        for path in sorted((self.output / "post_failures").glob(note_id + "-*.json"), reverse=True):
            data = _json(_inside(path, self.output))
            if data.get("note_id") == note_id:
                break
        else:
            data = {}
        error = self.retested.get(note_id, {}).get("error", "审核未完成")
        if data.get("error_code") == "audit_content_blocked":
            reason = "供应商内容检查拦截（data_inspection_failed）。审核未完成，不代表帖子已被判违规。"
        elif "invalid risk_level" in error:
            reason = '视频审核模型返回空 risk_level；纠正重试后仍未通过输出合同校验。'
        else:
            reason = data.get("reason") or "审核未完成，请查看本地阶段诊断。"
        return {"code": data.get("error_code", "audit_failed"), "stage": data.get("stage", "unknown"), "reason": reason}

    def card(self, row, order):
        raw, result = self.data(row)
        item = raw.get("item") or {}
        note = row["note_id"]
        completed = row["analyze_status"] == "completed"
        return {"note_id": note, "order": order, "title": result.get("content_title") or row["title"] or item.get("desc") or "未命名帖子",
                "author": (result.get("author") or {}).get("nickname") or item.get("nickname") or "未知作者",
                "status": "completed" if completed else "failed", "origin": "reaudit" if note in self.retested else "retained",
                "risk_level": result.get("risk_level") if completed else None,
                "summary": result.get("summary", "") if completed else self.failure(note)["reason"],
                "comments_count": len(raw.get("comments") or []), "url": row["url"] or item.get("aweme_url", "")}

    def media(self, note_id):
        paths = []
        for media_type, suffixes in (("images", {".jpg", ".jpeg", ".png", ".webp"}), ("videos", {".mp4", ".webm", ".mov"})):
            for path in sorted((self.source_output / "crawler/douyin" / media_type / note_id).glob("*")):
                if path.suffix.lower() in suffixes:
                    paths.append((_inside(path, self.source_output), "video" if media_type == "videos" else "image"))
        return paths


def create_local_reaudit_router(config, principal_provider, require_job):
    router = APIRouter(default_response_class=PrivateJSONResponse)
    snapshots = {name: ReauditSnapshot(Path(path), Path(config["runtime_root"])) for name, path in config.get("reaudit_views", {}).items()}

    def access(view_id, principal):
        snapshot = snapshots.get(view_id)
        if snapshot is None:
            raise HTTPException(404, "Re-audit view not found")
        require_job(snapshot.job_id, principal)
        return snapshot

    def row_for(snapshot, note_id):
        for order, row in enumerate(snapshot.rows(), 1):
            if row["note_id"] == note_id:
                return order, row
        raise HTTPException(404, "Post not found in this snapshot")

    @router.get("/api/local-reaudits/{view_id}/view", response_class=HTMLResponse)
    def page(view_id: str, principal=Depends(principal_provider)):
        access(view_id, principal)
        return HTMLResponse((Path(__file__).with_name("local_reaudit.html")).read_text(), headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        })

    @router.get("/api/local-reaudits/{view_id}")
    def listing(view_id: str, principal=Depends(principal_provider)):
        snapshot = access(view_id, principal)
        cards = [snapshot.card(row, order) for order, row in enumerate(snapshot.rows(), 1)]
        return {"title": "抖音 · 色情服务引流", "source_job": snapshot.job_id, "items": cards,
                "counts": {"total": len(cards), "completed": sum(p["status"] == "completed" for p in cards),
                           "failed": sum(p["status"] == "failed" for p in cards), "retained": sum(p["origin"] == "retained" for p in cards)}}

    @router.get("/api/local-reaudits/{view_id}/posts/{note_id}")
    def detail(view_id: str, note_id: str, principal=Depends(principal_provider)):
        snapshot = access(view_id, principal)
        order, row = row_for(snapshot, note_id)
        raw, result = snapshot.data(row)
        completed = row["analyze_status"] == "completed"
        comments = result.get("comments") or raw.get("comments") or []
        comment_fields = ("comment_id", "nickname", "content", "source_text", "translation_zh", "audit_status", "risk_score", "risk_level", "risk_basis", "evidence_quote")
        evidence_fields = ("evidence_id", "modality", "primary_modality", "source", "reason", "risk_score", "evidence_risk_level", "rule_id", "text", "quote", "source_text_dolphin", "translation_zh", "start", "end")
        return {**snapshot.card(row, order), "body": (raw.get("item") or {}).get("desc", ""),
                "comments": [{key: c[key] for key in comment_fields if key in c} for c in comments],
                "comments_audited": completed,
                "evidence": [{key: e[key] for key in evidence_fields if key in e} for e in result.get("evidence_items") or []],
                "failure": None if completed else snapshot.failure(note_id),
                "media": [{"index": i, "type": kind} for i, (_, kind) in enumerate(snapshot.media(note_id))]}

    @router.get("/api/local-reaudits/{view_id}/posts/{note_id}/media/{index}")
    def media(view_id: str, note_id: str, index: int, principal=Depends(principal_provider)):
        snapshot = access(view_id, principal)
        row_for(snapshot, note_id)
        paths = snapshot.media(note_id)
        if index < 0 or index >= len(paths):
            raise HTTPException(404, "Media not found")
        return FileResponse(paths[index][0], headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    return router
