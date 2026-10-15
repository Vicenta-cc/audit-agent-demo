"""P2 resource boundaries against actual isolated P1 resource/editor stores."""
from copy import deepcopy
import socket

import pytest

from backend.investigation_creation.contracts import UpdateDraftCommand
from experiments.geographic_execution_plan.authority import capture_fixture_authority
from test_geographic_execution_plan import build
from test_geographic_draft_configuration import setup_geo, flow, stored, no_effects, change
from test_geographic_scope_selection import select
from test_investigation_creation_conversation import creation_stack, _run_scripted_creation_turn
from test_ruleset_proposal_presentation import content
from test_unified_draft_configuration import P, prepare


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail('network/model entry forbidden')
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    from experiments.geographic_execution_plan import p3_bridge
    for module in (p3_bridge.names, p3_bridge.shared):
        monkeypatch.setattr(module, 'evaluate_relevance', blocked)


def capture(stack, session, draft_id):
    return capture_fixture_authority(stack['app_service'], draft_id, principal=P,
        session_id=session, fixture_id='fixture:p2-resource-boundaries')


def resource_draft(stack, body, *, ui=False):
    first, ctx, manager, rule, word, _, result, _ = setup_geo(stack, body)
    session, draft_id = first['session_id'], result['data']['draft']['id']
    flow(stack, session, {'operation': 'update', 'draft_id': draft_id,
        'expected_revision': 1, 'platform': 'dy'}, '使用抖音')
    if ui:
        select(stack, session, draft_id, revision=2)
    return session, draft_id, ctx, manager, rule, word


def test_old_ui_selection_survives_actual_rule_and_lexicon_replacement(creation_stack, content):
    stack = creation_stack
    session, draft_id, ctx, manager, rule, word = resource_draft(stack, content, ui=True)
    before = capture(stack, session, draft_id)
    body = deepcopy(content); body['name'] = '替换后的规则'
    _run_scripted_creation_turn(stack, session_id=session, content='改规则名称', client_message_id='replace-rule',
        actions=[('update_ruleset_proposal', {'proposal_id': rule['edit_id'], 'expected_version': 1, 'content': body})])
    rule2 = manager.get_edit(rule['edit_id'], **ctx)
    word2 = manager.update(word['edit_id'], 1, [{'operation': 'upsert_entry', 'target_id': 'variant-1',
        'values': {'term': '北京路欠薪'}}], **ctx)
    flow(stack, session, {'operation': 'update', 'draft_id': draft_id, 'expected_revision': 3,
        'ruleset_ref': rule2['resource_ref'], 'lexicon_ref': word2['resource_ref']}, '采用刚展示的新规则和新词库')
    plan = build(capture(stack, session, draft_id))
    assert plan.identity.revision == 4 and plan.inputs.provenance.origin_revision == 3
    assert plan.inputs.configuration.relevance_scope_state == stored(stack, draft_id).configuration.relevance_scope_state
    assert plan.inputs.configuration.relevance_scope_state.source_operation_id == before['configuration']['relevance_scope_state']['source_operation_id']
    assert plan.inputs.configuration.judgement.proposal_version == 2
    assert [q.query for q in plan.execution_queries] == ['北京路欠薪']
    no_effects(stack)


def test_adopted_snapshots_survive_both_live_editor_upgrades(creation_stack, content):
    stack = creation_stack
    session, draft_id, ctx, manager, rule, word = resource_draft(stack, content)
    before = build(capture(stack, session, draft_id))
    new_rule = manager.update(rule['edit_id'], 1, [{'operation': 'set_metadata', 'values': {'name': '当前编辑规则新版'}}], **ctx)
    new_word = manager.update(word['edit_id'], 1, [{'operation': 'upsert_entry', 'target_id': 'variant-1',
        'values': {'term': '当前编辑词库新版'}}], **ctx)
    assert new_rule['version'] == new_word['version'] == 2
    after = build(capture(stack, session, draft_id))
    assert after.plan_digest == before.plan_digest
    assert after.inputs.resources == before.inputs.resources
    assert [q.query for q in after.execution_queries] == ['维族文化']
    assert after.inputs.configuration.judgement.proposal_version == 1
    assert after.inputs.configuration.investigation.recall_plan.source_edit_ref == word['resource_ref']
    no_effects(stack)


def test_legal_drawer_edit_detaches_old_lexicon_reference(creation_stack, content):
    stack = creation_stack
    session, draft_id, ctx, manager, rule, word = resource_draft(stack, content, ui=True)
    original = stored(stack, draft_id)
    config = original.configuration.model_dump(mode='json')
    config['investigation']['recall_plan']['lexicon_content']['title'] = '草案抽屉合法修改'
    updated = stack['app_service'].update_draft(UpdateDraftCommand(draft_id=draft_id,
        expected_revision=3, configuration=config), principal=P, session_id=session)
    assert updated.configuration.investigation.recall_plan.source_edit_ref is None
    plan = build(capture(stack, session, draft_id))
    assert plan.inputs.resources.lexicon_content.title == '草案抽屉合法修改'
    assert [q.query for q in plan.execution_queries] == ['维族文化']
    assert manager.get_edit(word['edit_id'], **ctx)['content']['title'] != '草案抽屉合法修改'
    assert not any(b['kind'] == 'temporary_lexicon' for b in plan.inputs.resources.adoption_bindings)
    no_effects(stack)


