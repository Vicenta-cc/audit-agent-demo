"""Saving task keywords must not ask the model to serialize their content."""
import json

import pytest

from backend.investigation_creation.contracts import ConfirmAndQueueCommand, UpdateDraftCommand
from backend.investigation_creation.principal import Principal
from backend.resource_management.contracts import LexiconContent
from backend.investigation_creation.tools import (
    HERMES_M3_TOOL_SCHEMAS, HermesToolExecutionIdentity, InvestigationCreationToolService,
)
from test_investigation_draft_configuration_m3 import (
    m3_stack, _create_draft, _principal, _temporary_configuration,
    _structured_lexicon_content,
)
from test_investigation_creation_conversation import (
    creation_stack, _explicit_create_actions, _run_scripted_creation_turn,
    _search_draft_from_options,
)


def make_draft(stack):
    body = _structured_lexicon_content()
    body['entries'][1]['note'] = '含有 "双引号"、反斜线\\和换行\n，原样保留。' * 40
    config = _temporary_configuration(stack, ['非绿地陪', '门槛验牌'])
    config['investigation']['recall_plan']['lexicon_content'] = body
    return _create_draft(stack, config)


def invoke(stack, draft, *, revision=None, principal=None, call='save', operation='save-task-words'):
    return InvestigationCreationToolService(stack['service']).execute_with_identity(
        'save_draft_lexicon',
        {'draft_id': draft.id, 'expected_revision': revision or draft.current_revision,
         'operation_id': operation},
        principal=principal or _principal(stack),
        identity=HermesToolExecutionIdentity('session-save', 'turn-' + call, call),
    )


def test_save_tool_schema_is_reference_only():
    schema = next(x for x in HERMES_M3_TOOL_SCHEMAS if x['name'] == 'save_draft_lexicon')
    assert set(schema['parameters']['properties']) == {'draft_id', 'expected_revision', 'operation_id'}
    assert 'content' not in schema['parameters']['properties']


@pytest.mark.parametrize('state', ['draft', 'queued', 'running', 'failed', 'published'])
def test_save_before_or_after_start_preserves_snapshot_and_replays(m3_stack, state):
    stack = m3_stack
    draft = make_draft(stack)
    run = None
    if state != 'draft':
        run = stack['service'].confirm_and_queue(ConfirmAndQueueCommand(
            draft_id=draft.id, expected_revision=draft.current_revision,
            confirmed=True, idempotency_key='confirm'), principal=_principal(stack))
        if state in {'running', 'failed', 'published'}:
            with stack['store']._connect() as conn:
                conn.execute('UPDATE investigation_runs SET status=? WHERE id=?', (state.upper(), run.id))
        frozen = stack['store'].get_run_for_worker(run.id).model_dump(mode='json')
    before = stack['service'].get_draft(draft.id, principal=_principal(stack)).model_dump(mode='json')
    result = invoke(stack, draft)
    assert result['status'] == 'ok', result
    saved = result['data']
    assert saved['status'] == 'saved'
    assert 'content' not in saved  # No second full-content echo to the model.
    assert saved['recall_plan'] == {'strategy': 'resource_ref', 'resource_ref': saved['resource_ref']}
    formal = stack['service'].resource_management.read('lexicon', saved['resource_id'], principal=_principal(stack))
    assert formal['content'] == draft.configuration.investigation.recall_plan.lexicon_content.storage_dict()
    assert invoke(stack, draft) == result
    assert invoke(stack, draft, call='retry') == result
    another = invoke(stack, draft, call='retry-new', operation='new-recovery-id')
    assert another['data']['resource_id'] == saved['resource_id']
    receipt = stack['service'].resource_management.get_save('save-task-words', session_id='session-save', principal=_principal(stack))
    assert receipt['resource_id'] == saved['resource_id']
    assert stack['service'].get_draft(draft.id, principal=_principal(stack)).model_dump(mode='json') == before
    if run:
        assert stack['store'].get_run_for_worker(run.id).model_dump(mode='json') == frozen
    with stack['lexicons']._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM lexicon_categories WHERE id LIKE 'custom_%'").fetchone()[0] == 1


def test_card_edit_is_saved_and_old_revision_rejected(m3_stack):
    stack = m3_stack
    draft = make_draft(stack)
    config = draft.configuration.model_dump(mode='json')
    plan = config['investigation']['recall_plan']
    entries = plan['lexicon_content']['entries']
    entries[1]['note'] = '用户卡片里修改的 "备注"'
    entries[1]['term'] = '用户修订词'
    entries[1]['risk_level'] = '高'
    entries[2]['enabled'] = False
    plan['terms'] = LexiconContent.model_validate(plan['lexicon_content']).search_terms()
    changed = stack['service'].update_draft(UpdateDraftCommand(
        draft_id=draft.id, expected_revision=1, configuration=config), principal=_principal(stack))
    rejected = invoke(stack, draft, call='stale')
    assert rejected['status'] == 'error', rejected
    result = invoke(stack, changed, call='current')
    assert result['status'] == 'ok', result
    body = stack['service'].resource_management.read('lexicon', result['data']['resource_id'], principal=_principal(stack))['content']
    assert body['entries'][1]['note'] == '用户卡片里修改的 "备注"'
    assert body == changed.configuration.investigation.recall_plan.lexicon_content.storage_dict()
    assert result['data']['search_terms'] == ['用户修订词']


