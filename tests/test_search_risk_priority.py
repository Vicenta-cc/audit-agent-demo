"""Risk priority belongs to confirmation/execution, not storage or model prompts."""
from copy import deepcopy

import pytest

from backend.investigation_creation.contracts import CreateDraftCommand, ConfirmAndQueueCommand
from backend.investigation_creation.principal import Principal
from backend.resource_management.contracts import LexiconContent
from test_investigation_creation_conversation import creation_stack, _t1_temporary_arguments


def content():
    return LexiconContent.model_validate({'title': '检索优先级测试', 'entries': [
        {'id': 'm', 'term': '主题', 'risk_level': '高'},
        *[{'id': f'v{i}', 'term': term, 'kind': 'variant', 'parent_id': 'm', 'risk_level': risk}
          for i, (term, risk) in enumerate([
              ('低词', '低'), ('中词', '中'), ('高词甲', '高'), ('高词乙', 'high'), ('未知词', '未知')])],
        {'id': 'disabled', 'term': '停用词', 'kind': 'variant', 'parent_id': 'm', 'risk_level': '高', 'enabled': False},
        {'id': 'off', 'term': '停用主题', 'enabled': False},
        {'id': 'off-v', 'term': '停用主题的词', 'kind': 'variant', 'parent_id': 'off', 'risk_level': '高'},
        {'id': 'tag', 'term': '标签', 'kind': 'tag', 'risk_level': '高'},
    ]})


EXPECTED = ['高词甲', '高词乙', '中词', '低词', '未知词']


def test_priority_preserves_source_and_same_risk_order():
    lexicon = content()
    before = lexicon.storage_dict()
    terms = lexicon.search_terms()
    assert terms == ['低词', '中词', '高词甲', '高词乙', '未知词']
    assert lexicon.prioritize_search_terms(terms) == EXPECTED
    assert lexicon.prioritize_search_terms(['低词', '高词乙']) == ['高词乙', '低词']
    assert lexicon.storage_dict() == before
    assert lexicon.search_terms() == terms


def test_duplicates_use_highest_enabled_priority_and_legacy_main_fallback():
    lexicon = LexiconContent.model_validate({'title': '兼容', 'entries': [
        {'id': 'low', 'term': '重复', 'risk_level': '低'},
        {'id': 'med', 'term': '中词', 'risk_level': '中风险'},
        {'id': 'high', 'term': '重复', 'platform': '抖音', 'risk_level': '高风险'},
        {'id': 'disabled', 'term': '中词', 'platform': '抖音', 'risk_level': '高', 'enabled': False},
    ]})
    assert lexicon.prioritize_search_terms(['中词', '重复']) == ['重复', '中词']
    assert lexicon.search_terms() == ['重复', '中词']


@pytest.mark.parametrize('source', ['temporary', 'card_edited', 'saved', 'opened_edited', 'plain'])
def test_preview_and_frozen_execution_share_order_without_rewriting_draft(creation_stack, source):
    stack = creation_stack
    app = stack['app_service']
    principal = Principal('principal-a')
    ctx = dict(session_id='priority-session', principal=principal)
    manager = app.resource_management
    lexicon = content()
    args = _t1_temporary_arguments(stack)
    expected = EXPECTED
    if source == 'plain':
        plan = {'strategy': 'temporary_terms', 'terms': lexicon.search_terms(), 'source_lexicon_ids': []}
        expected = lexicon.search_terms()
    else:
        edit = manager.create_lexicon(lexicon.storage_dict(), **ctx)
        selected = edit
        if source in {'saved', 'opened_edited'}:
            selected = manager.save(edit['edit_id'], 1, mode='new', operation_id='priority-save', **ctx)
            if source == 'opened_edited':
                edit = manager.open('lexicon', selected['resource_id'], **ctx)
        if source in {'card_edited', 'opened_edited'}:
            selected = manager.update(edit['edit_id'], 1, [
                {'operation': 'upsert_entry', 'target_id': 'v0', 'values': {'risk_level': '高'}}], **ctx)
            expected = ['低词', '高词甲', '高词乙', '中词', '未知词']
        plan = manager.resolve_lexicon_ref(selected['resource_ref'], **ctx)
    args['configuration']['investigation']['recall_plan'] = plan
    draft = app.create_draft(CreateDraftCommand.model_validate(args), principal=principal)
    original = deepcopy(draft.configuration.model_dump(mode='json'))
    preview = app.get_confirmation_preview(draft.id, principal=principal)
    assert preview.can_confirm, preview.blockers
    assert preview.resolved_search_terms == expected
    field = 'enabled_main_terms' if source == 'saved' else 'temporary_terms'
    assert getattr(preview.recall_plan, field) == expected
    run = app.confirm_and_queue(ConfirmAndQueueCommand(
        draft_id=draft.id, expected_revision=draft.current_revision,
        confirmed=True, idempotency_key='priority-confirm'), principal=principal)
    frozen = run.confirmed_configuration
    assert frozen['resolved_search_terms'] == expected
    assert frozen['recall_plan'][field] == expected
    assert frozen['execution']['keyword'].split(',') == expected
    if source == 'saved':
        assert frozen['execution']['lexicon_keywords'] == expected
    assert app.get_draft(draft.id, principal=principal).configuration.model_dump(mode='json') == original
    # Re-reading a confirmed run must never re-sort from changed live resources.
    if source != 'plain':
        manager.update(edit['edit_id'], selected.get('version', 1), [
            {'operation': 'upsert_entry', 'target_id': 'v4', 'values': {'risk_level': '高'}}], **ctx)
    replay = app.confirm_and_queue(ConfirmAndQueueCommand(
        draft_id=draft.id, expected_revision=draft.current_revision,
        confirmed=True, idempotency_key='priority-confirm'), principal=principal)
    assert replay.confirmed_configuration == frozen
