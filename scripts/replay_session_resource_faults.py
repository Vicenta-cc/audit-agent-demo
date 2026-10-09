"""Offline causal replay of captured real Qwen calls against a Git export.

Only generated resource IDs are rebound. Business arguments and the original
resource author's output stay fixed. The conversation engine is scripted; tool
dispatch, validation, receipts, publication and SQLite writes are real.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--with-selections', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = args.repo.resolve()
    sys.path[:0] = [str(repo), str(repo/'tests')]
    # Keep provider keys, runtime DB paths and dotenv out of all three runs.
    minimal = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'TMPDIR') if k in os.environ}
    os.environ.clear()
    os.environ.update(minimal)
    os.environ.update(PYTHON_DOTENV_DISABLED='1', PYTHONDONTWRITEBYTECODE='1', APP_AUTH_MODE='disabled')
    for key, path in {
        'XHS_AUDIT_DATA_DIR': 'data', 'XHS_AUDIT_OUTPUTS_DIR': 'outputs', 'HERMES_HOME': 'hermes',
        'APP_AUTH_DB': 'auth.sqlite3', 'CRAWLER_AUTH_KEY_FILE': 'data/crawler_auth.key',
        'MEDIACRAWLER_DIR': 'disabled-crawler', 'MEDIACRAWLER_REQUEST_SCHEDULER_DB': 'scheduler.sqlite3',
    }.items():
        os.environ[key] = str(root/path)
    os.chdir(repo)
    from test_investigation_creation_conversation import creation_stack
    from backend.investigation_creation.principal import Principal
    from backend.investigation_creation.tools import HermesToolExecutionIdentity
    from backend.investigation_creation.contracts import ConfirmAndQueueCommand
    from backend.rulesets.contracts import RuleSetContent
    from backend.resource_management.contracts import LexiconContent

    captured = json.loads(args.evidence.read_text())
    fixture = creation_stack.__wrapped__(root)
    stack = next(fixture)
    conversation, service = stack['conversation'], stack['tool_service']
    principal = Principal('principal-a')
    session = conversation.create_session(principal=principal, workspace_key='fixed-real-qwen-trace')
    mapping = {captured['session']: session.id}
    record = dict(mode='offline recorded-model replay, no provider calls',
                  source_evidence_sha256=hashlib.sha256(args.evidence.read_bytes()).hexdigest(),
                  repo=str(repo), with_selections=args.with_selections, turns=[], selection_actions=[])

    def persist():
        (root/'replay.json').write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str)+'\n')

    def rebind(value):
        if isinstance(value, str):
            return mapping.get(value, value)
        if isinstance(value, dict):
            return {k: rebind(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rebind(v) for v in value]
        return value

    def map_ids(old, new):
        if isinstance(old, dict) and isinstance(new, dict):
            for key in old.keys() & new.keys():
                left, right = old[key], new[key]
                if (key=='id' or key.endswith('_id') or key=='resource_ref') and isinstance(left, str) and isinstance(right, str) and left and right and left!=right:
                    if left in mapping and mapping[left]!=right:
                        raise AssertionError('Resource identity unexpectedly changed: '+key)
                    mapping[left] = right
                else:
                    map_ids(left, right)
        elif isinstance(old, list) and isinstance(new, list):
            for left, right in zip(old, new):
                map_ids(left, right)

    def counts():
        with stack['creation_store']._connect() as conn:
            return {table: conn.execute('SELECT count(*) FROM '+table).fetchone()[0]
                    for table in ('investigation_drafts', 'investigation_runs', 'ruleset_proposal_approvals')}

    def business_state():
        # Do not call list_edits/get_edit: those may issue lexicon references.
        values = []
        with stack['creation_store']._connect() as conn:
            for kind, table in [('ruleset','ruleset_proposals'), ('lexicon','lexicon_edits')]:
                for row in conn.execute('SELECT version,content_json FROM '+table+' WHERE session_id=? ORDER BY created_at', (session.id,)):
                    values.append(dict(kind=kind,version=row['version'],content=json.loads(row['content_json'])))
        return values

    active_call = None
    def fixed_generation(kind, request):
        assert active_call and active_call['tool'] in ('create_ruleset_proposal', 'create_lexicon_edit')
        original_args = active_call['arguments']['generation_request']
        for key, value in original_args.items():
            assert request.model_dump(mode='json')[key]==value
        body = active_call['result']['data']['content']
        return (RuleSetContent if kind=='ruleset' else LexiconContent).model_validate(deepcopy(body))

    class ReplayAgent:
        def __init__(self, turn_record, source):
            self.trace, self.source = turn_record, source

        def run_conversation(self, message, *, conversation_history=None, task_id, **unused):
            nonlocal active_call
            messages = [*(conversation_history or []), dict(role='user', content=message)]
            for index, call in enumerate(self.source['tool_calls']):
                active_call = call
                arguments = rebind(deepcopy(call['arguments']))
                changes = []
                def changed(a, b, path=''):
                    if isinstance(a, dict):
                        for k in a: changed(a[k], b[k], path+'/'+k)
                    elif isinstance(a, list):
                        for j, (x,y) in enumerate(zip(a,b)): changed(x,y,path+'/'+str(j))
                    elif a!=b:
                        assert isinstance(a, str) and mapping.get(a)==b
                        changes.append(dict(path=path, original=a, rebound=b))
                changed(call['arguments'], arguments)
                call_id = f'{task_id}:recorded:{index}'
                result = service.execute_with_identity(call['tool'], arguments, principal=principal,
                    identity=HermesToolExecutionIdentity(session.id, task_id, call_id))
                item = dict(tool=call['tool'], arguments=arguments, identity_rebindings=changes,
                            business_arguments_unchanged=True, result=result,
                            captured_status=call['result']['status'])
                self.trace['calls'].append(item)
                if result['status']=='ok' and call['result']['status']=='ok':
                    map_ids(call['result']['data'], result['data'])
                persist()
                messages.extend([
                    dict(role='assistant', content='', tool_calls=[dict(id=call_id, type='function',
                         function=dict(name=call['tool'], arguments=arguments))]),
                    dict(role='tool', tool_call_id=call_id, name=call['tool'], content=json.dumps(result, ensure_ascii=False)),
                ])
            # Backend produces its own trusted presentation from real receipts.
            answer = '按记录重放完成，以业务工具回执为准。'
            messages.append(dict(role='assistant', content=answer))
            return dict(completed=True, failed=False, interrupted=False, final_response=answer,
                        messages=messages, turn_exit_reason='completed', api_calls=0)

    def select_original(original_id, version, purpose, event, previous=''):
        from backend.resource_management.session_state import SessionResourceReader
        from backend.resource_management.selections import ResourceSelectionWriter
        reader = SessionResourceReader(stack['creation_store'].db_path, stack['resource_db'], conversation.store.db_path)
        target = reader.detail(session.id, f'edit/{mapping[original_id]}/{version}', principal=principal)
        result = ResourceSelectionWriter(stack['creation_store'], reader).record(session.id, principal=principal,
            kind=target['kind'], purpose=purpose, key=target['key'], content_hash=target['content_hash'],
            event_id=event, expected_event_id=previous)
        record['selection_actions'].append(result)

    def generated_rule(label):
        return next(c['result']['data']['proposal_id'] for t in captured['turns'] if t['label']==label
                    for c in t['tool_calls'] if c['tool']=='create_ruleset_proposal')

    def forbid_network(*unused, **kw):
        raise AssertionError('Network access is forbidden during fixed replay')

    try:
        with patch.object(service.resource_generator, 'generate', side_effect=fixed_generation), patch.object(socket.socket, 'connect', side_effect=forbid_network):
            for original in captured['turns']:
                before = counts()
                entry = dict(label=original['label'], input=original['input'], before=before, calls=[])
                record['turns'].append(entry)
                route = conversation._provider_route(original['input'])
                cache_key = session.id if route=='default' else (session.id, route)
                conversation._agents[cache_key] = ReplayAgent(entry, original)
                accepted, repeated = conversation.accept_message(session.id, client_message_id=original['label'],
                    content=original['input'], principal=principal)
                assert not repeated
                result = conversation.execute_turn(accepted.id)
                saved = conversation.store.get_turn(accepted.id)
                entry.update(status=saved.status, after=counts(), state=business_state())
                old = original['artifact'].get('proposal_presentations', [])
                new = saved.public_artifact.get('proposal_presentations', [])
                assert len(old)==len(new), ('presentation count',original['label'],len(old),len(new))
                map_ids(old, new)
                persist()
                if args.with_selections and original['label']=='02-generate-b':
                    select_original(generated_rule('02-generate-b'), 1, 'edit', 'select-b1')
                    select_original(generated_rule('01-generate-a'), 1, 'view', 'view-a1')
                if args.with_selections and original['label']=='05-edit-b-v3':
                    select_original(generated_rule('02-generate-b'), 3, 'edit', 'select-b3', 'select-b1')
                if original['label']=='06b-additional-confirmation':
                    # Same explicitly separated fixture freeze as the live run.
                    draft = next(c['result']['data']['draft'] for c in entry['calls'] if c['tool']=='use_ruleset_proposal' and c['result']['status']=='ok')
                    run = stack['app_service'].confirm_and_queue(ConfirmAndQueueCommand(draft_id=draft['id'],
                        expected_revision=draft['current_revision'], confirmed=True, idempotency_key='fixture-freeze'), principal=principal)
                    record['fixture_freeze'] = dict(run_id=run.id, worker_started=False)
            with stack['creation_store']._connect() as conn:
                record['final_receipts'] = [dict(row) for row in conn.execute('SELECT tool_name,status,response_json FROM investigation_creation_tool_receipts ORDER BY created_at')]
            record['status'] = 'REPLAY_COMPLETE'
    except Exception as exc:
        record.update(status='REPLAY_ERROR', error_type=type(exc).__name__, error=str(exc))
    finally:
        record['identity_mapping'] = mapping
        persist()
        next(fixture, None)
    print(json.dumps(dict(status=record['status'], output=str(root),
        turns=[dict(label=t['label'], counts=t.get('after'), calls=[(c['tool'], c['result']['status'], c['result'].get('error',{}).get('code')) for c in t['calls']]) for t in record['turns']],
        error=record.get('error')), ensure_ascii=False))
    return 0 if record['status']=='REPLAY_COMPLETE' else 1


if __name__=='__main__':
    raise SystemExit(main())
