"""Run a pinned local image offline with bounded resources; never contacts production."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from control import InvalidRelease, atomic_json, digest, require


def rehearse(image, manifest, state, receipt):
    require(
        re.fullmatch(r"sha256:[0-9a-f]{64}", image) is not None,
        "immutable_image_id_required",
    )
    state = Path(state).resolve()
    require(state.is_dir() and not state.is_symlink(), "state_directory_required")
    metadata = json.loads(
        subprocess.check_output(["docker", "image", "inspect", image], text=True)
    )
    require(
        len(metadata) == 1
        and metadata[0]["Architecture"] == "amd64"
        and metadata[0]["Os"] == "linux",
        "linux_amd64_image_required",
    )
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--shm-size=1g",
        "--pids-limit=256",
        "--memory=4g",
        "--cpus=2",
        "--ulimit",
        "nofile=4096:4096",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=268435456",
        "--mount",
        f"type=bind,source={state},target=/state",
        image,
        manifest,
        "--receipt",
        "/state/offline-smoke.json",
    ]
    result = subprocess.run(
        command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120
    )
    require(result.returncode == 0, "docker_smoke_failed")
    proof = json.loads((state / "offline-smoke.json").read_text())
    require(
        proof.get("passed") is True
        and proof.get("uid", 0) > 0
        and proof.get("sandbox_level", 0) > 0,
        "docker_smoke_receipt_invalid",
    )
    atomic_json(
        receipt,
        {
            "schema_version": 1,
            "release_id": proof["release_id"],
            "passed": True,
            "image_id": image,
            "network_mode": "none",
            "read_only": True,
            "shm_bytes": 1024**3,
            "memory_bytes": 4 * 1024**3,
            "pids_limit": 256,
            "browser_receipt_sha256": digest(state / "offline-smoke.json"),
        },
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("image_id")
    p.add_argument("manifest")
    p.add_argument("state")
    p.add_argument("--receipt", required=True)
    a = p.parse_args()
    try:
        rehearse(a.image_id, a.manifest, a.state, a.receipt)
        print('{"passed":true}')
    except (InvalidRelease, OSError, ValueError, subprocess.SubprocessError):
        print('{"passed":false,"error":"docker_rehearsal_failed"}', file=sys.stderr)
        raise SystemExit(2)
