"""Full audit of only failed posts in an isolated copy; published source data is unchanged."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", required=True, type=Path)
    parser.add_argument("--source-output", required=True, type=Path)
    parser.add_argument("--source-job", required=True)
    parser.add_argument("--expected-failed", required=True, type=int)
    parser.add_argument("--note-id", help="Re-audit exactly one failed numeric note ID, never the whole batch")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.note_id and not args.note_id.isdigit():
        parser.error("note-id must be numeric")
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        parser.error("Output directory must be empty")
    args.output = args.output.resolve()
    copied_db = args.output / "audit_index.sqlite3"
    with sqlite3.connect(f"file:{args.source_db.resolve()}?mode=ro", uri=True) as source, sqlite3.connect(copied_db) as target:
        source.backup(target)

    # Configure the private database before importing modules with default stores.
    os.environ["XHS_AUDIT_DATA_DIR"] = str(args.output)
    os.environ["XHS_AUDIT_OUTPUTS_DIR"] = str(args.output / "outputs")

    from backend.audit_agent import pipeline as module
    from backend.audit_agent.config import settings
    from backend.audit_agent.job_store import JobStore
    from backend.audit_agent.ingestion import IngestionStore, AuditResultStore
    from backend.audit_agent.qwen_client import QwenClient

    class NonThinkingAuditClient(QwenClient):
        def analyze_image(self, *args, **kwargs):
            kwargs["enable_thinking"] = False
            return super().analyze_image(*args, **kwargs)

        def audit_text(self, *args, **kwargs):
            kwargs["enable_thinking"] = False
            return super().audit_text(*args, **kwargs)

    # These stores and output files are private to this maintenance process.
    # Keep the normal shared capacity locks and real providers from the controller.
    settings.outputs_dir = args.output / "outputs"
    module.job_store = JobStore(copied_db)
    pipeline = module.AuditPipeline(args.source_job)
    pipeline.qwen = NonThinkingAuditClient()
    pipeline.ingestion = IngestionStore(copied_db)
    pipeline.audit_results = AuditResultStore(copied_db)
    source_job = module.job_store.get(args.source_job)
    if not source_job:
        raise RuntimeError("Source job missing")
    refs = pipeline.ingestion.pending_for_task(args.source_job)
    with sqlite3.connect(copied_db) as db:
        statuses = dict(db.execute("SELECT analyze_status,count(*) FROM task_contents WHERE task_id=? GROUP BY analyze_status", (args.source_job,)))
        row = db.execute("SELECT rule_snapshot_json,prompt_profile_snapshot_json FROM task_audit_config_revisions WHERE id=? AND job_id=?",
                         (source_job["current_audit_config_revision_id"], args.source_job)).fetchone()
    if statuses.get("failed") != args.expected_failed or len(refs) != args.expected_failed or statuses.get("queued", 0) or statuses.get("analyzing", 0):
        raise RuntimeError("Source selection differs from expected failed-only scope")
    if row is None or json.loads(row[0]) != source_job["rule_snapshot"] or json.loads(row[1]) != source_job["prompt_profile_snapshot"]:
        raise RuntimeError("Frozen revision and job snapshots differ")
    if args.note_id:
        selected = []
        for ref in refs:
            subjects = pipeline._build_subjects(source_job["platform"], [ref["item"]], ref["comments"], args.source_output, include_media=False)
            if len(subjects) == 1 and subjects[0].note_id == args.note_id:
                selected.append(ref)
        if len(selected) != 1:
            raise RuntimeError("Requested note is not uniquely present in the failed-only selection")
        refs = selected
    pipeline.authoritative_m3 = True
    pipeline.rule_snapshot = source_job["rule_snapshot"]
    pipeline.audit_config_revision_id = source_job["current_audit_config_revision_id"]
    pipeline._set_prompt_context(pipeline._prompt_category_from_source(source_job), source_job["prompt_profile_snapshot"])
    module.job_store.update_control(args.source_job, analysis_stop_requested=False, analysis_paused=False, stop_all_requested=False)
    manifest = {"source_job": args.source_job, "source_database": str(args.source_db), "copied_database": str(copied_db),
                "source_statuses": statuses, "audit_config_revision_id": pipeline.audit_config_revision_id,
                "rule_snapshot": pipeline.rule_snapshot, "prompt_profile_snapshot": source_job["prompt_profile_snapshot"],
                "pipeline_sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
                "thinking": False, "asr_engine": settings.asr_engine, "ffmpeg_path": settings.ffmpeg_path,
                "selected_content_keys": [ref["content_key"] for ref in refs], "started_at": time.time()}
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    results = []
    retained = list(source_job.get("items") or [])
    for ref in refs:
        subjects = pipeline._build_subjects(source_job["platform"], [ref["item"]], ref["comments"], args.source_output, include_media=True)
        if len(subjects) != 1:
            raise RuntimeError("Failed post could not be reconstructed uniquely")
        subject = subjects[0]
        print(json.dumps({"note_id": subject.note_id, "status": "started", "comments": len(subject.comments)}), flush=True)
        entry = {"note_id": subject.note_id, "comments": len(subject.comments)}
        try:
            pipeline._begin_subject_audit()
            result = pipeline._analyze_subject(subject)
            pipeline._assert_authoritative_provider_healthy()
            path = pipeline._write_result_json(subject.note_id, result)
            persisted = pipeline._persist_audit_result(platform=source_job["platform"], content_key=ref["content_key"], result=result, result_path=path, content_id=ref["content_id"])
            pipeline.ingestion.mark_content_status(source_job["platform"], ref["content_key"], "completed", task_id=args.source_job)
            retained.append(persisted)
            entry.update(status="completed", result_path=str(path))
        except Exception as exc:
            pipeline._record_subject_failure(source_job["platform"], ref["content_key"], subject, exc)
            entry.update(status="failed", stage=getattr(pipeline, "_current_audit_stage", "unknown"), error_type=type(exc).__name__, error=str(exc), diagnostic_paths=getattr(exc, "diagnostic_paths", []))
        results.append(entry)
        module.job_store.update(args.source_job, items=retained)
        summary = {"total": len(refs), "finished": len(results), "completed": sum(r["status"] == "completed" for r in results),
                   "failed": sum(r["status"] == "failed" for r in results), "retained_source_successes": len(source_job.get("items") or []), "results": results}
        temporary = args.output / "summary.tmp"
        temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        temporary.replace(args.output / "summary.json")
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    module.job_store.update(args.source_job, status="completed", analysis_status="partial" if summary["failed"] else "completed")
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}), flush=True)


if __name__ == "__main__":
    main()
