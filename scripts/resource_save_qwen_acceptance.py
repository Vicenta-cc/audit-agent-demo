"""Opt-in R06 real Qwen acceptance. Fresh synthetic SQLite; no workers/deployment.

Preparation uses fixture tools. Qwen chooses save/read calls in acceptance turns.
Transport failure is injected after a real resource commit, never by changing
model arguments, prompts or business results. Evidence distinguishes injected
failures from actual provider errors.
"""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import argparse
import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coordinator-env', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--max-calls', type=int, default=40)
    parser.add_argument('--cases', nargs='+', choices=['ruleset_reply', 'lexicon_reply', 'tool_receipt_gap'],
                        default=['ruleset_reply', 'lexicon_reply', 'tool_receipt_gap'])
    args = parser.parse_args()
    os.umask(0o077)
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(repo), str(repo/'tests')]
    from dotenv import dotenv_values
    credentials = dotenv_values(args.coordinator_env)
    key = credentials.get('DASHSCOPE_API_KEY')
    if not key:
        raise SystemExit('Coordinator credential missing')
    inherited = {k: os.environ[k] for k in ('HOME', 'PATH', 'LANG', 'LC_ALL', 'TMPDIR') if k in os.environ}
    os.environ.clear()
    os.environ.update(inherited)
    os.environ.update({k: credentials[k] for k in ('DASHSCOPE_API_KEY', 'DASHSCOPE_BASE_URL') if credentials.get(k)})
    os.environ.update(PYTHON_DOTENV_DISABLED='1', PYTHONDONTWRITEBYTECODE='1', APP_AUTH_MODE='disabled')
    for name, relative in {
        'XHS_AUDIT_DATA_DIR': 'globals/data', 'XHS_AUDIT_OUTPUTS_DIR': 'globals/outputs',
        'APP_AUTH_DB': 'globals/auth.sqlite3', 'HERMES_HOME': 'globals/hermes',
        'CRAWLER_AUTH_KEY_FILE': 'globals/crawler.key', 'MEDIACRAWLER_DIR': 'disabled-crawler',
        'MEDIACRAWLER_REQUEST_SCHEDULER_DB': 'globals/scheduler.sqlite3',
    }.items():
        os.environ[name] = str(root/relative)

    import httpx
    from openai import APIConnectionError
    from openai.resources.chat.completions import Completions
    from backend.investigation.store import InvestigationStore
    from backend.investigation_creation.conversation import InvestigationCreationConversationService
    from backend.investigation_creation.principal import Principal
    from backend.investigation_creation.store import InvestigationCreationStore
    from backend.investigation_creation.tools import configure_hermes_investigation_creation_tools
    from test_investigation_creation_conversation import creation_stack, _run_scripted_creation_turn
    from test_resource_save_recovery import actions, snapshot

    evidence = {'status': 'RUNNING', 'model_calls': [], 'cases': [], 'limitations': [
        'Synthetic fixture preparation; real Qwen save and recovery turns',
        'Injected transport/log failure, not a claim of a live provider incident or physical power loss',
        'No crawler, worker, production database or deployment; no spontaneous censor bypass test']}
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str).replace(key, '[REDACTED]') + '\n')
    def persist():
        write(root/'evidence.json', evidence)
    original_request = Completions.create
    for case, kind in [('ruleset_reply', 'ruleset'), ('lexicon_reply', 'lexicon'), ('tool_receipt_gap', 'lexicon')]:
        if case not in args.cases:
            continue
        folder = root/case
        folder.mkdir()
        record = {'name': case, 'calls': [], 'turns': [], 'checks': [], 'faults': []}
        evidence['cases'].append(record)
        fixture = creation_stack.__wrapped__(folder)
        stack = next(fixture)
        app, conv, service = stack['app_service'], stack['conversation'], stack['tool_service']
        # Rule proposal preparation does not instantiate the resource manager;
        # create its schema before the harness starts taking DB snapshots.
        _ = app.resource_management
        followup = False
        original_execute = service.execute_with_identity
        original_complete = app.store.complete_tool_execution
        def request(client, *a, **kw):
            if len(evidence['model_calls']) >= args.max_calls:
                raise RuntimeError('Acceptance model request budget exhausted')
            item = {'case': case, 'followup': followup, 'model': kw.get('model'), 'stream': kw.get('stream')}
            evidence['model_calls'].append(item)
            write(root/f'model-request-{len(evidence["model_calls"]):03d}.json', kw.get('messages'))
            if not followup and snapshot(stack)['resource_save_receipts']:
                item['injected_transport_failure'] = True
                record['faults'].append('Transport unavailable after resource commit for remainder of first turn')
                persist()
                raise APIConnectionError(request=httpx.Request('POST', 'https://acceptance.invalid/chat/completions'))
            persist()
            return original_request(client, *a, **kw)
        def execute(name, arguments, **kw):
            item = {'tool': name, 'arguments': deepcopy(arguments), 'followup': followup}
            record['calls'].append(item)
            persist()
            if name in {'confirm_and_queue_investigation', 'create_investigation_draft', 'use_ruleset_proposal'}:
                raise AssertionError('No investigation creation/start authorized in R06 acceptance')
            try:
                result = original_execute(name, arguments, **kw)
                item['result'] = result
                return result
            except Exception as exc:
                item['exception'] = str(exc)
                raise
            finally:
                persist()
        def complete(receipt_id, *, response, succeeded):
            if case == 'tool_receipt_gap' and not followup and response.get('data', {}).get('status') == 'saved':
                record['faults'].append('Tool log completion lost after real resource commit')
                persist()
                raise ConnectionError('Injected tool receipt write failure after commit')
            return original_complete(receipt_id, response=response, succeeded=succeeded)
        def turn(label, message):
            accepted, replay = conv.accept_message(session, client_message_id=label, content=message, principal=Principal('principal-a'))
            assert not replay
            detail = {'id': accepted.id, 'input': message}
            record['turns'].append(detail)
            output = io.StringIO()
            try:
                with patch.object(Completions, 'create', request), patch.object(service, 'execute_with_identity', execute), \
                     patch.object(app.store, 'complete_tool_execution', complete), redirect_stdout(output), redirect_stderr(output):
                    result = conv.execute_turn(accepted.id)
                stored = conv.store.get_turn(accepted.id)
                detail.update(status=stored.status, answer=result.answer, stop_reason=stored.stop_reason)
                write(folder/(label+'.transcript.json'), conv.store.latest_completed_hermes_transcript(session))
            except Exception as exc:
                detail.update(status='ERROR', error=str(exc))
            finally:
                (folder/(label+'.runtime.log')).write_text(output.getvalue().replace(key, '[REDACTED]'))
                persist()
        def check(name, passed):
            record['checks'].append({'name': name, 'passed': bool(passed)})
            persist()
        try:
            seed = _run_scripted_creation_turn(stack, content='请准备这份资源，先不保存。', actions=actions(kind)[:1],
                final_response='临时资源已准备好，尚未正式保存。')
            session = seed['session_id']
            conv._agents.clear()
            conv.fake_runtime = False
            configure_hermes_investigation_creation_tools(service, principal_provider=conv.principal_for_session)
            label = '审核规则' if kind == 'ruleset' else '词库'
            turn('save', f'请把刚才准备的{label}正式保存，保持内容不变，不创建或启动调查。')
            first = snapshot(stack)
            write(folder/'after-save.json', first)
            check('one actual save receipt', len(first['resource_save_receipts']) == 1)
            check('failed final reply recovered as saved', record['turns'][0].get('stop_reason') == 'recovered_successful_resource_save')
            check('fault actually injected', bool(record['faults']))
            # Drop all in-memory agents and reopen both application stores.
            old = conv
            old.close()
            app.store = InvestigationCreationStore(app.store.db_path)
            original_complete = app.store.complete_tool_execution
            conv = InvestigationCreationConversationService(tool_service=service, store=InvestigationStore(old.store.db_path),
                fake_runtime=False, hermes_state_dir=old.hermes_state_dir)
            configure_hermes_investigation_creation_tools(service, principal_provider=conv.principal_for_session)
            followup = True
            turn('followup', f'刚才回复没有完整显示。请核查那份{label}到底保存成功没有，叫什么、保存的是哪个版本？不要重复保存，也不要修改。')
            after = snapshot(stack)
            write(folder/'after-followup.json', after)
            check('followup leaves all resource versions and receipts unchanged', after == first)
            check('model followup only reads', not any(c['followup'] and c['tool'] in {
                'save_resource', 'save_draft_ruleset', 'save_draft_lexicon', 'update_resource_edit',
                'create_ruleset_proposal', 'create_lexicon_edit'} for c in record['calls']))
            check('both turns completed', all(t.get('status') == 'completed' for t in record['turns']))
            record['status'] = 'PASS' if all(c['passed'] for c in record['checks']) else 'FAIL'
            conv.close()
        except Exception as exc:
            record.update(status='ERROR', error=str(exc))
        finally:
            try:
                next(fixture)
            except StopIteration:
                pass
            persist()
    evidence['status'] = 'PASS' if all(c.get('status') == 'PASS' for c in evidence['cases']) else 'FAIL'
    persist()
    print(json.dumps({'status': evidence['status'], 'evidence': str(root/'evidence.json')}))
    return 0 if evidence['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