def test_other_owner_cannot_save_but_ordinary_owner_can(m3_stack):
    stack = m3_stack
    draft = make_draft(stack)
    assert invoke(stack, draft, principal=Principal('other'), call='other')['status'] == 'error'
    stack['service'].resource_management.shared_lexicon_writes_require_admin = True
    accepted = invoke(stack, draft, call='ordinary-owner')
    assert accepted['status'] == 'ok', accepted


def test_legacy_flat_terms_are_not_silently_regenerated(m3_stack):
    draft = _create_draft(m3_stack, _temporary_configuration(m3_stack, ['原文词']))
    assert invoke(m3_stack, draft)['status'] == 'error'


def test_prompt_no_longer_requires_draft_content_recopy():
    from backend.investigation_creation.conversation import CREATION_SYSTEM_PROMPT
    from backend.resource_management.tools import RESOURCE_PROMPT, RESOURCE_DESCRIPTIONS
    for prompt in (CREATION_SYSTEM_PROMPT, RESOURCE_PROMPT):
        assert 'save_draft_lexicon' in prompt
        assert 'pass that exact content unchanged to create_lexicon_edit' not in prompt
        assert '逐字段原样传给 create_lexicon_edit' not in prompt
    assert '如保存 Draft 词库' not in RESOURCE_DESCRIPTIONS['create_lexicon_edit']


def test_save_after_start_survives_failed_final_answer_without_recopy(creation_stack):
    stack = creation_stack
    body = _structured_lexicon_content()
    body['entries'][1]['note'] = '保留 "引号" 与换行\n' * 40

    def temporary_draft(results):
        args = _search_draft_from_options(results)
        args['configuration']['investigation']['recall_plan'] = {
            'strategy': 'temporary_terms', 'lexicon_content': body,
            'terms': LexiconContent.model_validate(body).search_terms(),
        }
        return args

    actions = _explicit_create_actions()
    actions[-1] = ('create_investigation_draft', temporary_draft)
    created = _run_scripted_creation_turn(
        stack, content='建立调查草案', actions=actions,
        client_message_id='setup-save-draft',
    )
    draft_id = created['turn'].public_artifact['draft_id']
    principal = Principal('principal-a')
    draft = stack['app_service'].get_draft(draft_id, principal=principal)
    run = stack['app_service'].confirm_and_queue(ConfirmAndQueueCommand(
        draft_id=draft_id, expected_revision=draft.current_revision,
        confirmed=True, idempotency_key='start-save-test'), principal=principal)
    frozen = stack['creation_store'].get_run_for_worker(run.id).model_dump(mode='json')
    saved = _run_scripted_creation_turn(
        stack, session_id=created['session_id'], content='存一下这个关键词',
        client_message_id='save-after-start', actions=[
            ('get_investigation_draft', {'draft_id': draft_id}),
            ('save_draft_lexicon', lambda results: {
                'draft_id': results[-1]['draft']['id'],
                'expected_revision': results[-1]['draft']['current_revision'],
                'operation_id': 'save-after-start',
            }),
        ], completed=False, failed=True, turn_exit_reason='max_iterations_reached',
        final_response='回答中断',
    )
    receipt = saved['agent'].results[-1]
    assert receipt['status'] == 'saved'
    assert len(json.dumps(saved['agent'].calls[-1][1])) < 300
    assert [name for name, _ in saved['agent'].calls] == [
        'get_investigation_draft', 'save_draft_lexicon',
    ]
    formal = stack['app_service'].resource_management.read(
        'lexicon', receipt['resource_id'], principal=principal)
    assert formal['content'] == draft.configuration.investigation.recall_plan.lexicon_content.storage_dict()
    assert stack['creation_store'].get_run_for_worker(run.id).model_dump(mode='json') == frozen
    resumed = _run_scripted_creation_turn(
        stack, session_id=created['session_id'], content='刚刚保存成功了吗？',
        client_message_id='check-save-after-interruption', actions=[],
        final_response='已保存，任务配置未改变。',
    )
    history = json.dumps(resumed['agent'].conversation_histories[0], ensure_ascii=False)
    assert 'save_draft_lexicon' in history
    assert receipt['resource_id'] in history
    assert 'recovered_from_durable_checkpoint' in history
