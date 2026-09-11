"""Run an isolated C backend; never touch the collection/demo environments."""
import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8151)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = json.loads(args.config.read_text())
    from dotenv import load_dotenv
    load_dotenv(config["secrets_env"], override=True)
    data = args.runtime.resolve() / "app-data"
    os.environ.update(XHS_AUDIT_DATA_DIR=str(data), XHS_AUDIT_OUTPUTS_DIR=str(data / "outputs"), HERMES_HOME=str(data / "hermes"), HERMES_CREATION_FAKE_RUNTIME="false", HISTORICAL_REPORT_C_MANIFEST=str(args.runtime.resolve() / "workspace-manifest.json"), HISTORICAL_REPORT_WORKSPACE_IDS="historical-report-a,historical-report-b,historical-report-c")
    archives = Path(config["production_data"]).parent.parent / "xhs-audit-agent-hermes-m2-2-valid-heldout/artifacts"
    os.environ["HISTORICAL_REPORT_A_DB"] = str(archives / "report_r31_account_overview_zero_standalone_recovery_20260825_a1386c5/report-generation.sqlite3")
    os.environ["HISTORICAL_REPORT_B_DB"] = str(archives / "report_r31_target_role_correction_20260825_ad68dec/report-generation.sqlite3")
    os.environ.setdefault("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    os.environ["PATH"] = str(Path(config["node"]).parent) + os.pathsep + os.environ["PATH"]
    sys.path.insert(0, str(root))
    os.chdir(root)
    import uvicorn
    from backend.main import app
    @app.get("/api/report-c-runtime")
    def identity():
        return {"code_path": str(root), "data_path": str(data), "report_scope": ["historical-report-a", "historical-report-b", "historical-report-c"], "real_model": True, "collection_environment": "separate"}
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
