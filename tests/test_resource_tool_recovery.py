"""Real contracts, version fences and bridge recovery; no live model required."""
import json
from types import SimpleNamespace
import pytest
from test_resource_lifecycle import service, P, CTX, save
from test_resource_exemption_edits import edit
from backend.resource_management.contracts import OpenResourceInput, ResourceError
from backend.resource_management.tool_recovery import argument_error
from backend.hermes_runtime.creation_discovery import creation_discovery_guidance


def test_exact_version_resolution_and_handles(service, edit):
    v1 = save(service, edit)
    opened = service.open('ruleset', v1['resource_id'], **CTX)
    changed = service.update(**opened['update_arguments'], changes=[{
        'operation': 'update_rule', 'target_id': 'recruitment.fee_request',
        'values': {'suggested_risk_level': 'medium'}}], **CTX)
    v2 = save(service, changed, 'v2', 'update')
    resource = service.read('ruleset', v1['resource_id'], principal=P)
    old = service.open(**resource['published_versions'][0]['open_arguments'], **CTX)
    assert old['content'] == edit['content']
    args = OpenResourceInput(kind='ruleset', resource_id=v1['resource_id'], published_version=1).model_dump()
    assert service.open(**args, **CTX)['content'] == edit['content']
    assert changed['update_arguments']['expected_version'] == changed['version']
    for extra in [{'published_version': 999}, {'published_version': 1, 'revision_id': v2['revision_id']}]:
        with pytest.raises(ResourceError):
            service.open('ruleset', v1['resource_id'], **extra, **CTX)
    with pytest.raises(ResourceError):
        service.update(**opened['update_arguments'], changes=[{'operation':'remove_rule','target_id':'recruitment.fee_request'}], **CTX)


def test_recovery_rechecks_tool_instead_of_requesting_full_body():
    error = argument_error('update_ruleset_proposal', {'proposal_id': 'p', 'general_exemptions_add': []})
    details = error['error']['details']
    assert details['mutation_applied'] is False
    assert details['alternative_tool'] == 'update_resource_edit'
    assert details['read_current_edit']['arguments'] == {'edit_id': 'p'}
    assert 'changes' in details['alternative_parameters']['properties']
    assert '抄写整套' in details['recovery']


@pytest.mark.parametrize('name,args', [
    ('open_resource_edit', {'resource_id':'r','version':2}),
    ('update_resource_edit', {'edit_id':'p','expected_version':1,'rules':[]}),
    ('update_resource_edit', {'edit_id':'p','expected_version':1,'changes':[{'operation':'set_rule'}]}),
    ('update_resource_edit', {'edit_id':'p','expected_version':1,'changes':[{'operation':'add_exemption','scope':'general','exemption':{}}]}),
])
def test_observed_bad_parameters_return_actual_schema(name, args):
    details = argument_error(name,args)['error']['details']
    assert details['mutation_applied'] is False
    assert details['parameters']['properties']
    assert details['validation_errors']


def test_bridge_recovery_is_scoped_and_valid_calls_unmodified():
    original = lambda name,args: 'original-validator'
    schemas = lambda: []
    bridge = SimpleNamespace(bridge_tool_schemas=schemas,validate_deferred_call_args=original)
    with creation_discovery_guidance(bridge):
        error = json.loads(bridge.validate_deferred_call_args('update_ruleset_proposal',{'proposal_id':'p'}))
        assert isinstance(error['error'], str)
        assert 'update_resource_edit' in error['error']
        assert 'changes' in error['parameters']['properties']
        assert 'content' not in error['parameters']['properties']
        assert bridge.validate_deferred_call_args('open_resource_edit',{'kind':'ruleset','resource_id':'r','published_version':2}) == 'original-validator'
        assert bridge.validate_deferred_call_args('other',{}) == 'original-validator'
    assert bridge.validate_deferred_call_args is original
    assert bridge.bridge_tool_schemas is schemas


def test_authorized_full_replacement_is_not_rejected(edit):
    # Recovery guidance must not remove the existing exact replacement operation.
    assert argument_error('update_ruleset_proposal', {
        'proposal_id': edit['edit_id'], 'expected_version': edit['version'],
        'content': edit['content']}) is None


def test_published_version_still_checks_owner(service, edit):
    from backend.investigation_creation.principal import Principal
    from backend.rulesets.errors import RuleSetNotFoundError
    stored = save(service, edit)
    with pytest.raises(RuleSetNotFoundError):
        service.open('ruleset', stored['resource_id'], published_version=1,
                     session_id='other', principal=Principal('other'))


def test_lexicon_patch_returns_next_version_and_preserves_others(service):
    from test_resource_lifecycle import lexicon
    old = service.create_lexicon(lexicon(), **CTX)
    changed = service.update(**old['update_arguments'], changes=[{
        'operation':'upsert_entry', 'target_id':'variant-1', 'values':{'note':'只改备注'}}], **CTX)
    expected = old['content']
    expected['entries'][1]['note'] = '只改备注'
    assert changed['content'] == expected
    assert changed['update_arguments'] == {'edit_id':old['edit_id'], 'expected_version':2}
