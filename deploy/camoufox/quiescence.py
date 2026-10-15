"""Local Linux quiescence evidence. Does not stop services or accept Boolean proofs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from control import InvalidRelease, atomic_json, exact, read_json, require, validate


def manifest_hash(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def observe(config):
    require(sys.platform == "linux" and os.geteuid() == 0, "local_linux_admin_required")
    units = []
    for unit in config["quiescence_scope"]["units"]:
        output = subprocess.check_output(
            [
                "systemctl",
                "show",
                unit,
                "--property=LoadState,ActiveState,SubState,MainPID,ControlGroup,ExecMainStartTimestampMonotonic,FragmentPath",
            ],
            text=True,
            timeout=10,
        )
        values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        require(
            values.get("LoadState") == "loaded"
            and values.get("ActiveState") == "inactive"
            and values.get("SubState") == "dead"
            and values.get("MainPID") == "0",
            "formal_unit_not_stopped",
        )
        fragment = Path(values.get("FragmentPath", ""))
        require(
            fragment.is_absolute() and fragment.is_file(),
            "formal_unit_definition_missing",
        )
        cgroup = values.get("ControlGroup", "")
        if cgroup:
            base = Path("/sys/fs/cgroup") / cgroup.lstrip("/")
            require(Path("/sys/fs/cgroup") in base.resolve().parents, "invalid_cgroup")
            for pidfile in base.rglob("cgroup.procs") if base.exists() else []:
                require(not pidfile.read_text().strip(), "formal_cgroup_not_empty")
        units.append(
            {
                "unit": unit,
                "active_state": values["ActiveState"],
                "main_pid": 0,
                "fragment_sha256": hashlib.sha256(fragment.read_bytes()).hexdigest(),
                "last_start_monotonic": values.get(
                    "ExecMainStartTimestampMonotonic", ""
                ),
            }
        )
    matches = []
    roots = config["quiescence_scope"]["process_roots"]
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            cmd = (
                (proc / "cmdline")
                .read_bytes()
                .replace(b"\x00", b" ")
                .decode(errors="replace")
            )
            exe = os.readlink(proc / "exe")
            cwd = os.readlink(proc / "cwd")
        except FileNotFoundError:
            continue
        except PermissionError:
            raise InvalidRelease("proc_scan_permission_denied") from None
        if any(
            root in cmd
            or exe == root
            or exe.startswith(root + "/")
            or cwd == root
            or cwd.startswith(root + "/")
            for root in roots
        ):
            matches.append(int(proc.name))
    require(not matches, "managed_processes_still_running")
    return {
        "units": units,
        "process_roots": roots,
        "remaining_pids": matches,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    }


def capture(config, destination):
    observed = observe(config)
    receipt = {
        "schema_version": 1,
        "release_id": config["release_id"],
        "manifest_sha256": manifest_hash(config),
        "observed_at": time.time(),
        "passed": True,
        "observation": observed,
        "old_runtime_fence_supported": False,
        "requires_formal_units_stopped": True,
    }
    atomic_json(destination, receipt)
    return receipt


def verify_receipt(config, receipt_path, max_age=300):
    receipt = read_json(receipt_path)
    exact(
        receipt,
        {
            "schema_version",
            "release_id",
            "manifest_sha256",
            "observed_at",
            "passed",
            "observation",
            "old_runtime_fence_supported",
            "requires_formal_units_stopped",
        },
        "quiescence_receipt_schema",
    )
    require(
        receipt["schema_version"] == 1
        and receipt["passed"] is True
        and receipt["release_id"] == config["release_id"]
        and receipt["manifest_sha256"] == manifest_hash(config),
        "quiescence_receipt_identity",
    )
    age = time.time() - receipt["observed_at"]
    require(0 <= age <= max_age, "quiescence_receipt_expired")
    require(
        receipt["old_runtime_fence_supported"] is False
        and receipt["requires_formal_units_stopped"] is True,
        "old_runtime_fence_not_proven",
    )
    require(receipt["observation"] == observe(config), "quiescence_live_state_changed")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["capture", "verify"])
    parser.add_argument("manifest")
    parser.add_argument("receipt")
    args = parser.parse_args()
    try:
        config = validate(read_json(args.manifest))
        result = (
            capture(config, args.receipt)
            if args.action == "capture"
            else verify_receipt(config, args.receipt)
        )
        print(json.dumps({"passed": True, "release_id": result["release_id"]}))
    except (InvalidRelease, OSError, ValueError, subprocess.SubprocessError):
        print('{"passed":false,"error":"quiescence_not_proven"}', file=sys.stderr)
        raise SystemExit(2)
