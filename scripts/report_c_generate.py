"""Verify the handoff and generate/resume Report C using the real report graph."""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    args.runtime.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text())
    from dotenv import load_dotenv
    load_dotenv(config["secrets_env"], override=True)
    os.environ.update(XHS_AUDIT_DATA_DIR=str(args.runtime), XHS_AUDIT_OUTPUTS_DIR=str(args.runtime / "outputs"), REPORT_MAX_TOKENS="16000", REPORT_REQUEST_TIMEOUT="240")
    from backend.reporting.aggregate_source import SelectedReportSource
    from backend.reporting.selected_graph import SelectedEvidenceReportGraph
    from backend.reporting.store import ReportStore
    from backend.reporting.runtime import R31ReportRuntime
    source = SelectedReportSource(args.handoff, task_id="report-c-ethnic-discussion-219", title="维汉婚姻与家庭讨论内容分析", outputs_dir=Path(config["production_data"]) / "outputs")
    verification = {**source.verification, "original_workspace_head": "cd62a96e1e04609d346001be613ec1c2544c1e73", "adopted_baseline": "2e849ba883dfffe2a16cb005de4145f34048ea46", "code_path": str(ROOT)}
    (args.runtime / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2))
    print(json.dumps({key: verification[key] for key in ("unique_posts", "verified_payload_files", "canonical_row_extensions")}), flush=True)
    if args.verify_only:
        return
    store = ReportStore(args.runtime / "report-generation.sqlite3")
    runtime = R31ReportRuntime(store, graph_factory=SelectedEvidenceReportGraph)
    receipt_path = args.runtime / "generation.json"
    def created(generation):
        receipt_path.write_text(json.dumps(generation, ensure_ascii=False, indent=2))
        print(json.dumps(generation), flush=True)
    kwargs = dict(source=source, checkpoint_path=args.runtime / "report-checkpoints.sqlite3")
    if receipt_path.exists():
        result = runtime.resume(json.loads(receipt_path.read_text())["run_id"], **kwargs)
    else:
        result = runtime.generate(source.aggregate_task_id, on_generation_created=created, **kwargs)
    full = store.get_full_version(result.report_version_id)
    (args.runtime / "report.md").write_text(result.body_markdown)
    (args.runtime / "report.json").write_text(json.dumps(store.get_frontend_report(result.report_version_id), ensure_ascii=False, indent=2))
    receipt = {"report_version_id": result.report_version_id, "status": result.status, "content_hash": full["content_hash"], "snapshot_hash": full["source_snapshot"]["snapshot_hash"]}
    (args.runtime / "published.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
