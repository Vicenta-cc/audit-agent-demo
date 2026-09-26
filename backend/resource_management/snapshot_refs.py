"""Principal-bound handles for exact formal lexicon snapshots, not bearer grants."""
import json
from uuid import uuid4

from .contracts import ResourceError
from .lexicon_versions import canonical, content, digest
from .ownership import require_lexicon


def initialize(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS lexicon_snapshot_refs (
        ref TEXT PRIMARY KEY, principal_id TEXT NOT NULL, resource_id TEXT NOT NULL,
        version INTEGER NOT NULL, content_hash TEXT NOT NULL,
        runtime_hash TEXT NOT NULL, terms_json TEXT NOT NULL,
        UNIQUE(principal_id, resource_id, version, content_hash, runtime_hash))''')


def issue(conn, store, resource_id, *, principal):
    require_lexicon(conn, resource_id, principal)
    revision = conn.execute('SELECT version,content_hash FROM lexicon_content_versions '
                            'WHERE category_id=? ORDER BY version DESC LIMIT 1', (resource_id,)).fetchone()
    if revision is None or content(conn, resource_id).get('deleted'):
        raise ResourceError('词库不存在。', code='RESOURCE_NOT_FOUND')
    runtime_hash = store.runtime_content_hash(resource_id, connection=conn)
    terms = store.enabled_search_terms(resource_id, connection=conn)
    key = (principal.id, resource_id, revision['version'], revision['content_hash'], runtime_hash)
    row = conn.execute('SELECT ref FROM lexicon_snapshot_refs WHERE principal_id=? AND '
                       'resource_id=? AND version=? AND content_hash=? AND runtime_hash=?', key).fetchone()
    ref = row['ref'] if row else 'resource-ref:' + uuid4().hex
    if row is None:
        conn.execute('INSERT INTO lexicon_snapshot_refs VALUES (?,?,?,?,?,?,?)',
                     (ref, *key, canonical(terms)))
    return ref


def resolve(conn, store, ref, *, principal):
    row = conn.execute('SELECT * FROM lexicon_snapshot_refs WHERE ref=? AND principal_id=?',
                       (ref, principal.id)).fetchone()
    if row is None:
        raise ResourceError('资源引用无效或当前用户无权使用，请重新读取有权访问的资源。',
                            code='RESOURCE_REF_INVALID', details={'mutation_applied': False, 'retryable': False})
    body = content(conn, row['resource_id'])
    revision = conn.execute('SELECT version FROM lexicon_content_versions WHERE category_id=? '
                            'ORDER BY version DESC LIMIT 1', (row['resource_id'],)).fetchone()
    if (body.get('deleted') or revision is None or revision['version'] != row['version']
            or digest(body) != row['content_hash']
            or store.runtime_content_hash(row['resource_id'], connection=conn) != row['runtime_hash']):
        raise ResourceError('所引用的词库版本已变化或删除；请重新读取并核对差异后再决定是否采用。',
                            code='RESOURCE_REF_STALE', details={'mutation_applied': False, 'retryable': False})
    require_lexicon(conn, row['resource_id'], principal)
    return {'strategy': 'existing_lexicon', 'lexicon_id': row['resource_id'],
            'expected_runtime_content_hash': row['runtime_hash'],
            'enabled_main_terms': json.loads(row['terms_json'])}


def tool_view(value):
    """Only formal-resource receipts change shape; stored commands remain legacy."""
    if isinstance(value, list):
        return [tool_view(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: tool_view(item) for key, item in value.items()}
    if value.get('resource_ref'):
        result['recall_plan'] = {'strategy': 'resource_ref', 'resource_ref': value['resource_ref']}
        result.pop('runtime_content_hash', None)
    return result
