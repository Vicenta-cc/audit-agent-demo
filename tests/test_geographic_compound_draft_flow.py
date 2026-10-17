"""Offline compound-region draft flows against the packaged national catalog."""
from uuid import uuid4

import pytest

from backend.investigation_creation.contracts import ConfirmAndQueueCommand
from backend.investigation_creation.errors import (
    ConfigurationValidationError,
    PrincipalAccessDeniedError,
)
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.store import InvestigationCreationStore
from test_geographic_draft_configuration import change, flow, no_effects, stored
from test_investigation_creation_conversation import creation_stack
from test_resource_lifecycle import lexicon
from test_ruleset_proposal_presentation import content
from test_unified_draft_configuration import P, call, prepare


def create_compound(stack, body, target):
    first, context, manager, _, _, args = prepare(stack, body)
    message = '调查' + target
    args.update(scope_decision='apply', relevance_scope_change=change(target, message))
    result, _ = flow(stack, first['session_id'], args, message)
    return first, context, manager, result['data']['draft']['id'], result


@pytest.mark.parametrize('target,canonical,code,province', [
    ('新疆克拉玛依市', '克拉玛依市', '650200', '新疆维吾尔自治区'),
    ('浙江杭州', '杭州市', '330100', '浙江省'),
])
def test_compound_create_preview_preserve_and_reopen(creation_stack, content, target, canonical, code, province):
    stack = creation_stack
    first, context, manager, draft_id, result = create_compound(stack, content, target)
    original = stored(stack, draft_id)
    scope = original.configuration.relevance_scope
    state = original.configuration.relevance_scope_state
    assert state.status == 'resolved'
    assert state.target_code == code
    assert scope.target.name == canonical
    assert scope.province.name == province
    assert result['data']['confirmation_preview']['relevance_scope']['target']['name'] == canonical
    assert not any(blocker['code'] == 'RELEVANCE_SCOPE_PENDING'
        for blocker in result['data']['confirmation_preview']['blockers'])

    replacement = manager.create_lexicon({**lexicon(), 'title': '更新检索词库'}, **context)
    updated, _ = flow(stack, first['session_id'], {
        'operation': 'update', 'draft_id': draft_id, 'expected_revision': 1,
        'title': '更新标题和词库', 'lexicon_ref': replacement['resource_ref'],
    }, '更新标题和词库，地域不变')
    after = stored(stack, draft_id)
    assert after.current_revision == 2
    assert after.title == '更新标题和词库'
    assert after.configuration.relevance_scope == scope
    assert after.configuration.relevance_scope_state == state
    assert updated['data']['confirmation_preview']['relevance_scope']['target']['name'] == canonical

    reopened = InvestigationCreationStore(stack['creation_store'].db_path)
    assert reopened.get_draft(draft_id, principal=P.id).configuration == after.configuration
    public = stack['client'].get('/api/investigation-drafts/' + draft_id)
    assert public.status_code == 200
    public_state = public.json()['configuration']['relevance_scope_state']
    assert public_state['status'] == 'resolved'
    assert not {'principal', 'session_id', 'source_quote', 'catalog_version'} & public_state.keys()

    # Resolving geography cannot bypass the existing execution-readiness gate.
    with pytest.raises(ConfigurationValidationError) as error:
        stack['app_service'].confirm_and_queue(ConfirmAndQueueCommand(
            draft_id=draft_id, expected_revision=2, confirmed=True, idempotency_key=uuid4().hex), principal=P)
    assert error.value.code == 'RELEVANCE_SCOPE_EXECUTION_UNAVAILABLE'
    no_effects(stack)


@pytest.mark.parametrize('target', ['吉林', '浙江克拉玛依市', '新疆石河子市', '浙江杭州和宁波'])
def test_uncertain_compound_update_keeps_scope_and_blocks_confirmation(creation_stack, content, target):
    stack = creation_stack
    first, _, _, draft_id, _ = create_compound(stack, content, '新疆克拉玛依市')
    previous_scope = stored(stack, draft_id).configuration.relevance_scope
    message = '改查' + target
    result, _ = flow(stack, first['session_id'], {
        'operation': 'update', 'draft_id': draft_id, 'expected_revision': 1,
        'relevance_scope_change': change(target, message),
    }, message)
    after = stored(stack, draft_id)
    assert after.configuration.relevance_scope_state.status == 'pending'
    assert after.configuration.relevance_scope == previous_scope
    assert not result['data']['confirmation_preview']['can_confirm']
    assert 'RELEVANCE_SCOPE_PENDING' in {
        blocker['code'] for blocker in result['data']['confirmation_preview']['blockers']}
    with pytest.raises(ConfigurationValidationError) as error:
        stack['app_service'].confirm_and_queue(ConfirmAndQueueCommand(
            draft_id=draft_id, expected_revision=2, confirmed=True, idempotency_key=uuid4().hex), principal=P)
    assert error.value.code == 'RELEVANCE_SCOPE_PENDING'

    cleared, _ = flow(stack, first['session_id'], {
        'operation': 'update', 'draft_id': draft_id, 'expected_revision': 2,
        'relevance_scope_change': {'action': 'clear', 'source_quote': '不限制地域'},
    }, '不限制地域')
    assert stored(stack, draft_id).configuration.relevance_scope is None
    assert stored(stack, draft_id).configuration.relevance_scope_state.status == 'cleared'
    assert not any(blocker['code'].startswith('RELEVANCE_SCOPE')
        for blocker in cleared['data']['confirmation_preview']['blockers'])
    no_effects(stack)


@pytest.mark.parametrize('bad_field', ['source', 'revision'])
def test_compound_updates_preserve_provenance_revision_and_ownership(creation_stack, content, bad_field):
    stack = creation_stack
    first, _, _, draft_id, _ = create_compound(stack, content, '新疆克拉玛依市')
    before = stored(stack, draft_id)
    args = {'operation': 'update', 'draft_id': draft_id, 'expected_revision': 1,
        'title': '不得部分更新', 'relevance_scope_change': change('浙江杭州', '改查浙江杭州')}
    if bad_field == 'source':
        args['relevance_scope_change']['source_quote'] = '从未说过的浙江杭州'
    else:
        args['expected_revision'] = 99
    failure, _, _ = call(stack, first['session_id'], args, message='改查浙江杭州')
    assert failure['status'] == 'error'
    assert stored(stack, draft_id) == before
    with pytest.raises(PrincipalAccessDeniedError):
        stack['app_service'].get_draft(draft_id, principal=Principal('other-owner'))
    no_effects(stack)
