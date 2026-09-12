"""Description edits must stay independent of recall terms and legacy revisions."""
import sqlite3

import pytest
from pydantic import ValidationError

from backend.audit_agent.lexicon_store import LexiconStore
from backend.resource_management.contracts import ResourceError
from backend.resource_management.legacy_lexicon import save_editor
from test_resource_lifecycle import CTX, P, lexicon, save, service


def change_description(service, edit, description):
    return service.update(edit['edit_id'], edit['version'], [
        {'operation': 'set_metadata', 'values': {'description': description}}
    ], **CTX)


def editor_entries(content):
    return [dict(id=e['id'], main_term=e['term'], enabled=e['enabled'],
                 query_type='tag' if e['kind'] == 'tag' else 'keyword',
                 variants=[v['term'] for v in content['entries'] if v['parent_id'] == e['id']])
            for e in content['entries'] if e['kind'] != 'variant']


def test_description_only_save_preserves_entries_stats_recall_and_reloads(service):
    body = lexicon()
    for i, entry in enumerate(body['entries']):
        entry['note'] = f'原词条备注{i}'
    formal = save(service, service.create_lexicon(body, **CTX))
    identifier = formal['resource_id']
    before = service.read('lexicon', identifier, principal=P)
    with service.lexicons._connect() as conn:
        conn.execute('UPDATE lexicon_keywords SET hit_count_7d=12 WHERE category_id=?', (identifier,))
        rows_before = [tuple(r) for r in conn.execute('SELECT id,entry_id,hit_count_7d,note FROM lexicon_keywords WHERE category_id=? ORDER BY id', (identifier,))]
    edit = service.open('lexicon', identifier, **CTX)
    changed = change_description(service, edit, '测试用途：维汉婚恋讨论')
    assert changed['content'] == {**before['content'], 'description': '测试用途：维汉婚恋讨论'}
    assert service.read('lexicon', identifier, principal=P) == before
    receipt = save(service, changed, 'description-save', 'update')
    after = service.read('lexicon', identifier, principal=P)
    assert after['version'] == before['version'] + 1
    assert after['content_hash'] != before['content_hash']
    assert after['runtime_content_hash'] == before['runtime_content_hash']
    assert after['search_terms'] == before['search_terms']
    assert after['content'] == changed['content']
    assert service.get_edit(edit['edit_id'], **CTX)['saved']
    assert receipt == save(service, changed, 'description-save', 'update')
    service.lexicons = LexiconStore(service.lexicons.db_path)
    with service.lexicons._connect() as conn:
        assert [tuple(r) for r in conn.execute('SELECT id,entry_id,hit_count_7d,note FROM lexicon_keywords WHERE category_id=? ORDER BY id', (identifier,))] == rows_before
    reopened = service.open('lexicon', identifier, session_id='new-session', principal=P)
    assert reopened['content'] == after['content']
    assert service.lexicons.get_category(identifier)['description'] == '测试用途：维汉婚恋讨论'


def test_temporary_description_copy_and_clear_do_not_change_original(service):
    original = save(service, service.create_lexicon({**lexicon(), 'description': '正式说明'}, **CTX))
    identifier = original['resource_id']
    before = service.read('lexicon', identifier, principal=P)
    edit = service.open('lexicon', identifier, **CTX)
    changed = change_description(service, edit, '这次修改不要保存')
    assert not changed['saved']
    assert service.read('lexicon', identifier, principal=P) == before
    assert service.open('lexicon', identifier, **CTX)['content']['description'] == '正式说明'
    copied = save(service, changed, 'copy-description', 'copy')
    assert copied['resource_id'] != identifier
    assert service.read('lexicon', copied['resource_id'], principal=P)['content']['description'] == '这次修改不要保存'
    cleared = change_description(service, changed, '')
    assert 'description' not in cleared['content']
    save(service, cleared, 'clear-copy', 'update')
    assert service.lexicons.get_category(copied['resource_id'])['description'] == ''
    assert service.read('lexicon', identifier, principal=P) == before


