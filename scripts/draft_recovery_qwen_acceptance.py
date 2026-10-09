"""Opt-in real Qwen draft recovery acceptance, with fresh synthetic databases.

Setup uses existing fixture tools and public presentation records. Only the
acceptance turns use real Qwen. Faults are injected at documented boundaries;
model arguments, prompts, tool definitions and recovery code are not rewritten.
No worker is started. Evidence distinguishes harness probes from model calls.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coordinator-env', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', default=['stale_rule', 'stale_lexicon', 'cross_entry', 'preview'])
    parser.add_argument('--max-calls', type=int, default=40)
    parser.add_argument('--source-root', type=Path, help='Read-only baseline source tree for identical acceptance')
    parser.add_argument('--legacy-checkpoint-history', action='store_true',
                        help='Seed pre-fix history in the disposable model_reply case and recreate the conversation service')
    args = parser.parse_args()
    os.umask(0o077)
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = args.source_root.resolve() if args.source_root else Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(repo), str(repo/'tests')]
    from dotenv import dotenv_values
    env = dotenv_values(args.coordinator_env)
    key = env.get('DASHSCOPE_API_KEY')
    if not key:
        raise SystemExit('Coordinator credential missing')
    inherited = {k: os.environ[k] for k in ('HOME', 'PATH', 'LANG', 'LC_ALL', 'TMPDIR') if k in os.environ}
    os.environ.clear()
    os.environ.update(inherited)
    os.environ.update({k: env[k] for k in ('DASHSCOPE_API_KEY', 'DASHSCOPE_BASE_URL') if env.get(k)})
    os.environ.update(PYTHON_DOTENV_DISABLED='1', PYTHONDONTWRITEBYTECODE='1', APP_AUTH_MODE='disabled')
    for name, relative in {
        'XHS_AUDIT_DATA_DIR': 'globals/data', 'XHS_AUDIT_OUTPUTS_DIR': 'globals/outputs',
        'APP_AUTH_DB': 'globals/auth.sqlite3', 'HERMES_HOME': 'globals/hermes',
        'CRAWLER_AUTH_KEY_FILE': 'globals/crawler.key', 'MEDIACRAWLER_DIR': 'disabled-crawler',
        'MEDIACRAWLER_REQUEST_SCHEDULER_DB': 'globals/scheduler.sqlite3',
    }.items():
        os.environ[name] = str(root/relative)

    from openai.resources.chat.completions import Completions
    from backend.investigation_creation.principal import Principal
    from backend.investigation_creation.tools import configure_hermes_investigation_creation_tools, HermesToolExecutionIdentity
    from test_investigation_creation_conversation import creation_stack, _run_scripted_creation_turn
    from test_draft_creation_recovery import arguments
    from test_ruleset_proposal_approval import creation
    from test_ruleset_proposal_presentation import evidence as presentation
    from backend.hermes_runtime.service import HermesInvestigationAgentService

    p = Principal('principal-a')
    data = {'status': 'RUNNING', 'model_calls': [], 'cases': [], 'limitations': [
        'Synthetic resources and scripted preparation; real Qwen acceptance turns only',
        'Cross-entry duplicate is harness-injected, not a claim that Qwen chose both tools',
        'Fault injection does not simulate provider outage or physical power loss',
        'No crawler, deployment, real report or production database']}

    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str).replace(key, '[REDACTED]')+'\n')
    def persist():
        write(root/'evidence.json', data)
    def check(record, name, result):
        record.setdefault('checks', []).append({'name': name, 'passed': bool(result)})
        persist()
    original_request = Completions.create
    def request(client, *a, **kw):
        if len(data['model_calls']) >= args.max_calls:
            raise RuntimeError('Acceptance model request budget exhausted')
        item = {'model': kw.get('model'), 'stream': bool(kw.get('stream')), 'case': data['cases'][-1]['name']}
        data['model_calls'].append(item)
        write(root/f'model-request-{len(data["model_calls"]):03d}.json', kw.get('messages'))
        persist()
        if case == 'model_reply' and not followup and snapshot()['investigation_drafts']:
            from openai import APIConnectionError
            import httpx
            item['injected_transport_failure'] = True
            record['faults'].append('Provider connection unavailable after draft commit for remainder of first turn')
            persist()
            raise APIConnectionError(request=httpx.Request('POST', 'https://acceptance.invalid/chat/completions'))
        try:
            result = original_request(client, *a, **kw)
            if not kw.get('stream'):
                item['response_id'] = result.id
                item['usage'] = result.usage.model_dump() if result.usage else None
                persist()
            return result
        except Exception as exc:
            item['error'] = str(exc)
            persist()
            raise

    for case in args.cases:
        case_dir = root/case
        case_dir.mkdir()
        record = {'name': case, 'calls': [], 'harness_probes': [], 'faults': [], 'turns': []}
        followup = False
        data['cases'].append(record)
        fixture = creation_stack.__wrapped__(case_dir)
        stack = next(fixture)
        app, conv, service = stack['app_service'], stack['conversation'], stack['tool_service']
        content = json.loads((repo/'tests/fixtures/recruitment_fraud_ruleset.json').read_text())
        content['name'] = '招聘收费风险验收规则'
        manager = app.resource_management
        def snapshot():
            with app.store._connect() as db:
                return {name: [dict(row) for row in db.execute('SELECT * FROM '+name)] for name in (
                    'investigation_drafts', 'investigation_draft_revisions', 'investigation_runs',
                    'ruleset_proposal_approvals', 'draft_creation_operations',
                    'draft_creation_attempts', 'investigation_creation_tool_receipts')}
        try:
            if case == 'stale_rule':
                manager.save_library('ruleset', 'ruleset.acceptance', content, 0, 'seed', principal=p)
                seed = _run_scripted_creation_turn(stack, content='看看已保存的招聘收费风险验收规则，下一步准备在抖音调查招聘收费。',
                    actions=[('read_resource', {'kind': 'ruleset', 'resource_id': 'ruleset.acceptance'}),
                             ('create_lexicon_edit', {'content': {'title': '招聘收费验收词库', 'entries': [
                                 {'id': 'topic', 'kind': 'main', 'term': '招聘风险'},
                                 {'id': 'variant', 'kind': 'variant', 'parent_id': 'topic', 'term': '招聘收费'}]}})],
                    final_response='已读取审核规则并准备好临时词库。')
                user = '确认用刚才已保存的招聘收费风险验收规则和已准备的招聘收费验收词库，在抖音只创建一个调查草案。词库直接用已有编辑稿，先不启动、不新建规则或词库。规则只发生内部排序变化时仍采用同一套内容。'
            else:
                seed = _run_scripted_creation_turn(stack, content='准备招聘收费风险调查，先展示临时规则和词库，不创建草案。',
                    actions=[('create_ruleset_proposal', {'content': content}),
                             ('create_lexicon_edit', {'content': {'title': '招聘收费验收词库', 'entries': [
                                 {'id': 'topic', 'kind': 'main', 'term': '招聘风险'},
                                 {'id': 'variant', 'kind': 'variant', 'parent_id': 'topic', 'term': '招聘收费'}]}})],
                    final_response='临时审核规则与黑话库已准备，请确认是否采用。')
                user = '确认采用刚才完整展示的招聘收费风险验收规则和招聘收费验收词库，在抖音创建一个调查草案。词库直接用已有编辑稿，规则保持临时，不正式保存，不启动任务。'
            session = seed['session_id']
            if case != 'stale_rule':
                shown = presentation(seed)
                with app.store._connect() as db:
                    rows = db.execute("SELECT response_json FROM investigation_creation_tool_receipts WHERE tool_name='create_lexicon_edit'").fetchall()
                edit = json.loads(rows[-1]['response_json'])['data']
            conv._agents.clear()
            conv.fake_runtime = False
            configure_hermes_investigation_creation_tools(service, principal_provider=conv.principal_for_session)
            record['session_id'] = session
            original_execute = service.execute_with_identity
            original_preview = app.resource_service.confirmation_preview
            faulted = False
            preview_failed = False

            def preview(*a, **kw):
                nonlocal preview_failed
                if case == 'preview' and not preview_failed and snapshot()['investigation_drafts']:
                    preview_failed = True
                    record['faults'].append('Raise once after committed draft, before confirmation preview')
                    persist()
                    raise RuntimeError('Acceptance injected preview transport failure after commit')
                return original_preview(*a, **kw)

            def execute(name, arguments_, **kw):
                nonlocal faulted
                item = {'tool': name, 'arguments': deepcopy(arguments_)}
                record['calls'].append(item)
                persist()
                if name == 'confirm_and_queue_investigation':
                    raise AssertionError('No task start authorized in acceptance')
                creating = name == 'create_investigation_draft' or (name == 'use_ruleset_proposal' and arguments_.get('create_draft'))
                if creating and not faulted:
                    if case == 'stale_rule':
                        updated = deepcopy(content)
                        for category in updated['categories']:
                            category['order'] += 100
                        manager.save_library('ruleset', 'ruleset.acceptance', updated, 1, 'race-save', principal=p)
                        record['faults'].append('Publish order-only v2 immediately before model creation call; model arguments unchanged')
                        faulted = True
                    elif case == 'stale_lexicon':
                        manager.update(edit['edit_id'], 1, [{'operation': 'set_metadata', 'values': {'title': '招聘收费验收词库（已同步）'}}],
                                       session_id=session, principal=p)
                        record['faults'].append('Advance lexicon metadata version immediately before model adoption call')
                        faulted = True
                result = original_execute(name, arguments_, **kw)
                item['result'] = deepcopy(result)
                if case == 'cross_entry' and creating and result['status'] == 'ok' and not faulted:
                    faulted = True
                    if name == 'use_ruleset_proposal':
                        other = 'create_investigation_draft'
                        command = arguments(stack)
                    else:
                        other = 'use_ruleset_proposal'
                        command = {'presentation_id': shown['presentation_id'], 'create_draft': creation()}
                    identity = kw['identity']
                    conflict = original_execute(other, command, principal=p,
                        identity=HermesToolExecutionIdentity(session, identity.turn_id, 'harness-second-entry'))
                    record['harness_probes'].append({'tool': other, 'arguments': command, 'result': conflict})
                    record['faults'].append('Inject alternate creation tool after committed first call; return real conflict to Qwen')
                    item['delivered_result'] = conflict
                    result = conflict
                persist()
                return result

            def turn(label, message):
                accepted, _ = conv.accept_message(session, client_message_id=label, content=message, principal=p)
                detail = {'label': label, 'input': message, 'id': accepted.id}
                record['turns'].append(detail)
                persist()
                output = io.StringIO()
                start = time.monotonic()
                original_validate = HermesInvestigationAgentService._validate_completed_transcript
                def validate(result, *, history, user_message):
                    expected = history or []
                    actual = result.get('messages') or []
                    write(case_dir/(label+'.history-validation.json'), {
                        'expected_history': expected, 'returned_messages': actual,
                        'user_message': user_message, 'final_response': result.get('final_response'),
                        'first_difference': next((i for i, (a, b) in enumerate(zip(expected, actual)) if a != b), None),
                    })
                    return original_validate(result, history=history, user_message=user_message)
                try:
                    with patch.object(Completions, 'create', request), patch.object(service, 'execute_with_identity', side_effect=execute), \
                         patch.object(app.resource_service, 'confirmation_preview', side_effect=preview), \
                         patch.object(HermesInvestigationAgentService, '_validate_completed_transcript', staticmethod(validate)), \
                         redirect_stdout(output), redirect_stderr(output):
                        result = conv.execute_turn(accepted.id)
                    saved = conv.store.get_turn(accepted.id)
                    detail.update(status=saved.status, answer=result.answer, artifact=saved.public_artifact, stop_reason=saved.stop_reason)
                    with conv.store._connect() as db:
                        row = db.execute('SELECT messages_json FROM investigation_hermes_transcripts WHERE turn_id=?', (accepted.id,)).fetchone()
                    transcript = json.loads(row['messages_json']) if row else []
                    write(case_dir/(label+'.transcript.json'), transcript)
                    # Read tools do not all pass through mutation receipt middleware.
                    # The actual runtime transcript is authoritative for model calls.
                    detail['tool_results'] = []
                    for message in transcript:
                        if message.get('role') == 'user':
                            detail['tool_results'] = []
                        elif message.get('role') == 'tool':
                            try:
                                payload = json.loads(message.get('content') or '{}')
                            except ValueError:
                                payload = {'raw': message.get('content')}
                            detail['tool_results'].append({'name': message.get('name'), 'result': payload})
                finally:
                    detail['seconds'] = round(time.monotonic()-start, 2)
                    write(case_dir/(label+'.runtime.json'), output.getvalue())
                    persist()
                print(json.dumps({'case': case, 'turn': label, 'status': detail['status'], 'seconds': detail['seconds'],
                    'calls': len(record['calls'])}, ensure_ascii=False), flush=True)

            turn('create', user)
            final = snapshot()
            write(case_dir/'database-snapshot.json', final)
            check(record, 'one draft', len(final['investigation_drafts']) == 1)
            check(record, 'no task started', len(final['investigation_runs']) == 0)
            if case.startswith('stale_'):
                failed = [c for c in record['calls'] if c.get('result', {}).get('error', {}).get('details', {}).get('write_status') == 'NOT_STARTED']
                succeeded = [c for c in record['calls'] if c['tool'] in ('create_investigation_draft', 'use_ruleset_proposal') and c.get('result', {}).get('status') == 'ok']
                check(record, 'fault reached known-no-write validation', bool(failed))
                check(record, 'model corrected within same user turn', bool(failed and succeeded))
            if case == 'cross_entry':
                probes = record['harness_probes']
                check(record, 'alternate entry rejected as committed', bool(probes) and probes[0]['result'].get('error', {}).get('details', {}).get('write_status') == 'COMMITTED')
                check(record, 'model read existing draft', any(c['name'] == 'get_investigation_draft' and c['result'].get('status') == 'ok'
                    for t in record['turns'] for c in t.get('tool_results', [])))
            if case in ('preview', 'model_reply'):
                if case == 'preview':
                    check(record, 'post-commit failure injected', preview_failed)
                    check(record, 'committed failure has original draft id', any(c.get('result', {}).get('error', {}).get('details', {}).get('write_status') == 'COMMITTED' for c in record['calls']))
                else:
                    check(record, 'post-commit model transport failure injected', bool(record['faults']))
                    check(record, 'application recovered durable success', record['turns'][0].get('stop_reason') == 'recovered_successful_application_checkpoint')
                legacy_row = None
                if args.legacy_checkpoint_history and case == 'model_reply':
                    # Only this newly created fixture DB is changed, never user history.
                    first_id = record['turns'][0]['id']
                    transcript = conv.store.latest_completed_hermes_transcript(session)
                    transcript.insert(-1, {'role': 'assistant', 'content':
                        '系统已从持久化检查点恢复以上成功操作。它们已经完成，不要因原回合失败而重复执行。'})
                    serialized = conv.store._hermes_transcript_json(transcript)
                    with conv.store._connect() as db:
                        db.execute('UPDATE investigation_hermes_transcripts SET messages_json=?, messages_sha256=? WHERE turn_id=?',
                            (serialized, hashlib.sha256(serialized.encode()).hexdigest(), first_id))
                        legacy_row = dict(db.execute('SELECT * FROM investigation_hermes_transcripts WHERE turn_id=?', (first_id,)).fetchone())
                    write(case_dir/'seeded-legacy-history.json', transcript)
                    from backend.investigation.store import InvestigationStore
                    from backend.investigation_creation.conversation import InvestigationCreationConversationService
                    old_conv = conv
                    old_conv.close()
                    conv = InvestigationCreationConversationService(tool_service=service,
                        store=InvestigationStore(old_conv.store.db_path), fake_runtime=False,
                        hermes_state_dir=old_conv.hermes_state_dir)
                    configure_hermes_investigation_creation_tools(service, principal_provider=conv.principal_for_session)
                    record['faults'].append('Seed old adjacent assistant history and recreate service/agent from SQLite')
                # A follow-up is a read-only recovery instruction, not a new create intent.
                followup = True
                turn('recover', '刚才没有拿到完整结果，请核查刚才草案的实际保存状态并展示已有草案，不要重新创建，也不要启动。')
                final = snapshot()
                write(case_dir/'database-snapshot-after-followup.json', final)
                check(record, 'follow-up retains exactly original draft', len(final['investigation_drafts']) == 1 and len(final['investigation_runs']) == 0)
                if legacy_row:
                    with conv.store._connect() as db:
                        current_row = dict(db.execute('SELECT * FROM investigation_hermes_transcripts WHERE turn_id=?', (legacy_row['turn_id'],)).fetchone())
                    check(record, 'original legacy history remains byte-for-byte unchanged', legacy_row == current_row)
                    conv.close()
            check(record, 'all model turns completed', all(t.get('status') == 'completed' for t in record['turns']))
            record['status'] = 'PASS' if all(c['passed'] for c in record['checks']) else 'FAIL'
        except Exception as exc:
            record.update(status='ERROR', error_type=type(exc).__name__, error=str(exc))
            import traceback
            write(case_dir/'error.json', traceback.format_exc())
        finally:
            try:
                next(fixture)
            except StopIteration:
                pass
            persist()
            print(json.dumps({'case': case, 'status': record['status'], 'checks': record.get('checks', []), 'error': record.get('error')}, ensure_ascii=False), flush=True)
    data['status'] = 'PASS' if all(c['status'] == 'PASS' for c in data['cases']) else 'FAIL'
    persist()
    return 0 if data['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
