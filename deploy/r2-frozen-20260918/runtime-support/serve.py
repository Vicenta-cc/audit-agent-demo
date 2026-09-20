"""Backend entrypoint for the isolated R2-ABC server runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("DOUYIN_CANDIDATE_CONFIG", ROOT / "runtime.json")).resolve()
CONFIG = json.loads(CONFIG_PATH.read_text())
ROOT = Path(CONFIG["runtime_root"]).resolve()
APP = Path(CONFIG["application_repository"]).resolve()
FORMAL = Path(CONFIG["formal_runtime_root"]).resolve()

environment = json.loads((FORMAL / "environment.json").read_text())
environment.update({
    key: value
    for key, value in dotenv_values(FORMAL / "secrets.env").items()
    if value is not None
})
environment.update({
    "XHS_AUDIT_DATA_DIR": CONFIG["data_dir"],
    "XHS_AUDIT_OUTPUTS_DIR": CONFIG["outputs_dir"],
    "HERMES_HOME": str(ROOT / "hermes"),
    "CRAWLER_AUTH_ENCRYPTION_KEY": "",
    "CRAWLER_AUTH_KEY_FILE": str(Path(CONFIG["data_dir"]) / "crawler_auth.key"),
    "MEDIACRAWLER_DIR": CONFIG["crawler_runtime"],
    "CRAWLER_LOGIN_PYTHON": CONFIG["crawler_python"],
    "CRAWLER_LOGIN_HEADED": "false",
    "CRAWLER_LOGIN_INTERACTIVE": str(CONFIG.get("interactive_login", {}).get("enabled", False)).lower(),
    "CRAWLER_LOGIN_INTERACTIVE_TIMEOUT_SECONDS": str(CONFIG.get("interactive_login", {}).get("timeout_seconds", 600)),
    "CRAWLER_LOGIN_BROWSER_VERSION": CONFIG.get("interactive_login", {}).get("browser_version", ""),
    "CRAWLER_BROWSER_PROFILE_ROOT": CONFIG["browser_profile_root"],
    "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / "playwright-browsers"),
    "REQUEST_SCHEDULER_DB": str(Path(CONFIG["data_dir"]) / "request_scheduler.sqlite3"),
    "AUTO_ANALYZE_CRAWLED_CONTENT": "true",
    "STREAM_CRAWL_ANALYSIS": "true",
    "HERMES_CREATION_FAKE_RUNTIME": "false",
    "CRAWLER_MAX_CONCURRENCY": "1",
    "M3_POSTS_PER_KEYWORD": "5",
    "M3_ANALYZE_LIMIT": "10",
    "EXECJS_RUNTIME": "Node",
    "HERMES_AUTHORIZED_REPORT_VERSION_IDS": "",
    "HISTORICAL_REPORT_WORKSPACE_IDS": "",
    "HISTORICAL_REPORT_A_DB": "",
    "HISTORICAL_REPORT_B_DB": "",
    "HISTORICAL_REPORT_C_MANIFEST": "",
})
for flag in (
    "INVESTIGATION_ACTIVITY_STREAM_ENABLED",
    "INVESTIGATION_ANSWER_STREAM_ENABLED",
    "INVESTIGATION_CREATION_ANSWER_STREAM_ENABLED",
):
    if flag in CONFIG.get("streaming_flags", {}):
        environment[flag] = "true" if CONFIG["streaming_flags"][flag] else "false"
os.environ.update({key: str(value) for key, value in environment.items()})
os.environ["PATH"] = ":".join((
    str(Path(CONFIG["node"]).parent),
    str(Path(CONFIG["application_python"]).parent),
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
    "/usr/local/bin",
))
os.chdir(APP)
sys.path.insert(0, str(APP))

# R2 is deployed with an intentionally empty data profile.  The pinned
# application includes A/B demo specifications by default, so the isolated
# runtime disables their registration without modifying the clean checkout.
import backend.historical_reports as historical_reports  # noqa: E402
import backend.historical_reports.catalog as historical_catalog  # noqa: E402

historical_catalog.HISTORICAL_REPORT_SPECS = ()
historical_reports.HISTORICAL_REPORT_SPECS = ()

from backend.main import app  # noqa: E402
import uvicorn  # noqa: E402


@app.get("/api/acceptance-runtime")
def identity():
    return {
        "environment": CONFIG["environment_id"],
        "app": str(APP),
        "data": CONFIG["data_dir"],
        "port": int(CONFIG["backend_port"]),
        "auto_analysis": True,
    }


if __name__ == "__main__":
    uvicorn.run(app, host=CONFIG["host"], port=int(CONFIG["backend_port"]))
