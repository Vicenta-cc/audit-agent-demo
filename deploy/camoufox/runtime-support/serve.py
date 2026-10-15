"""Manifest-pinned backend entrypoint; no inherited legacy runtime overlays."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control import check_artifacts, environment, read_json, validate


def configure():
    manifest_path = os.environ.get("CAMOUFOX_RELEASE_MANIFEST")
    if not manifest_path:
        raise RuntimeError("CAMOUFOX_RELEASE_MANIFEST required")
    config = validate(read_json(manifest_path))
    check_artifacts(config)
    os.environ.clear()
    os.environ.update(environment(config))
    os.environ["CAMOUFOX_RELEASE_MANIFEST"] = manifest_path
    app_path = config["artifacts"]["app"]["path"]
    os.chdir(app_path)
    sys.path.insert(0, app_path)
    return config


if __name__ == "__main__":
    config = configure()
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=config["ports"]["host"],
        port=config["ports"]["backend"],
    )
