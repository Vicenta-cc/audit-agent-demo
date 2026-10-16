"""App-owner process boundary for offline, nonroot profile initialization."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
from contextlib import suppress
from pathlib import Path

from .config import settings
from .crawler_browser_migration import MigrationError, _safe_path, profile_key


def initialize_restricted_profile(account_id, *, root, generation, timeout=120):
    from .crawler_child_runtime import child_environment, child_process_options

    root = _safe_path(root)
    if root != settings.crawler_camoufox_state_root / "profiles":
        raise MigrationError("PROFILE_INITIALIZER_ROOT_CONFLICT")
    if not account_id or not generation:
        raise MigrationError("PROFILE_INITIALIZER_ID_REQUIRED")
    options = child_process_options()
    uid = options.get("user", os.geteuid())
    gid = options.get("group", os.getegid())
    # Root App must use a separately provisioned state directory; it must not
    # create/chown another account's state to make the subprocess succeed.
    env = child_environment()
    for parent in (root.parent, root):
        if parent.is_symlink() or not parent.is_dir() or parent.stat().st_uid != uid:
            raise MigrationError("PROFILE_INITIALIZER_STATE_NOT_PROVISIONED")
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "crawler_profile_initialize.py"
    )
    command = [
        str(settings.crawler_login_python),
        str(script),
        "--account-id",
        account_id,
        "--generation",
        generation,
        "--profile-root",
        str(root),
        "--crawler-root",
        str(settings.media_crawler_dir),
        "--version",
        settings.crawler_camoufox_browser_version,
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        cwd=str(settings.media_crawler_dir),
        close_fds=True,
        start_new_session=True,
        **options,
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=10)
        raise MigrationError("PROFILE_INITIALIZER_TIMEOUT") from exc
    if process.returncode != 0 or len(stdout) > 16384:
        raise MigrationError("PROFILE_INITIALIZER_FAILED")
    try:
        result = json.loads(stdout)
    except (ValueError, UnicodeError) as exc:
        raise MigrationError("PROFILE_INITIALIZER_INVALID_RESULT") from exc
    expected = {
        "directory": str(root / profile_key(account_id)),
        "engine": "camoufox",
        "version": settings.crawler_camoufox_browser_version,
        "generation": generation,
        "account_key": profile_key(account_id),
    }
    if (
        not isinstance(result, dict)
        or set(result) != {*expected, "identity_hash", "os"}
        or any(result.get(k) != v for k, v in expected.items())
    ):
        raise MigrationError("PROFILE_INITIALIZER_RESULT_CONFLICT")
    directory = _safe_path(root, profile_key(account_id))
    for path in (
        directory,
        directory / "profile.json",
        directory / "launch-identity.json",
    ):
        if (
            path.is_symlink()
            or not path.exists()
            or path.stat().st_uid != uid
            or path.stat().st_gid != gid
        ):
            raise MigrationError("PROFILE_INITIALIZER_OWNERSHIP_CONFLICT")
    identity = json.loads((directory / "launch-identity.json").read_text())
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest = json.loads((directory / "profile.json").read_text())
    if digest != result["identity_hash"] or any(
        manifest.get(k) != result[k]
        for k in (
            "engine",
            "version",
            "os",
            "generation",
            "account_key",
            "identity_hash",
        )
    ):
        raise MigrationError("PROFILE_INITIALIZER_IDENTITY_CONFLICT")
    return result
