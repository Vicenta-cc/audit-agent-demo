"""Session/principal-bound references to exact temporary lexicon versions."""
import json
from uuid import uuid4

from .contracts import LexiconContent, ResourceError
from .lexicon_versions import canonical, digest


def initialize(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS lexicon_edit_refs (
        ref TEXT PRIMARY KEY, principal_id TEXT NOT NULL, session_id TEXT NOT NULL,
        edit_id TEXT NOT NULL, version INTEGER NOT NULL, content_hash TEXT NOT NULL,
        source_ids_json TEXT NOT NULL,
        UNIQUE(principal_id, session_id, edit_id, version, content_hash))''')


def issue(conn, edit, *, session_id, principal, source):
    key = (principal.id, session_id, edit['edit_id'], edit['version'], edit['content_hash'])
    row = conn.execute('SELECT ref FROM lexicon_edit_refs WHERE principal_id=? AND '
                       'session_id=? AND edit_id=? AND version=? AND content_hash=?', key).fetchone()
    if row:
        return row['ref']
    ref = 'resource-ref:' + uuid4().hex
    conn.execute('INSERT INTO lexicon_edit_refs VALUES (?,?,?,?,?,?,?)',
                 (ref, *key, canonical([source['id']] if source else [])))
    return ref


def resolve(conn, ref, *, session_id, principal):
    row = conn.execute('SELECT * FROM lexicon_edit_refs WHERE ref=?', (ref,)).fetchone()
    if row is None:
        return None  # It may be a formal-resource reference.
    if row['principal_id'] != principal.id or row['session_id'] != session_id:
        raise ResourceError('资源引用不属于当前用户或会话。', code='RESOURCE_REF_INVALID',
                            details={'mutation_applied': False, 'retryable': False})
    current = conn.execute('SELECT e.*, o.principal_id AS owner FROM lexicon_edits e '
                           'JOIN resource_edit_origins o ON o.edit_id=e.id '
                           'AND o.session_id=e.session_id WHERE e.id=? AND e.session_id=?',
                           (row['edit_id'], session_id)).fetchone()
    if (current is None or current['owner'] != principal.id
            or current['version'] != row['version']
            or digest(json.loads(current['content_json'])) != row['content_hash']):
        raise ResourceError('临时词库已修改或失效；请读取并核对最新版本后重新采用。',
                            code='RESOURCE_REF_STALE',
                            details={'mutation_applied': False, 'retryable': False})
    body = LexiconContent.model_validate_json(current['content_json'])
    return {'strategy': 'temporary_terms', 'terms': body.search_terms(),
            'source_lexicon_ids': json.loads(row['source_ids_json']), 'source_edit_ref': row['ref'],
            'lexicon_content': body.model_dump(mode='json')}
