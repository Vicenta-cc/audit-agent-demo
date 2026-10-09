"""The same product cases drive frontend projection and real resource adoption."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.investigation_creation.contracts import TemporaryTermsRecallPlan
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.tools import HermesToolExecutionIdentity
from backend.resource_management.contracts import LexiconContent
from test_investigation_creation_conversation import creation_stack, _t1_temporary_arguments
from test_resource_lifecycle import service, CTX, P
from test_ruleset_proposal_approval import approve, creation
from test_ruleset_proposal_presentation import content, run


CASES = json.loads((Path(__file__).parent / 'fixtures/lexicon_search_semantics.json').read_text())


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['name'])
def test_temporary_and_saved_projection_and_validation_agree(service, case):
    body = deepcopy(case['content'])
    expected = case['expected_terms']
    edit = service.create_lexicon(body, **CTX)
    assert edit['search_terms'] == expected
    saved = service.save(edit['edit_id'], 1, 'new', 'save-projection', **CTX)
    assert service.lexicons.enabled_search_terms(saved['resource_id']) == expected
    assert service.read('lexicon', saved['resource_id'], principal=P)['search_terms'] == expected
    if expected:
        plan = TemporaryTermsRecallPlan.model_validate(edit['recall_plan'])
        assert plan.terms == expected
        assert plan.lexicon_content == LexiconContent.model_validate(body)
    else:
        with pytest.raises(ValidationError, match='at least one search term'):
            TemporaryTermsRecallPlan.model_validate(edit['recall_plan'])


@pytest.mark.parametrize('case', [c for c in CASES if c['expected_terms']], ids=lambda c: c['name'])
@pytest.mark.parametrize('source', ['temporary', 'saved'])
@pytest.mark.parametrize('entrypoint', ['create', 'adopt'])
def test_same_terms_reach_draft_preview_through_both_tools(creation_stack, content, case, source, entrypoint):
    stack = creation_stack
    shown = run(stack, content)
    principal = Principal('principal-a')
    ctx = dict(session_id=shown['session_id'], principal=principal)
    manager = stack['app_service'].resource_management
    edit = manager.create_lexicon(deepcopy(case['content']), **ctx)
    selected = edit if source == 'temporary' else manager.save(edit['edit_id'], 1, 'new', 'save-for-draft', **ctx)
    args = creation() if entrypoint == 'adopt' else _t1_temporary_arguments(stack)
    args['configuration']['investigation']['recall_plan'] = {
        'strategy': 'resource_ref', 'resource_ref': selected['resource_ref'],
    }
    if entrypoint == 'adopt':
        result, _, _ = approve(stack, shown, arguments={'create_draft': args})
    else:
        result = stack['tool_service'].execute_with_identity(
            'create_investigation_draft', args, principal=principal,
            identity=HermesToolExecutionIdentity(shown['session_id'], 'create', 'one-call'),
        )
    assert result['status'] == 'ok', result
    draft = result['data']['draft']
    preview = stack['app_service'].resource_service.confirmation_preview(
        stack['app_service'].get_draft(draft['id'], principal=principal), principal=principal,
    )
    assert list(preview.resolved_search_terms) == case['expected_terms']
    assert preview.can_confirm
    # Later resource edits must not silently rewrite the already adopted content.
    manager.update(edit['edit_id'], 1, [{'operation': 'set_metadata', 'values': {'title': '修改后的词库'}}], **ctx)
    assert stack['app_service'].get_draft(draft['id'], principal=principal).configuration.model_dump(mode='json') == draft['configuration']
    with stack['creation_store']._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM investigation_drafts').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM investigation_runs').fetchone()[0] == 0
