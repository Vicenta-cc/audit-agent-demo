"""Isolated fixture SQLite snapshots; explicit experiment/temp paths only.

This stores static plan content, not execution progress or confirmation authority.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
import re
import sqlite3
import tempfile

SCHEMA_VERSION = 1
EXPERIMENT_ROOT = Path(__file__).resolve().parent


def _path(path):
    raw = Path(path)
    if not raw.is_absolute():
        raise ValueError("explicit absolute experiment/temp database path required")
    resolved = raw.resolve()
    allowed = (EXPERIMENT_ROOT, Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve())
    if not any(resolved.is_relative_to(root) and resolved != root for root in allowed):
        raise ValueError("database must be under experiment or temporary directory")
    if resolved.suffix not in {".sqlite", ".sqlite3", ".db"}:
        raise ValueError("explicit fixture database extension required")
    return resolved


def _digest(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid expected digest")
    return value


@contextmanager
def _connect(path, *, create):
    path = _path(path)
    exists = path.exists()
    if not exists and not create:
        raise ValueError("missing fixture database")
    if not exists:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path.as_uri() + ("?mode=rw" if exists else "?mode=rwc"), uri=True)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if not exists:
            conn.executescript("""
                CREATE TABLE geo_fixture_blobs (digest TEXT PRIMARY KEY, content BLOB NOT NULL);
                CREATE TABLE geo_fixture_plans (plan_digest TEXT PRIMARY KEY, blob_digest TEXT NOT NULL);
                PRAGMA user_version=1;
            """)
        elif version != SCHEMA_VERSION:
            raise ValueError("unknown fixture storage schema")
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if names != {"geo_fixture_blobs", "geo_fixture_plans"}:
            raise ValueError("unknown fixture storage schema")
        with conn:
            yield conn
    finally:
        conn.close()


def save_blob(path, content: bytes):
    if type(content) is not bytes:
        raise ValueError("immutable bytes required")
    digest = hashlib.sha256(content).hexdigest()
    with _connect(path, create=True) as conn:
        row = conn.execute("SELECT content FROM geo_fixture_blobs WHERE digest=?", (digest,)).fetchone()
        if row is not None and bytes(row[0]) != content:
            raise ValueError("immutable blob digest mismatch")
        conn.execute("INSERT OR IGNORE INTO geo_fixture_blobs VALUES (?,?)", (digest, content))
    return digest


def load_blob(path, *, expected_digest):
    digest = _digest(expected_digest)
    with _connect(path, create=False) as conn:
        row = conn.execute("SELECT content FROM geo_fixture_blobs WHERE digest=?", (digest,)).fetchone()
    if row is None:
        raise ValueError("missing immutable content copy")
    content = bytes(row[0])
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("blob digest mismatch")
    return content


def save_plan(path, plan):
    from backend.investigation_creation.scope_search_plan import serialize_scope_search_plan

    raw = serialize_scope_search_plan(plan).encode("utf-8")
    blob_digest = save_blob(path, raw)
    digest = _digest(plan.plan_digest)
    with _connect(path, create=False) as conn:
        row = conn.execute("SELECT blob_digest FROM geo_fixture_plans WHERE plan_digest=?", (digest,)).fetchone()
        if row is not None and row[0] != blob_digest:
            raise ValueError("immutable plan content mismatch")
        conn.execute("INSERT OR IGNORE INTO geo_fixture_plans VALUES (?,?)", (digest, blob_digest))
    return digest


def load_plan(path, *, expected_identity, expected_digest):
    from backend.investigation_creation.scope_search_plan import parse_scope_search_plan

    digest = _digest(expected_digest)
    with _connect(path, create=False) as conn:
        row = conn.execute("SELECT blob_digest FROM geo_fixture_plans WHERE plan_digest=?", (digest,)).fetchone()
    if row is None:
        raise ValueError("missing frozen plan")
    raw = load_blob(path, expected_digest=row[0]).decode("utf-8")
    return parse_scope_search_plan(raw, expected_identity=expected_identity, expected_digest=digest)
