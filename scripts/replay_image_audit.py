"""Replay saved images against a frozen job snapshot; no crawl/job/report updates.

Run with the application's Python and audit-provider environment (not the resource
authoring provider). Raw responses stay in the explicitly supplied output folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--source-output", type=Path, required=True)
    parser.add_argument("--source-job", required=True)
    parser.add_argument("--note-ids", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if not args.source_job or Path(args.source_job).name != args.source_job:
        parser.error("source-job must be a single path component")
    if any(not note.isdigit() for note in args.note_ids):
        parser.error("note IDs must be numeric")
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        parser.error("output directory must be empty")

    from backend.audit_agent.config import settings
    from backend.audit_agent import pipeline as module
    from backend.audit_agent.models import AuditSubject
    from backend.audit_agent.qwen_client import QwenClient

    QwenClient.validate_authoritative_vision_configuration(
        required_models={"QWEN_IMAGE_AUDIT_MODEL": settings.qwen_image_audit_model}
    )
    with sqlite3.connect(f"file:{args.database.resolve()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM jobs WHERE id=?", (args.source_job,)).fetchone()
        if row is None:
            raise RuntimeError("Source job missing")
        source = dict(row)
        revision = db.execute(
            "SELECT * FROM task_audit_config_revisions WHERE id=? AND job_id=?",
            (source["current_audit_config_revision_id"], args.source_job),
        ).fetchone()
    if revision is None:
        raise RuntimeError("Frozen audit revision missing")
    rule_snapshot = json.loads(revision["rule_snapshot_json"])
    profile = json.loads(revision["prompt_profile_snapshot_json"])
    if rule_snapshot != json.loads(source["rule_snapshot"]):
        raise RuntimeError("Job and revision rule snapshots differ")
    repo = Path(module.__file__).resolve().parents[2]
    manifest = {
        "source_job": args.source_job,
        "audit_config_revision_id": revision["id"],
        "rule_snapshot": rule_snapshot,
        "prompt_profile_snapshot": profile,
        "python": sys.executable,
        "pipeline_file": module.__file__,
        "pipeline_sha256": digest(Path(module.__file__)),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "model": settings.qwen_image_audit_model,
        "provider_host": urlsplit(settings.remote_inference_base_url if settings.use_remote_vlm else settings.dashscope_base_url).hostname,
        "max_images_per_note": settings.max_images_per_note,
        "scope": "image audit only; no OCR, comments, fusion, report or job updates",
        "notes": args.note_ids,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    settings.outputs_dir = args.output

    def replay(note):
        p = module.AuditPipeline.__new__(module.AuditPipeline)
        p.job_id = "image-replay"
        p.authoritative_m3 = True
        p.audit_config_revision_id = revision["id"]
        p.rule_snapshot = rule_snapshot
        p.qwen = QwenClient()
        p._set_prompt_context(module.AuditPipeline._prompt_category_from_source(source), profile)
        subject = AuditSubject("dy", note, "", "", "", {}, [], [], [])
        images = sorted((args.source_output / "crawler" / "douyin" / "images" / note).glob("*"))
        images = [image for image in images if image.is_file()][:settings.max_images_per_note]
        if not images:
            raise RuntimeError(f"Saved images missing for {note}")
        result = {"note_id": note, "images": []}
        for index, source_image in enumerate(images):
            item = {"evidence_id": f"image:{index}", "source_image": str(source_image), "sha256": digest(source_image)}
            image = p._stage_local_image(source_image, args.output / "staged" / note, index)
            try:
                audit, exemptions = p._run_image_audit(subject, image, evidence_id=item["evidence_id"])
                item.update(status="completed", audit=audit, matched_exemption_ids=exemptions)
            except Exception as exc:
                item.update(status="failed", error_type=type(exc).__name__, error=str(exc), diagnostic_paths=getattr(exc, "diagnostic_paths", []))
            result["images"].append(item)
            print(json.dumps({"note_id": note, "image": index, "status": item["status"], "error_type": item.get("error_type")}), flush=True)
        (args.output / f"result-{note}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    with patch.object(module.job_store, "log"), ThreadPoolExecutor(max_workers=max(1, min(args.workers, 2))) as pool:
        results = list(pool.map(replay, args.note_ids))
    failures = [json.loads(path.read_text()) for path in args.output.glob("image-replay/assets/*/image_failures/*.json")]
    summary = {
        "notes": len(results),
        "images": sum(len(r["images"]) for r in results),
        "completed_images": sum(i["status"] == "completed" for r in results for i in r["images"]),
        "failed_attempts": len(failures),
        "failures": [{key: f.get(key) for key in ("note_id", "evidence_id", "attempt", "phase", "error", "error_type", "will_retry")} for f in failures],
    }
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
