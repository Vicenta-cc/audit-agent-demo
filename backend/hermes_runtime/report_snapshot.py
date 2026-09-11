"""Stable SQLite images for report tools while the worker writes live data."""
from __future__ import annotations

from contextlib import closing
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import tempfile


def published_report_snapshot(
    source: Path, *, ledger_path: Path, session_id: str, identities: tuple
) -> tuple[Path, str]:
    """Pin a session's authorized report set to one WAL-aware SQLite backup.

    The digest is saved separately and verified on reuse. Report repositories
    still validate the file digest, content hashes and frozen snapshot hashes.
    """
    key = sha256(json.dumps([str(source), session_id, identities],
                            ensure_ascii=False).encode()).hexdigest()
    directory = ledger_path.parent / "report-snapshots"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = directory / f"{key}.json"
    with (directory / f"{key}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if manifest.exists():
            record = json.loads(manifest.read_text())
            digest = record["sha256"]
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise RuntimeError("invalid published report snapshot digest")
            snapshot = directory / f"{key}-{digest}.sqlite3"
            if sha256(snapshot.read_bytes()).hexdigest() != digest:
                raise RuntimeError("published report snapshot SHA-256 mismatch")
            return snapshot, digest
        fd, temporary = tempfile.mkstemp(dir=directory, suffix=".sqlite3")
        os.close(fd)
        temporary_path = Path(temporary)
        try:
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as live:
                with closing(sqlite3.connect(temporary)) as frozen:
                    live.backup(frozen)
            digest = sha256(temporary_path.read_bytes()).hexdigest()
            snapshot = directory / f"{key}-{digest}.sqlite3"
            temporary_path.chmod(0o400)
            os.replace(temporary_path, snapshot)
            # Publish the manifest only after its immutable database is complete.
            fd, pending = tempfile.mkstemp(dir=directory, suffix=".json")
            try:
                with os.fdopen(fd, "w") as output:
                    json.dump({"sha256": digest}, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(pending, manifest)
            finally:
                Path(pending).unlink(missing_ok=True)
            return snapshot, digest
        finally:
            temporary_path.unlink(missing_ok=True)
