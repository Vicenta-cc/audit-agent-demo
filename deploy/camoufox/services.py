"""Render manifest-pinned systemd units for review; never install or control services."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from control import InvalidRelease, check_artifacts, digest, read_json, require, validate

ROLES = {"api": ("serve.py", "2G", 10),
         "worker": ("worker.py", "4G", 310),
         "frontend": ("frontend.py", "256M", 10)}


def path_value(value):
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts
            and not any(ord(c) < 32 or ord(c) == 127 for c in str(value)),
            "service_absolute_clean_path_required")
    # A parent symlink could retarget a pinned path after review.
    require(all(not p.is_symlink() for p in (path, *path.parents)),
            "service_path_symlink")
    return path


def quoted(value, *, command=False):
    """Quote one systemd token; do not involve a shell or expand specifiers."""
    require(not any(ord(c) < 32 or ord(c) == 127 for c in str(value)),
            "service_control_character")
    value = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if command:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def working_directory(value):
    # Unlike ExecStart/Environment tokens, older systemd path directives do not
    # unquote the value. Restrict this renderer to plain deployment paths rather
    # than guessing at version-specific escaping for literal path characters.
    value = str(value)
    require(re.fullmatch(r'/[A-Za-z0-9_./@+-]+', value) is not None,
            "service_working_directory_requires_plain_path")
    return value


def pinned_config(manifest, expected_sha256=None):
    path = path_value(manifest)
    require(path.is_file(), "service_manifest_missing")
    actual = digest(path)
    if expected_sha256 is not None:
        require(actual == expected_sha256, "service_manifest_changed")
    config = validate(read_json(path))
    app = path_value(config["artifacts"]["app"]["path"])
    support = path_value(config["artifacts"]["runtime_support"]["path"])
    require(support == app / "deploy/camoufox/runtime-support",
            "service_runtime_support_source_mismatch")
    python = Path(config["runtime"]["app_python"])
    # venv/bin/python is conventionally a symlink. Preserve its venv path in
    # ExecStart, while refusing a retargetable directory component.
    path_value(python.parent)
    require(python.is_file() and os.access(python, os.X_OK), "service_python_missing")
    for entry in ("serve.py", "worker.py", "frontend.py"):
        require((support / entry).is_file(), "service_entry_missing")
    require((app / "deploy/camoufox/services.py").is_file(), "service_verifier_missing")
    check_artifacts(config)
    # Detect manifest replacement while validating the complete asset set.
    require(digest(path) == actual, "service_manifest_changed")
    return config, actual


def render(manifest, destination, prefix):
    require(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", prefix) is not None,
            "service_prefix_invalid")
    config, manifest_sha = pinned_config(manifest)
    destination = path_value(destination)
    require(destination.parent.is_dir(), "service_output_parent_missing")
    app = Path(config["artifacts"]["app"]["path"])
    support = Path(config["artifacts"]["runtime_support"]["path"])
    python = config["runtime"]["app_python"]
    verifier = app / "deploy/camoufox/services.py"
    protected = [Path(item["path"]) for item in config["artifacts"].values()]
    protected += [Path(config["runtime"]["state_root"]),
                  Path("/etc/systemd/system"), Path("/run/systemd/system")]
    require(not any(destination == root or root in destination.parents
                    for root in protected), "service_output_protected_path")
    units = {}
    for role, (entry, memory, restart_seconds) in ROLES.items():
        after = "network-online.target"
        if role != "api":
            after += f" {prefix}-api.service"
        if role == "frontend":
            after += f" {prefix}-worker.service"
        command = " ".join(quoted(x, command=True) for x in (python, "-B"))
        unit = f"""# Rendered review artifact; installation and live-state checks are separate.
# Manifest SHA256: {manifest_sha}
# App commit: {config['sources']['app_commit']}
[Unit]
Description=Content audit {config['mode']} {role}
Wants={after}
After={after}
StartLimitIntervalSec=1800
StartLimitBurst=3

[Service]
Type=exec
User=root
Group=root
UMask=0077
LimitNOFILE=8192
KillMode=control-group
TimeoutStopSec=45
Restart=on-failure
RestartSec={restart_seconds}
MemoryMax={memory}
CPUQuota=200%
TasksMax=512
WorkingDirectory={working_directory(app)}
Environment={quoted('CAMOUFOX_RELEASE_MANIFEST=' + str(manifest))}
Environment=GIT_OPTIONAL_LOCKS=0
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONNOUSERSITE=1
Environment=PYTHON_DOTENV_DISABLED=1
Environment=LANG=C.UTF-8
ExecStartPre={command} {quoted(verifier, command=True)} verify {quoted(manifest, command=True)} --sha256 {manifest_sha}
ExecStart={command} {quoted(support / entry, command=True)}

[Install]
WantedBy=multi-user.target
"""
        units[f"{prefix}-{role}.service"] = unit
    # Exclusive mkdir prevents overwriting an existing draft or live unit directory.
    destination.mkdir(mode=0o700)
    receipt = {"schema_version": 1, "mode": config["mode"],
               "manifest": str(manifest), "manifest_sha256": manifest_sha,
               "sources": config["sources"], "units": {},
               "installed": False, "linux_verified": False}
    for name, content in units.items():
        path = destination / name
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            stream.write(content)
        receipt["units"][name] = hashlib.sha256(content.encode()).hexdigest()
    with os.fdopen(os.open(destination / "receipt.json",
                           os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    render_parser = sub.add_parser("render")
    render_parser.add_argument("manifest")
    render_parser.add_argument("destination")
    render_parser.add_argument("--prefix", required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("manifest")
    verify_parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    try:
        if args.action == "render":
            print(json.dumps(render(args.manifest, args.destination, args.prefix)))
        else:
            pinned_config(args.manifest, args.sha256)
    except (InvalidRelease, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