@pytest.mark.parametrize('mutation', ['unknown_ref', 'owner', 'session', 'editor_owner', 'editor_session', 'content_hash', 'source_ids'])
def test_actual_lexicon_reference_mismatches_are_rejected(creation_stack, content, mutation):
    stack = creation_stack
    session, draft_id, _, _, _, word = resource_draft(stack, content)
    with stack['creation_store']._connect() as db:
        if mutation == 'unknown_ref':
            db.execute('DELETE FROM lexicon_edit_refs WHERE ref=?', (word['resource_ref'],))
        elif mutation in {'owner', 'session', 'content_hash', 'source_ids'}:
            field, value = {'owner': ('principal_id', 'another-owner'), 'session': ('session_id', 'another-session'),
                'content_hash': ('content_hash', '0' * 64), 'source_ids': ('source_ids_json', '["foreign-source"]')}[mutation]
            db.execute('UPDATE lexicon_edit_refs SET ' + field + '=? WHERE ref=?', (value, word['resource_ref']))
        else:
            field = 'principal_id' if mutation == 'editor_owner' else 'session_id'
            db.execute('UPDATE resource_edit_origins SET ' + field + '=? WHERE edit_id=?', ('foreign', word['edit_id']))
    with pytest.raises(ValueError, match='adoption reference'):
        capture(stack, session, draft_id)
    no_effects(stack)


@pytest.mark.parametrize('mutation', ['approval', 'presentation'])
def test_actual_missing_rule_approval_or_presentation_rejects_capture(creation_stack, content, mutation):
    stack = creation_stack
    session, draft_id, _, _, _, _ = resource_draft(stack, content)
    with stack['creation_store']._connect() as db:
        approval = db.execute('SELECT * FROM ruleset_proposal_approvals WHERE draft_id=?', (draft_id,)).fetchone()
        assistant_id = approval['presentation_assistant_message_id']
        if mutation == 'approval':
            db.execute('DELETE FROM ruleset_proposal_approvals WHERE draft_id=?', (draft_id,))
    if mutation == 'presentation':
        with stack['conversation'].store._connect() as db:
            db.execute("UPDATE investigation_turns SET public_artifact_json='{}' WHERE assistant_message_id=?", (assistant_id,))
    with pytest.raises(ValueError, match='adoption|presentation'):
        capture(stack, session, draft_id)
    no_effects(stack)


def test_exact_queries_with_multiple_resource_sources_execute_once(creation_stack, content):
    stack = creation_stack
    first, ctx, manager, rule, _, args = prepare(stack, content)
    words = ['克拉玛依市包过', '包过', '北京路欠薪', '克拉玛依市 包过']
    lexicon = {'title': '原样搜索fixture', 'entries': [
        {'id': 'exact-' + str(n), 'term': word, 'kind': 'main', 'platform': 'dy', 'match_type': '黑话词'}
        for n, word in enumerate(words)] + [
        {'id': 'duplicate-source', 'term': words[0], 'kind': 'main', 'platform': '全平台', 'match_type': '精确'}]}
    edit = manager.create_lexicon(lexicon, **ctx)
    args.update(platform='dy', lexicon_ref=edit['resource_ref'], scope_decision='apply', relevance_scope_change=change())
    result, _ = flow(stack, first['session_id'], args)
    draft_id = result['data']['draft']['id']
    plan = build(capture(stack, first['session_id'], draft_id))
    assert plan.inputs.resolution.resolved_search_terms == words
    assert [q.query for q in plan.execution_queries] == words
    assert len(plan.execution_queries) == len(words) == 4
    assert all(q.query == q.source_term for q in plan.execution_queries)
    first_sources = plan.execution_queries[0].sources
    assert {source.entry_id for source in first_sources} == {'exact-0', 'duplicate-source'}
    assert len(first_sources) == 2
    flow(stack, first['session_id'], {'operation':'update', 'draft_id':draft_id, 'expected_revision':1,
        'relevance_scope_change':change('深圳市', '地域改查深圳市')}, '地域改查深圳市')
    changed = build(capture(stack, first['session_id'], draft_id))
    assert changed.inputs.configuration.relevance_scope.target.name == '深圳市'
    assert changed.inputs.configuration.judgement == plan.inputs.configuration.judgement
    assert changed.inputs.configuration.investigation.recall_plan == plan.inputs.configuration.investigation.recall_plan
    assert changed.execution_queries == plan.execution_queries
    assert changed.inputs.resolution.execution.keyword == plan.inputs.resolution.execution.keyword
    no_effects(stack)
