"""Resource ownership is checked at persistence boundaries, never inferred from a handle."""
import json

from .contracts import ResourceError


def require_lexicon(conn, resource_id, principal, *, write=False):
    row = conn.execute('SELECT owner_id FROM lexicon_categories WHERE id=?', (resource_id,)).fetchone()
    owner = row['owner_id'] if row else ''
    if not owner or owner not in {principal.id, 'system'}:
        raise ResourceError('词库不存在或无权访问。', code='RESOURCE_NOT_FOUND')
    if write and owner != principal.id:
        raise ResourceError('系统词库只读，请另存为自己的词库。', code='RESOURCE_FORBIDDEN')
    return owner


def recover_lexicon_owners(conn, system_ids):
    """One-way, conservative migration. Ambiguous/unattributed resources stay quarantined.

    Save receipts identify writers; conflicting writers cannot establish ownership.
    Handles/read history are deliberately not ownership evidence.
    """
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    claims = {}
    for table in ('resource_save_receipts', 'resource_library_writes'):
        if table not in tables:
            continue
        for row in conn.execute(f'SELECT principal_id,result_json FROM {table}'):
            try:
                value = json.loads(row['result_json'])
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and value.get('kind') == 'lexicon':
                resource_id = value.get('resource_id') or value.get('id')
                if resource_id and row['principal_id']:
                    claims.setdefault(resource_id, set()).add(row['principal_id'])
    for row in conn.execute("SELECT id FROM lexicon_categories WHERE owner_id='' ").fetchall():
        resource_id = row['id']
        owners = claims.get(resource_id, set())
        owner = next(iter(owners)) if len(owners) == 1 else 'system' if not owners and resource_id in system_ids else ''
        if owner:
            conn.execute("UPDATE lexicon_categories SET owner_id=? WHERE id=? AND owner_id=''", (owner, resource_id))
