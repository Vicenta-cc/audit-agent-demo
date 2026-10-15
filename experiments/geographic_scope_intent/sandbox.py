"""Allowlisted fixture dispatcher. No production imports or callable forwarding."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

from .contract import CandidateConfigureInput, configure_schema, validate_source, normalize_target

HERE = Path(__file__).resolve().parent
READS = frozenset({'query_investigation_options', 'get_investigation_draft', 'get_investigation_run',
                  'get_ruleset_proposal', 'read_resource', 'get_resource_edit', 'get_resource_save',
                  'list_investigation_rounds'})
EXECUTION = frozenset({'confirm_and_queue_investigation'})


def candidate_tools(case):
    tools = json.loads((HERE/'baseline_tools.json').read_text())
    for tool in tools:
        if tool['name'] == 'configure_investigation_draft':
            tool['parameters'] = configure_schema()
            tool['description'] += '\n实验候选：relevance_scope_change 省略保留，set 携带目标/策略/用户原话，clear 只携带取消原话。'
    if not case.get('feature_flags', {}).get('investigation_multi_round_enabled', False):
        tools = [t for t in tools if t['name'] not in {'copy_investigation_draft','list_investigation_rounds'}]
    return tools


def validate_json_schema(value, schema, root=None):
    """Validate the emitted Pydantic schema vocabulary without another dependency.

    Configure additionally uses its original operation validator. This is not a
    substitute for business resource authorization or a general JSON Schema engine.
    """
    root = root or schema
    if '$ref' in schema:
        node = root
        for part in schema['$ref'].split('/')[1:]:
            node = node[part]
        return validate_json_schema(value, node, root)
    if 'anyOf' in schema:
        for branch in schema['anyOf']:
            try:
                validate_json_schema(value, branch, root)
                return
            except ValueError:
                pass
        raise ValueError('no schema alternative matched')
    if 'oneOf' in schema:
        matched = 0
        for branch in schema['oneOf']:
            try:
                validate_json_schema(value,branch,root)
                matched += 1
            except ValueError:
                pass
        if matched != 1:
            raise ValueError('exactly one schema alternative required')
        return
    kind = schema.get('type')
    matches = {'object': isinstance(value, dict), 'array': isinstance(value, list),
               'string': isinstance(value, str), 'integer': type(value) is int,
               'number': type(value) in (int,float), 'boolean': type(value) is bool, 'null': value is None}
    if kind and not matches.get(kind, False):
        raise ValueError(f'expected {kind}')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('value outside enum')
    if 'const' in schema and value != schema['const']:
        raise ValueError('value differs from const')
    if isinstance(value, dict):
        if set(schema.get('required', []))-value.keys():
            raise ValueError('missing required fields')
        props = schema.get('properties', {})
        if schema.get('additionalProperties') is False and value.keys()-props.keys():
            raise ValueError('unknown fields')
        for key, child in value.items():
            if key in props:
                validate_json_schema(child, props[key], root)
            elif isinstance(schema.get('additionalProperties'), dict):
                validate_json_schema(child, schema['additionalProperties'], root)
    if isinstance(value, list):
        for child in value:
            validate_json_schema(child, schema.get('items', {}), root)
        if len(value) < schema.get('minItems',0) or len(value) > schema.get('maxItems',float('inf')):
            raise ValueError('array size outside bounds')
    if isinstance(value, str):
        if len(value) < schema.get('minLength',0) or len(value) > schema.get('maxLength',float('inf')):
            raise ValueError('string size outside bounds')
        if 'pattern' in schema and not re.search(schema['pattern'],value):
            raise ValueError('pattern mismatch')
    if type(value) in (int,float):
        if value < schema.get('minimum',float('-inf')) or value > schema.get('maximum',float('inf')):
            raise ValueError('number outside bounds')


class FixtureDispatcher:
    def __init__(self, case):
        self.case = deepcopy(case)
        self.tools = {t['name']:t for t in candidate_tools(case)}
        self.draft = deepcopy(case.get('current_draft'))
        self.calls = []

    def dispatch(self, name, arguments):
        receipt = {'simulated': True, 'mutation_applied': False, 'ok': False}
        self.calls.append({'name':name, 'arguments':deepcopy(arguments), 'receipt':receipt})
        if not isinstance(name,str) or name not in self.tools:
            receipt.update(code='UNKNOWN_TOOL')
            return receipt
        try:
            if not isinstance(arguments,dict):
                raise ValueError('arguments must be an object')
            validate_json_schema(arguments,self.tools[name]['parameters'])
            if name == 'configure_investigation_draft':
                CandidateConfigureInput.model_validate(arguments)
        except (ValueError,TypeError,KeyError):
            receipt.update(code='INVALID_ARGUMENTS')
            return receipt
        if name in READS:
            fixture = self.case['read_fixtures'].get(name)
            # Read only the already supplied synthetic state. Never invent a
            # historical Run, confirmation preview, or a different draft ID.
            if fixture is None and name == 'get_investigation_draft' and self.draft is not None:
                if arguments != {'draft_id':self.draft['draft_id']}:
                    receipt.update(code='FIXTURE_ARGUMENT_MISMATCH')
                    return receipt
                fixture = self.draft
            if fixture is None and name == 'list_investigation_rounds' and arguments == {}:
                fixture = {'current_task_round':self.case['task_round'],'current_draft':self.draft}
            if fixture is None:
                receipt.update(code='FIXTURE_MISSING')
                return receipt
            if 'responses' in fixture:
                matches = [r['result'] for r in fixture['responses'] if r['arguments'] == arguments]
                if len(matches) != 1:
                    receipt.update(code='FIXTURE_ARGUMENT_MISMATCH')
                    return receipt
                fixture = matches[0]
            receipt.update(ok=True,code='FIXTURE_READ',data=deepcopy(fixture))
            return receipt
        if name in EXECUTION:
            receipt.update(code='EXECUTION_BLOCKED',captured_only=True)
            return receipt
        if name != 'configure_investigation_draft':
            receipt.update(ok=True,code='MUTATION_CAPTURED',captured_only=True,
                           notice='模拟捕获；未生成、保存、确认或启动真实资源/任务。')
            return receipt
        if arguments['operation'] == 'create':
            if self.draft is not None:
                receipt.update(code='EDITABLE_DRAFT_EXISTS')
                return receipt
            if not self.case['resources_ready']:
                receipt.update(code='RESOURCES_NOT_READY')
                return receipt
        elif self.draft is None or arguments['draft_id'] != self.draft['draft_id'] or arguments['expected_revision'] != self.draft['revision']:
            receipt.update(code='DRAFT_REVISION_STALE')
            return receipt
        state = self.case['resource_state']
        for key in ('ruleset_ref','lexicon_ref'):
            allowed_refs = {state.get('refs',{}).get(key)}
            # Available edit references are explicitly typed fixtures, not model
            # claims. No resource content is synthesized by captured mutations.
            if key == 'lexicon_ref':
                allowed_refs.update(state.get('available_refs',[]))
            if key in arguments and arguments[key] not in allowed_refs:
                receipt.update(code='RESOURCE_REF_INVALID')
                return receipt
        if 'ruleset_ref' in arguments and state.get('kind') == 'temporary':
            shown = state.get('displayed_turn')
            adopted = state.get('adoption_turn')
            if not (state.get('shown') and state.get('adoption') and isinstance(shown,int)
                    and isinstance(adopted,int) and shown < adopted <= self.case['application_turn']):
                receipt.update(code='ADOPTION_NOT_AUTHORIZED')
                return receipt
        change = arguments.get('relevance_scope_change')
        if change:
            source = validate_source(self.case,change['source_quote'])
            if not source['ok']:
                receipt.update(code=source['code'],source=source)
                return receipt
            if change['action'] == 'set':
                region = normalize_target(change['target_text'])
                if not region['ok']:
                    receipt.update(code=region['code'],region=region)
                    return receipt
        draft = deepcopy(self.draft) or {'draft_id':'simulated-draft:'+self.case['case_id'], 'revision':0,'scope':None}
        draft.update({k:v for k,v in arguments.items() if k not in {'operation','draft_id','expected_revision','relevance_scope_change'}})
        if change:
            draft['scope'] = None if change['action'] == 'clear' else deepcopy(change)
        draft['revision'] += 1
        self.draft = draft
        receipt.update(ok=True,code='DRAFT_SIMULATED',captured_only=True,data=deepcopy(draft),
                       notice='仅内存模拟草案；未写入正式 store，地域执行未接入。')
        return receipt
