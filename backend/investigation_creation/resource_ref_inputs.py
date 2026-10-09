"""Tool-only reference inputs; persisted Draft and execution contracts stay unchanged."""
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import Field, StrictStr

from .contracts import (StrictModel, ExistingLexiconRecallPlan, TemporaryTermsRecallPlan,
                        CreatorInvestigationMode, InvestigationDraftConfiguration)
from .approval import ProposalDraftConfiguration, ProposalDraftCreation, UseRuleSetProposalInput
from backend.resource_management.contracts import ResourceError


class ResourceRefRecallPlan(StrictModel):
    strategy: Literal['resource_ref']
    resource_ref: StrictStr = Field(pattern=r'^resource-ref:[0-9a-f]{32}$')
    enabled_main_terms: list[StrictStr] | None = Field(default=None, max_length=100)


class ToolSearchMode(StrictModel):
    mode: Literal['search']
    recall_plan: Annotated[ResourceRefRecallPlan | ExistingLexiconRecallPlan | TemporaryTermsRecallPlan,
                           Field(discriminator='strategy')]


ToolInvestigationMode = Annotated[ToolSearchMode | CreatorInvestigationMode, Field(discriminator='mode')]


class ToolDraftConfiguration(InvestigationDraftConfiguration):
    investigation: ToolInvestigationMode


class ToolProposalConfiguration(ProposalDraftConfiguration):
    investigation: ToolInvestigationMode


class ToolProposalCreation(ProposalDraftCreation):
    configuration: ToolProposalConfiguration


class ToolUseRuleSetProposalInput(UseRuleSetProposalInput):
    create_draft: ToolProposalCreation | None = None


def resolve_arguments(name, arguments, service, *, principal, session_id=''):
    if name not in {'create_investigation_draft', 'update_investigation_draft', 'use_ruleset_proposal'}:
        return arguments, None
    result = deepcopy(arguments)
    target = (result.get('create_draft') or {}) if name == 'use_ruleset_proposal' else result
    investigation = (target.get('configuration') or {}).get('investigation') or {}
    plan = investigation.get('recall_plan') or {}
    resource_ref = None
    if plan.get('strategy') == 'resource_ref':
        parsed = ResourceRefRecallPlan.model_validate(plan)
        resource_ref = parsed.resource_ref
        resolved = service().resolve_lexicon_ref(parsed.resource_ref, principal=principal, session_id=session_id)
        if parsed.enabled_main_terms is not None:
            if resolved['strategy'] == 'temporary_terms':
                raise ResourceError('请先修改临时编辑稿再使用其新引用，不能覆盖引用中的搜索词。',
                                    code='INVALID_TOOL_ARGUMENTS')
            resolved['enabled_main_terms'] = parsed.enabled_main_terms
        investigation['recall_plan'] = resolved
    return result, resource_ref


def hide_legacy_hash_input(parameters):
    """Accept old transcripts at runtime, but advertise the new handle to models."""
    definitions = parameters.get('$defs', {})
    # Full content remains accepted for old transcripts and direct UI submissions,
    # but generated/edited resources must travel by reference in model calls.
    temporary = definitions.get('TemporaryTermsRecallPlan', {})
    temporary.get('properties', {}).pop('lexicon_content', None)
    temporary.get('properties', {}).pop('source_edit_ref', None)
    if 'ResourceRefRecallPlan' not in definitions:
        return
    definitions.pop('ExistingLexiconRecallPlan', None)
    legacy = '#/$defs/ExistingLexiconRecallPlan'

    def visit(node):
        if isinstance(node, dict):
            for key in ('oneOf', 'anyOf'):
                if key in node:
                    node[key] = [entry for entry in node[key] if entry.get('$ref') != legacy]
            mapping = node.get('discriminator', {}).get('mapping', {})
            if mapping.get('existing_lexicon') == legacy:
                mapping.pop('existing_lexicon')
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(parameters)
    # Pydantic retains definitions for the hidden legacy content field. Remove
    # unreachable definitions so deferred describe does not still ship the entire
    # lexicon storage schema to the model.
    reachable = set()
    def collect(node):
        if isinstance(node, dict):
            ref = node.get('$ref', '')
            if ref.startswith('#/$defs/'):
                name = ref.split('/')[-1]
                if name not in reachable:
                    reachable.add(name)
                    collect(definitions.get(name, {}))
            for key, value in node.items():
                if key != '$defs':
                    collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)
    collect(parameters)
    for name in list(definitions):
        if name not in reachable:
            del definitions[name]
