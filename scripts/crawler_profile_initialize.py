"""Restricted offline Camoufox identity initializer. No browser or database IO."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("account-id", "generation", "profile-root", "crawler-root", "version"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    if sys.platform == "linux" and os.geteuid() == 0:
        raise ValueError("PROFILE_INITIALIZER_ROOT_FORBIDDEN")
    if os.environ.get("CRAWLER_RESTRICTED_CHILD") != "1":
        raise ValueError("PROFILE_INITIALIZER_RESTRICTED_ENV_REQUIRED")
    state = Path(os.environ["CRAWLER_CAMOUFOX_STATE_ROOT"]).resolve()
    root = Path(args.profile_root)
    if not root.is_absolute() or root.is_symlink() or root != state / "profiles":
        raise ValueError("PROFILE_INITIALIZER_ROOT_CONFLICT")
    module_path = Path(args.crawler_root) / "tools" / "camoufox_browser.py"
    if module_path.is_symlink() or not module_path.is_file():
        raise ValueError("PROFILE_INITIALIZER_ADAPTER_MISSING")
    os.environ["MEDIACRAWLER_CAMOUFOX_BROWSER_VERSION"] = args.version
    spec = importlib.util.spec_from_file_location(
        "offline_account_camoufox", module_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.initialize_account_profile(
        args.account_id, root=root, generation=args.generation
    )
    key = hashlib.sha256(("dy:" + args.account_id).encode()).hexdigest()
    if (
        Path(result["directory"]) != root / key
        or result["generation"] != args.generation
        or result["engine"] != "camoufox"
        or result["version"] != args.version
    ):
        raise ValueError("PROFILE_INITIALIZER_RESULT_CONFLICT")
    print(
        json.dumps(
            {
                key: str(value)
                for key, value in result.items()
                if key
                in (
                    "directory",
                    "engine",
                    "version",
                    "os",
                    "account_key",
                    "identity_hash",
                    "generation",
                )
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - Child boundary must redact module errors and environment.
        # Never return module diagnostics/environment/private paths on stdout.
        print("PROFILE_INITIALIZER_FAILED", file=sys.stderr)
        sys.exit(2)
