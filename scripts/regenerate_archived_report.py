"""Run the standard R3.1 graph on a hash-verified existing report snapshot."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--managed-runtime", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--reuse-content", action="store_true", help="No model calls; preserve archived prose")
    args = parser.parse_args()
    from dotenv import load_dotenv
    if not args.reuse_content and not args.verify_only:
        load_dotenv(args.managed_runtime / "secrets.env", override=True)
    os.environ.update(
        XHS_AUDIT_DATA_DIR=str(args.runtime),
        XHS_AUDIT_OUTPUTS_DIR=str(args.managed_runtime / "data/outputs"),
        REPORT_MAX_TOKENS="16000", REPORT_REQUEST_TIMEOUT="240",
    )
    from backend.reporting.archive_source import ArchivedReportSource
    from backend.reporting.account_overview import ReportAccountOverviewProjector
    from backend.reporting.store import ReportStore
    from backend.reporting.runtime import R31ReportRuntime
    manifest = json.loads(args.manifest.read_text())
    source = ArchivedReportSource(manifest, outputs_dir=args.managed_runtime / "data/outputs")
    projection = ReportAccountOverviewProjector.from_snapshot(source.snapshot).build(
        source.snapshot, target_account_identity=None, require_target=False,
    )
    args.runtime.mkdir(parents=True, exist_ok=True)
    verification = {"graph": "AccountOverviewReportGraph", "source_manifest": str(args.manifest),
        "source_version": manifest["report_version_id"], "snapshot_hash": source.snapshot.snapshot_hash,
        "statistics": source.snapshot.statistics, "accounts": projection["statistics"]}
    (args.runtime / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2))
    print(json.dumps({"posts": len(source.snapshot.posts), "accounts": projection["statistics"]}), flush=True)
    if args.verify_only:
        return
    database = args.runtime / "report-generation.sqlite3"
    if not database.exists():
        with sqlite3.connect(source.db_path.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(database) as dest:
            src.backup(dest)
    store = ReportStore(database)
    runtime = R31ReportRuntime(store)
    receipt_path = args.runtime / "generation.json"

    def created(generation):
        receipt_path.write_text(json.dumps(generation, indent=2))
        print(json.dumps(generation), flush=True)

    kwargs = dict(source=source, checkpoint_path=args.runtime / "report-checkpoints.sqlite3")
    if args.reuse_content:
        from backend.reporting.reassembly import reassemble_archived_report
        if receipt_path.exists():
            raise ValueError("Use a fresh output directory for archived reassembly")
        result = reassemble_archived_report(store=store, on_created=created, **kwargs)
    elif receipt_path.exists():
        result = runtime.resume(json.loads(receipt_path.read_text())["run_id"], **kwargs)
    else:
        result = runtime.generate(source.snapshot.task_id, on_generation_created=created, **kwargs)
    full = store.get_full_version(result.report_version_id)
    (args.runtime / "report.md").write_text(result.body_markdown)
    (args.runtime / "report.json").write_text(json.dumps(store.get_frontend_report(result.report_version_id), ensure_ascii=False, indent=2))
    receipt = {"report_version_id": result.report_version_id, "status": result.status,
        "content_hash": full["content_hash"], "snapshot_hash": full["source_snapshot"]["snapshot_hash"]}
    (args.runtime / "published.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
