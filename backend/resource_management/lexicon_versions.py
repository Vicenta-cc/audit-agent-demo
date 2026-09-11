"""Snapshots accompany legacy and new lexicon writes in their own transaction."""
from __future__ import annotations
import hashlib
import json
import sqlite3
from datetime import datetime, timezone


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def initialize(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS lexicon_content_versions (category_id TEXT NOT NULL, version INTEGER NOT NULL, content_hash TEXT NOT NULL, content_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(category_id,version))')
    for operation in ('UPDATE', 'DELETE'):
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS lexicon_versions_no_{operation.lower()} BEFORE {operation} ON lexicon_content_versions BEGIN SELECT RAISE(ABORT, 'lexicon revisions are immutable'); END")
    cols = {r[1] for r in conn.execute('PRAGMA table_info(lexicon_keywords)')}
    for name in ('entry_id', 'parent_entry_id', 'entry_kind'):
        if name not in cols:
            conn.execute(f"ALTER TABLE lexicon_keywords ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")


def content(conn, category_id):
    row = conn.execute('SELECT * FROM lexicon_categories WHERE id=?', (category_id,)).fetchone()
    if row is None:
        return {'deleted': True}
    entries = []
    for r in conn.execute('SELECT * FROM lexicon_keywords WHERE category_id=? ORDER BY id', (category_id,)):
        entries.append(dict(id=r['entry_id'], term=r['keyword'], kind=r['entry_kind'], parent_id=r['parent_entry_id'], enabled=bool(r['enabled']), platform=r['platform'], match_type=r['match_type'], risk_level=r['risk_level'], note=r['note'] or ''))
    return {'title': row['title'], 'risk_label': row['risk_label'], 'description': row['description'], 'entries': entries}


def synchronize(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lexicon_content_versions'").fetchone():
        return
    # Only uninitialized rows use text-based legacy provenance. Once assigned, IDs
    # are stable even after renaming; orphan legacy variants never become main terms.
    rows = conn.execute('SELECT * FROM lexicon_keywords ORDER BY id').fetchall()
    for r in rows:
        if not r['entry_id']:
            conn.execute('UPDATE lexicon_keywords SET entry_id=? WHERE id=?', ('entry:' + str(r['id']), r['id']))
    for r in conn.execute("SELECT * FROM lexicon_keywords WHERE entry_kind='' ORDER BY id").fetchall():
        try:
            metadata = json.loads(r['note'] or '{}')
        except (ValueError, TypeError):
            metadata = {}
        parent_name = metadata.get('variant_of', '') if isinstance(metadata, dict) else ''
        parent = conn.execute('SELECT entry_id FROM lexicon_keywords WHERE category_id=? AND keyword=? AND id!=? ORDER BY id LIMIT 1', (r['category_id'], parent_name, r['id'])).fetchone() if parent_name else None
        kind = 'variant' if parent_name else ('tag' if r['match_type'].lower() in ('tag', '平台标签') else 'main')
        conn.execute('UPDATE lexicon_keywords SET entry_kind=?,parent_entry_id=? WHERE id=?', (kind, parent[0] if parent else '', r['id']))
    ids = {r[0] for r in conn.execute('SELECT id FROM lexicon_categories')} | {r[0] for r in conn.execute('SELECT DISTINCT category_id FROM lexicon_content_versions')}
    for category_id in ids:
        value = content(conn, category_id)
        hashed = digest(value)
        last = conn.execute('SELECT version,content_hash FROM lexicon_content_versions WHERE category_id=? ORDER BY version DESC LIMIT 1', (category_id,)).fetchone()
        if last is None or last['content_hash'] != hashed:
            conn.execute('INSERT INTO lexicon_content_versions VALUES (?,?,?,?,?)', (category_id, last['version'] + 1 if last else 1, hashed, canonical(value), datetime.now(timezone.utc).isoformat()))


class VersionedLexiconConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is None and self.total_changes:
                synchronize(self)
        except Exception:
            self.rollback()
            raise
        return super().__exit__(exc_type, exc, traceback)
