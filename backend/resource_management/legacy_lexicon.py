"""Versioned adapter for the existing complete-lexicon editor."""
from copy import deepcopy
from uuid import uuid4
from .contracts import LexiconContent, LexiconEntry, ResourceError
from .lexicon_versions import content as read_content
from .service import ResourceManagementService


def save_editor(store, category_id, title, risk_label, entries, expected_version):
    with store._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        current = read_content(conn, category_id) if category_id else {'deleted': True}
        if category_id and current.get('deleted'):
            raise ResourceError('词库已删除。', code='RESOURCE_NOT_FOUND')
        if category_id and expected_version is None:
            raise ResourceError('请刷新词库后再保存。', code='RESOURCE_VERSION_REQUIRED')
        previous = current.get('entries', [])
        previous_by_id = {e['id']:e for e in previous}
        output = []
        for entry in entries:
            entry_id = entry.get('id', '')
            base = previous_by_id.get(entry_id)
            if base is None:
                matches = [e for e in previous if e['term'] == entry.get('main_term') and e['kind'] != 'variant']
                base = matches[0] if len(matches) == 1 else None
            row = deepcopy(base) if base else LexiconEntry(term=entry['main_term']).model_dump(mode='json')
            row.update(term=entry['main_term'].strip(), enabled=bool(entry.get('enabled',True)), kind='tag' if entry.get('query_type')=='tag' else 'main')
            if (row['kind']=='tag') != (row['match_type'] in ('tag','平台标签')):
                row['match_type'] = '平台标签' if row['kind']=='tag' else '黑话词'
            row['parent_id'] = ''
            output.append(row)
            for term in entry.get('variants', []):
                if row['kind'] != 'main':
                    raise ResourceError('标签不能拥有搜索词变体。')
                old = [e for e in previous if e['kind']=='variant' and e['parent_id']==row['id'] and e['term']==term]
                output.extend(deepcopy(old) if old else [LexiconEntry(term=term,kind='variant',parent_id=row['id']).model_dump(mode='json')])
        body = LexiconContent(title=title, risk_label=risk_label or current.get('risk_label',''), entries=output)
        identifier = category_id or 'custom_' + uuid4().hex[:16]
        ResourceManagementService._save_lexicon(conn, identifier, body, {'version':expected_version} if category_id else None)
    return store.get_category(identifier)
