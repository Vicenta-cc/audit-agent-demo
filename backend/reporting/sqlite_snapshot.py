"""Read live SQLite databases through SQLite; hash only closed private copies."""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Iterator


SQLITE_BACKUP_DIGEST = "sqlite-backup-sha256/v1"


@contextmanager
def sqlite_snapshot(path: Path, *, timeout: float = 30.0) -> Iterator[tuple[Path, str]]:
    """Yield one consistent, WAL-aware database image and its file digest.

    The private image is removed on success or failure. Never open the live
    database as an ordinary file: closing that descriptor can release locks
    held by other SQLite connections in this process on POSIX.
    """
    deadline = monotonic() + timeout

    def check_deadline(status: int, remaining: int, total: int) -> None:
        if status != sqlite3.SQLITE_DONE and monotonic() >= deadline:
            raise TimeoutError("report SQLite snapshot exceeded its backup deadline")

    with TemporaryDirectory(prefix="audit-report-snapshot-") as directory:
        frozen_path = Path(directory) / "source.sqlite3"
        with closing(sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro", uri=True, timeout=min(timeout, 1.0),
        )) as live:
            with closing(sqlite3.connect(frozen_path)) as frozen:
                live.backup(frozen, pages=256, progress=check_deadline, sleep=0.05)
                # Make the closed image standalone even when the live DB uses WAL.
                frozen.execute("PRAGMA journal_mode=DELETE")
        digest = hashlib.sha256()
        with frozen_path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        yield frozen_path, digest.hexdigest()
