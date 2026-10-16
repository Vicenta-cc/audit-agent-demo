"""Build isolated venvs from a pre-verified, fully hashed offline wheelhouse."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from control import (
    InvalidRelease,
    atomic_json,
    check_artifacts,
    read_json,
    require,
    validate,
)


def build(config, destination):
    check_artifacts(config)
    destination = Path(destination)
    require(
        destination.is_absolute() and not destination.exists(),
        "new_build_destination_required",
    )
    # Build roots are new; never upgrade an existing production environment.
    destination.mkdir(mode=0o755)
    completed = []
    for role in ("app", "crawler"):
        venv = destination / ("venv-" + role)
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        python = str(venv / "bin/python")
        subprocess.run(
            [
                python,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--require-hashes",
                "--only-binary=:all:",
                "--find-links",
                config["artifacts"]["wheelhouse"]["path"],
                "-r",
                config["artifacts"][role + "_requirements"]["path"],
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={
                "PATH": "/usr/bin:/bin",
                "PIP_CONFIG_FILE": os.devnull,
                "PIP_NO_INDEX": "1",
            },
        )
        subprocess.run(
            [python, "-m", "pip", "check"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if role == "crawler":
            code = "from importlib.metadata import version; assert version('camoufox') == '0.5.8'; assert version('playwright') == '1.62.0'"
            subprocess.run(
                [python, "-c", code],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        completed.append(
            {
                "role": role,
                "python": python,
                "requirements_sha256": config["artifacts"][role + "_requirements"][
                    "sha256"
                ],
            }
        )
    receipt = {
        "schema_version": 1,
        "release_id": config["release_id"],
        "passed": True,
        "venvs": completed,
        "browser_archive_sha256": config["artifacts"]["browser_archive"]["sha256"],
        "production_approved": False,
    }
    atomic_json(destination / "build-receipt.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("destination")
    args = parser.parse_args()
    try:
        print(json.dumps(build(validate(read_json(args.manifest)), args.destination)))
    except (InvalidRelease, OSError, subprocess.SubprocessError):
        print('{"passed":false,"error":"offline_build_failed"}', file=sys.stderr)
        raise SystemExit(2)