def test_legacy_editor_omission_preserves_description_and_empty_clears(service):
    body = {**lexicon(), 'description': '必须保留的说明'}
    body['entries'][2]['match_type'] = '平台标签'
    formal = save(service, service.create_lexicon(body, **CTX))
    identifier = formal['resource_id']
    before = service.read('lexicon', identifier, principal=P)
    entries = editor_entries(before['content'])
    save_editor(service.lexicons, identifier, before['content']['title'], '', entries, before['version'])
    assert service.read('lexicon', identifier, principal=P) == before
    save_editor(service.lexicons, identifier, before['content']['title'], '', entries, before['version'], description='页面的新说明')
    after = service.read('lexicon', identifier, principal=P)
    assert after['content'] == {**before['content'], 'description': '页面的新说明'}
    save_editor(service.lexicons, identifier, before['content']['title'], '', entries, after['version'], description='')
    assert service.lexicons.get_category(identifier)['description'] == ''


def test_legacy_database_migration_keeps_hashes_versions_and_saved_receipts(service):
    formal = save(service, service.create_lexicon(lexicon(), **CTX))
    identifier = formal['resource_id']
    before = service.read('lexicon', identifier, principal=P)
    edit = service.open('lexicon', identifier, **CTX)
    save(service, edit, 'old-receipt', 'update')
    with sqlite3.connect(service.lexicons.db_path) as conn:
        revisions = conn.execute('SELECT * FROM lexicon_content_versions ORDER BY category_id,version').fetchall()
        # Recreate the old schema without touching historical revision payloads.
        conn.execute('ALTER TABLE lexicon_categories DROP COLUMN description')
    service.lexicons = LexiconStore(service.lexicons.db_path)
    with service.lexicons._connect() as conn:
        assert [tuple(r) for r in conn.execute('SELECT * FROM lexicon_content_versions ORDER BY category_id,version')] == revisions
    assert service.read('lexicon', identifier, principal=P) == before
    assert service.get_edit(edit['edit_id'], **CTX)['saved']
    unchanged = change_description(service, edit, '')
    assert unchanged['version'] == edit['version']
    assert unchanged['saved']
    changed = change_description(service, unchanged, '迁移后可修改')
    save(service, changed, 'after-migration', 'update')
    assert service.read('lexicon', identifier, principal=P)['version'] == before['version'] + 1


def test_description_conflict_does_not_overwrite_newer_formal(service):
    formal = save(service, service.create_lexicon(lexicon(), **CTX))
    drafts = [change_description(service, service.open('lexicon', formal['resource_id'], **CTX), value) for value in ('会话A说明', '会话B说明')]
    save(service, drafts[0], 'a-description', 'update')
    with pytest.raises(ResourceError, match='已经变化'):
        save(service, drafts[1], 'b-description', 'update')
    assert service.read('lexicon', formal['resource_id'], principal=P)['content']['description'] == '会话A说明'
    assert service.get_save('b-description', **CTX)['status'] == 'not_found'


@pytest.mark.parametrize('value', [None, 123, '字' * 2001])
def test_invalid_description_is_atomic(service, value):
    edit = service.create_lexicon(lexicon(), **CTX)
    with pytest.raises(ValidationError):
        change_description(service, edit, value)
    assert service.get_edit(edit['edit_id'], **CTX) == edit


def test_unsupported_metadata_names_report_allowed_fields_without_mutating_notes(service):
    edit = service.create_lexicon(lexicon(), **CTX)
    with pytest.raises(ResourceError) as exc:
        service.update(edit['edit_id'], edit['version'], [{'operation': 'set_metadata', 'values': {'note': '不能写到这里'}}], **CTX)
    assert exc.value.details['allowed_fields'] == ['description', 'risk_label', 'title']
    assert service.get_edit(edit['edit_id'], **CTX) == edit
