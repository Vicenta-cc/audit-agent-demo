"""Run local M3 checks with disposable storage and no production credentials."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def environment(directory: Path) -> dict[str, str]:
    directory = directory.resolve()
    env = dict(os.environ)
    for key in tuple(env):
        if any(word in key for word in ("API_KEY", "AUTH_KEY", "HISTORICAL_REPORT_", "ENCRYPTION_KEY")):
            env.pop(key, None)
    for name, sub in {
        "XHS_AUDIT_DATA_DIR": "data", "XHS_AUDIT_OUTPUTS_DIR": "outputs",
        "HERMES_HOME": "hermes", "CRAWLER_AUTH_KEY_FILE": "data/crawler_auth.key",
        "MEDIACRAWLER_DIR": "disabled-crawler",
    }.items():
        env[name] = str(directory / sub)
    env.update(PYTHONDONTWRITEBYTECODE="1")
    return env


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tests", nargs="*", default=["tests/test_resource_lifecycle.py"])
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="m3-resource-experiment-") as temp:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", *args.tests, "-q"], cwd=ROOT,
            env=environment(Path(temp)),
        )
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
