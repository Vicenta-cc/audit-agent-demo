"""Real persistence checks for narrow edits, including rollback and version origins."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from test_resource_lifecycle import service, P, CTX, save, lexicon
from backend.resource_management.contracts import ResourceError, UpdateEditInput
from backend.investigation_creation.errors import ProposalNotFoundError


@pytest.fixture
def edit(service):
    body = json.loads((Path(__file__).parent/'fixtures/recruitment_fraud_ruleset.json').read_text())
    body['general_exemptions'].append({'exemption_id': 'keep.this', 'name': 'Keep', 'condition': 'No payment or recruitment is solicited.'})
    proposal = service.app.create_ruleset_proposal(body, session_id=CTX['session_id'])
    return service.get_edit(proposal.proposal_id, **CTX)


@pytest.mark.parametrize('rule_id', ['', 'recruitment.fee_request'])
def test_exemption_add_modify_remove_preserves_every_other_field(service, edit, rule_id):
    original = deepcopy(edit['content'])
    expected = deepcopy(original)
    items = (expected['categories'][0]['rules'][0]['rule_exemptions'] if rule_id
             else expected['general_exemptions'])
    added = {'exemption_id': 'new.context', 'name': 'Normal context',
             'condition': 'No payment or offer is requested.', 'source_mappings': []}
    items.append(added)
    result = service.update(edit['edit_id'], edit['version'], [
        {'operation': 'add_exemption', 'rule_id': rule_id, 'values': added}], **CTX)
    assert result['content'] == expected
    added['condition'] = 'Only an explicitly fictional example, without solicitation.'
    result = service.update(edit['edit_id'], result['version'], [
        {'operation': 'update_exemption', 'rule_id': rule_id, 'target_id': 'new.context',
         'values': {'condition': added['condition']}}], **CTX)
    assert result['content'] == expected
    result = service.update(edit['edit_id'], result['version'], [
        {'operation': 'remove_exemption', 'rule_id': rule_id, 'target_id': 'new.context'}], **CTX)
    assert result['content'] == original
    assert service.get_edit(edit['edit_id'], **CTX)['content'] == original


@pytest.mark.parametrize('change', [
    {'operation': 'update_exemption', 'target_id': 'missing', 'values': {'name': 'X'}},
    {'operation': 'remove_exemption', 'target_id': 'missing'},
    {'operation': 'update_exemption', 'target_id': 'keep.this', 'values': {'exemption_id': 'renamed'}},
    {'operation': 'update_exemption', 'target_id': 'keep.this', 'values': {'condition': ''}},
    {'operation': 'remove_exemption', 'target_id': 'keep.this', 'values': {'name': 'X'}},
    {'operation': 'add_exemption', 'values': {'exemption_id': 'keep.this', 'name': 'X', 'condition': 'X'}},
    {'operation': 'add_exemption', 'rule_id': 'missing', 'values': {'exemption_id': 'new', 'name': 'X', 'condition': 'X'}},
    {'operation': 'remove_exemption', 'rule_id': 'recruitment.fee_request', 'target_id': 'keep.this'},
])
def test_invalid_batch_is_atomic(service, edit, change):
    with pytest.raises((ResourceError, ValidationError, ValueError)):
        service.update(edit['edit_id'], edit['version'], [
            {'operation': 'update_rule', 'target_id': 'recruitment.fee_request',
             'values': {'suggested_risk_level': 'medium'}}, change], **CTX)
    assert service.get_edit(edit['edit_id'], **CTX)['content'] == edit['content']
    assert service.get_edit(edit['edit_id'], **CTX)['version'] == edit['version']


def test_exemption_version_and_session_checks(service, edit):
    change = {'operation': 'remove_exemption', 'target_id': 'keep.this'}
    result = service.update(edit['edit_id'], 1, [change], **CTX)
    with pytest.raises(ResourceError, match='已经变化'):
        service.update(edit['edit_id'], 1, [change], **CTX)
    with pytest.raises(ProposalNotFoundError):
        service.update(edit['edit_id'], result['version'], [change], session_id='elsewhere', principal=P)
    assert service.get_edit(edit['edit_id'], **CTX)['content'] == result['content']


def test_open_specific_published_revision_does_not_copy_latest(service, edit):
    v1 = save(service, edit)
    original = deepcopy(edit['content'])
    opened = service.open('ruleset', v1['resource_id'], **CTX)
    changed = service.update(opened['edit_id'], 1, [
        {'operation': 'update_rule', 'target_id': 'recruitment.fee_request',
         'values': {'suggested_risk_level': 'medium'}}], **CTX)
    v2 = save(service, changed, 'v2', 'update')
    assert v1['revision_id'] != v2['revision_id']
    old = service.open('ruleset', v1['resource_id'], revision_id=v1['revision_id'], **CTX)
    assert old['content'] == original
    assert old['source']['published_revision_id'] == v1['revision_id']
    assert service.open('ruleset', v1['resource_id'], **CTX)['content'] == changed['content']
    with pytest.raises(ResourceError, match='已经变化'):
        save(service, old, 'cannot-overwrite-v2', 'update')
    other = save(service, edit, 'other-copy', 'copy')
    with pytest.raises(ResourceError, match='不属于'):
        service.open('ruleset', other['resource_id'], revision_id=v1['revision_id'], **CTX)


def test_lexicon_cannot_receive_exemption_operations(service):
    edit = service.create_lexicon(lexicon(), **CTX)
    with pytest.raises(ResourceError):
        service.update(edit['edit_id'], 1, [{'operation': 'remove_exemption', 'target_id': 'main-1'}], **CTX)
    assert service.get_edit(edit['edit_id'], **CTX)['content'] == edit['content']


def test_schema_exposes_scope_and_rejects_scope_on_non_exemption_operations():
    args = dict(edit_id='e', expected_version=1)
    assert UpdateEditInput(**args, changes=[{'operation': 'remove_exemption', 'target_id': 'x', 'rule_id': 'r'}])
    with pytest.raises(ValidationError):
        UpdateEditInput(**args, changes=[{'operation': 'remove_rule', 'target_id': 'r', 'rule_id': 'wrong'}])
