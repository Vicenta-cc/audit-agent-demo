"""Real P1 copy receipts and revision evidence remain mandatory in fixture plans."""
from uuid import uuid4
import socket

import pytest

from backend.investigation_creation.tools import HermesToolExecutionIdentity
from test_geographic_execution_plan import make_authority, build
from test_geographic_draft_configuration import stored, no_effects
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_unified_draft_configuration import P


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail('network/model entry forbidden')
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    from experiments.geographic_execution_plan import p3_bridge
    for module in (p3_bridge.names, p3_bridge.shared):
        monkeypatch.setattr(module, 'evaluate_relevance', blocked)


def copied_authority(stack, content, monkeypatch, *, hops=1, ui=False):
    from backend.audit_agent.config import settings
    from experiments.geographic_execution_plan.authority import capture_fixture_authority
    authority = make_authority(stack, content, ui=ui, formal_rules=True, formal_lexicon=True)
    session_id = authority['identity']['session_id']
    monkeypatch.setattr(settings, 'investigation_multi_round_enabled', True)
    for _ in range(hops):
        source_id = authority['identity']['draft_id']
        original = stored(stack, source_id)
        # Isolated historical fixture only: no P1 geography guard is bypassed.
        with stack['creation_store']._connect() as db:
            db.execute("UPDATE investigation_drafts SET status='QUEUED', confirmed_revision=?, confirmed_by=?, confirmed_at=? WHERE id=?",
                (original.current_revision, P.id, original.updated_at, source_id))
        result = stack['tool_service'].execute_with_identity('copy_investigation_draft',
            {'source_draft_id': source_id}, principal=P,
            identity=HermesToolExecutionIdentity(session_id, 'copy-runtime-' + uuid4().hex, 'copy-' + uuid4().hex))
        assert result['status'] == 'ok', result
        authority = capture_fixture_authority(stack['app_service'], result['data']['draft']['id'],
            principal=P, session_id=session_id, fixture_id='fixture:p2-real-copy')
    return authority


@pytest.mark.parametrize('hops', [1, 2])
@pytest.mark.parametrize('ui', [False, True])
def test_real_copy_one_and_multiple_hops(creation_stack, content, monkeypatch, hops, ui):
    authority = copied_authority(creation_stack, content, monkeypatch, hops=hops, ui=ui)
    plan = build(authority)
    assert len(plan.inputs.provenance.lineage) == hops
    assert plan.inputs.configuration.relevance_scope_state.source_kind == ('ui_selection' if ui else 'conversation')
    assert plan.inputs.provenance.lineage[-1].target_draft_id == plan.identity.draft_id
    no_effects(creation_stack)


@pytest.mark.parametrize('which', ['expected', 'committed'])
def test_ui_requires_both_selection_revision_records(creation_stack, content, which):
    authority = make_authority(creation_stack, content, ui=True, formal_rules=True, formal_lexicon=True)
    selection = authority['provenance']['selection']
    revision = selection[which + '_revision']
    authority['provenance']['history'] = [row for row in authority['provenance']['history']
        if (row['draft_id'], row['revision']) != (selection['draft_id'], revision)]
    with pytest.raises(ValueError):
        build(authority)


@pytest.mark.parametrize('mutation', ['source_revision_missing', 'source_revision_wrong_existing',
    'source_configuration', 'target_configuration', 'target_first_revision_missing'])
def test_copy_requires_exact_source_and_first_target_revision(creation_stack, content, monkeypatch, mutation):
    authority = copied_authority(creation_stack, content, monkeypatch, hops=2)
    link = authority['provenance']['lineage'][0]
    if mutation == 'source_revision_missing':
        link['source_revision'] = 999
    elif mutation == 'source_revision_wrong_existing':
        # Original draft rev1 was xhs; real copy source revision2 was dy.
        link['source_revision'] = 1
    elif mutation == 'source_configuration':
        link['source_configuration']['platform'] = 'xhs'
    elif mutation == 'target_configuration':
        link['target_configuration']['platform'] = 'xhs'
    else:
        authority['provenance']['history'] = [row for row in authority['provenance']['history']
            if (row['draft_id'], row['revision']) != (link['target_draft_id'], 1)]
    with pytest.raises(ValueError):
        build(authority)
