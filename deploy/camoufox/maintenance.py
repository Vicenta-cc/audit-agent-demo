"""Atomically enable maintenance; disabling requires an explicit evidence receipt.

This changes a manifest only. The operator must restart the pinned service and verify
that all login/crawl/recovery entrypoints reject work. It never signals processes.
"""

from __future__ import annotations

import argparse
import sys

from control import InvalidRelease, atomic_json, read_json, require, validate


def set_maintenance(path, enabled, receipt_path=None):
    config = validate(read_json(path))
    if not enabled:
        require(receipt_path is not None, "maintenance_release_receipt_required")
        proof = read_json(receipt_path)
        require(
            proof.get("release_id") == config["release_id"]
            and proof.get("migration_committed") is True
            and proof.get("runtime_health_passed") is True
            and proof.get("operator"),
            "maintenance_release_not_verified",
        )
    config["maintenance"] = enabled
    atomic_json(path, config)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest")
    p.add_argument("action", choices=["enable", "disable"])
    p.add_argument("--receipt")
    args = p.parse_args()
    try:
        set_maintenance(args.manifest, args.action == "enable", args.receipt)
        print('{"manifest_updated":true,"restart_and_gate_verification_required":true}')
    except (InvalidRelease, OSError):
        print('{"ok":false,"error":"maintenance_update_rejected"}', file=sys.stderr)
        raise SystemExit(2)
