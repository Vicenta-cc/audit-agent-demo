"""Create a one-post template preview from a real archived pass audit.

The archive is read-only; all preview state stays in a separate data directory.
This replays an existing audit result and does not claim a new crawl occurred.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--report-version", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    archive = args.archive.expanduser().resolve()
    data_dir = args.data_dir.expanduser().resolve()
    if archive == data_dir / "audit_index.sqlite3":
        parser.error("preview data must be separate from the archive")
    os.environ["XHS_AUDIT_DATA_DIR"] = str(data_dir)
    os.environ["XHS_AUDIT_OUTPUTS_DIR"] = str(data_dir / "outputs")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from backend.audit_agent.audit_policy_store import TaskAuditConfigRevisionStore
    from backend.audit_agent.ingestion import AuditResultStore, IngestionStore
    from backend.audit_agent.job_store import JobStore
    from backend.reporting.integration_source import CanonicalReportSource
    from backend.reporting.runtime import R31ReportRuntime
    from backend.reporting.store import ReportStore

    with sqlite3.connect(f"file:{archive}?mode=ro&immutable=1", uri=True) as connection:
        rows = connection.execute(
            """SELECT p.payload_json, f.payload_json FROM report_snapshot_posts p
               JOIN report_snapshot_findings f ON p.report_version_id = f.report_version_id AND p.post_ref = f.post_ref
               WHERE p.report_version_id = ? ORDER BY p.post_ref""", (args.report_version,)
        ).fetchall()
    chosen = None
    for post_json, finding_json in rows:
        post, finding = json.loads(post_json), json.loads(finding_json)
        result = dict(finding.get("raw_effective_result") or post.get("raw_content_payload") or {})
        if finding.get("decision") != "pass" or finding.get("risk_level") != "none":
            continue
        if result.get("decision") != "pass" or result.get("risk_level") != "none":
            continue
        if any(comment.get("risk_level") in {"low", "medium", "high"} for comment in result.get("comments") or []):
            continue
        if result.get("evidence_items") or result.get("risk_evidence"):
            continue
        chosen = post, result
        break
    if chosen is None:
        parser.error("archive contains no suitable pass/none sample")
    post, result = chosen
    db = data_dir / "audit_index.sqlite3"
    task_id = "pass-preview-" + uuid4().hex[:12]
    JobStore(db).create(job_id=task_id, status="completed", platform=post["platform"], crawl_mode="search",
                        display_name="单条通过帖报告预览（历史审核样本）", max_notes=1, analyze_limit=1, run_crawler=False)
    revision = TaskAuditConfigRevisionStore(db).create(job_id=task_id, audit_config={
        "source": "historical_audit_replay", "source_report_version": args.report_version,
        "source_audit_config_revision": result.get("audit_config_revision_id"),
    })
    key = post["canonical_key"]
    raw_dir = data_dir / "outputs" / task_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    batch = raw_dir / "batch.json"
    # Preserve the archived source text. No audit decision, explanation, or
    # evidence is generated or changed by the preview importer.
    batch.write_text(json.dumps({"task_id": task_id, "platform": post["platform"], "items": [{
        "aweme_id": key, "title": result.get("title") or "", "desc": result.get("desc") or "",
    }]}, ensure_ascii=False))
    ingestion = IngestionStore(db)
    refs = ingestion.ingest_batch(batch, raw_dir / "raw")
    stored = AuditResultStore(db).upsert_result(job_id=task_id, platform=post["platform"], content_key=key,
        content_id=refs[0]["content_id"], result=result, audit_config_revision_id=revision["id"])
    ingestion.mark_content_status(post["platform"], key, "completed", task_id=task_id, audit_result_id=stored["id"])
    generated = R31ReportRuntime(ReportStore(db)).generate(task_id, source=CanonicalReportSource(db, data_dir / "outputs"),
                                                          checkpoint_path=data_dir / "report_checkpoints.sqlite3")
    output = {"task_id": task_id, "report_version_id": generated.report_version_id,
              "source_report_version": args.report_version, "source_post_key": key,
              "preview_kind": "historical_audit_replay"}
    (data_dir / "pass-preview.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
