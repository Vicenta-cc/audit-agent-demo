"""Opt-in real Qwen 1A/1B component acceptance; never starts a worker.

Provider credentials are read from explicit files; all stores are newly created.
The existing product prompts, model parameters, retries and tool results are used
unchanged. Selection requests are application API actions, not model tools.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from unittest.mock import patch
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coordinator-env', type=Path, required=True)
    parser.add_argument('--generation-env', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--max-calls', type=int, default=55)
    parser.add_argument('--resume', action='store_true', help='Continue the same isolated acceptance; preserve previous failures')
    parser.add_argument('--confirm-after-failure', action='store_true', help='Record an additional explicit user confirmation after the failed draft turn')
    args = parser.parse_args()
    os.umask(0o077)
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=args.resume)
    repo = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(repo), str(repo/'tests')]
    from dotenv import dotenv_values
    coordinator = dotenv_values(args.coordinator_env)
    generation = dotenv_values(args.generation_env)
    allowed = {k: v for source, keys in (
        (coordinator, ('DASHSCOPE_API_KEY', 'DASHSCOPE_BASE_URL')),
        (generation, ('RESOURCE_GENERATION_API_KEY', 'RESOURCE_GENERATION_BASE_URL', 'RESOURCE_GENERATION_MODEL')),
    ) for k in keys if (v := source.get(k))}
    secrets = [v for k, v in allowed.items() if k.endswith('API_KEY')]
    if len(secrets) != 2:
        raise SystemExit('Both provider credentials are required')
    # Allow only OS necessities into the isolated process. No inherited DB paths.
    inherited = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'VIRTUAL_ENV') if k in os.environ}
    os.environ.clear()
    os.environ.update(inherited)
    os.environ.update(allowed)
    os.environ.update(PYTHON_DOTENV_DISABLED='1', PYTHONDONTWRITEBYTECODE='1', APP_AUTH_MODE='disabled')
    for key, path in {
        'XHS_AUDIT_DATA_DIR': 'data', 'XHS_AUDIT_OUTPUTS_DIR': 'outputs',
        'APP_AUTH_DB': 'auth.sqlite3', 'HERMES_HOME': 'hermes',
        'CRAWLER_AUTH_KEY_FILE': 'data/crawler_auth.key',
        'MEDIACRAWLER_DIR': 'disabled-crawler',
        'MEDIACRAWLER_REQUEST_SCHEDULER_DB': 'scheduler.sqlite3',
    }.items():
        os.environ[key] = str(root/path)
    from backend.audit_agent.config import settings
    from backend.investigation_creation.principal import Principal
    from backend.investigation_creation.contracts import ConfirmAndQueueCommand
    from backend.investigation_creation.tools import configure_hermes_investigation_creation_tools
    from backend.resource_management.api import create_resource_router
    from backend.resource_management.session_state import SessionResourceReader
    from test_investigation_creation_conversation import creation_stack
    from openai import OpenAI
    from openai.resources.chat.completions import Completions

    evidence = dict(status='RUNNING', scope='real product Qwen resource tools + 1A/1B API components',
                    limitations=['1A/1B are not product Qwen tools yet', 'No real report generation or crawler',
                                 'Synthetic test principal; not production login acceptance',
                                 'HTTP endpoints exercised through FastAPI TestClient'],
                    calls=[], turns=[], checks=[], selection_actions=[])
    if args.resume:
        evidence = json.loads((root/'evidence.json').read_text())
        (root/f'evidence-before-resume-{int(time.time())}.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        evidence['status'] = 'RUNNING_WITH_RECORDED_FAILURE'
    def clean(value):
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        for secret in secrets:
            text = text.replace(secret, '[REDACTED]')
        return text
    def persist():
        (root/'evidence.json').write_text(clean(evidence)+'\n')
    def check(name, condition, **details):
        evidence['checks'].append(dict(name=name, passed=bool(condition), **details))
        persist()
        if not condition:
            raise AssertionError(name)
    original_create = Completions.create
    def observed_create(client, *a, **kw):
        if len(evidence['calls']) >= args.max_calls:
            raise RuntimeError('Acceptance request limit reached')
        record = dict(index=len(evidence['calls'])+1, model=kw.get('model'), stream=bool(kw.get('stream')),
                      host=urlsplit(str(client._client.base_url)).hostname,
                      tools=[v.get('function', {}).get('name') for v in kw.get('tools', [])])
        evidence['calls'].append(record)
        persist()
        def observe(response):
            usage = getattr(response, 'usage', None)
            if usage:
                record['usage'] = usage.model_dump()
            record['response_id'] = getattr(response, 'id', None)
            persist()
        try:
            response = original_create(client, *a, **kw)
            if not kw.get('stream'):
                observe(response)
                return response
            class ObservedStream:
                def __iter__(self):
                    for chunk in response:
                        observe(chunk)
                        yield chunk
                def __getattr__(self, name):
                    return getattr(response, name)
                def __enter__(self):
                    response.__enter__()
                    return self
                def __exit__(self, *exc):
                    return response.__exit__(*exc)
            return ObservedStream()
        except Exception as exc:
            record.update(error_type=type(exc).__name__, error=str(exc))
            persist()
            raise

    fixture = creation_stack.__wrapped__(root)
    stack = next(fixture)
    app, conv = stack['app_service'], stack['conversation']
    conv.fake_runtime = False
    principal = Principal('principal-a')
    configure_hermes_investigation_creation_tools(stack['tool_service'], principal_provider=conv.principal_for_session)
    stack['client'].app.include_router(create_resource_router(app, conv, stack['principals']))
    session = (conv.get_session(evidence['session'], principal=principal) if args.resume else
               conv.create_session(principal=principal, workspace_key='real-qwen-1a-1b'))
    evidence['session'] = session.id
    reader = SessionResourceReader(stack['creation_store'].db_path, stack['resource_db'], conv.store.db_path)
    paths = [reader.creation_db, reader.resource_db, reader.conversation_db]
    def dump():
        values = []
        for path in paths:
            with sqlite3.connect(path) as conn:
                values.append('\n'.join(conn.iterdump()))
        return values
    def state():
        before = dump()
        value = reader.read(session.id, principal=principal, limit=100)
        assert dump() == before, 'Read projection wrote to a database'
        return value
    def current(kind, letter):
        # Recover identities from actual successful tool receipts, never from a
        # fabricated name or a hand-written DB row. Naming fidelity is separate.
        label = '01-generate-a' if letter=='A' else '02-generate-b'
        tool = 'create_ruleset_proposal' if kind=='ruleset' else 'create_lexicon_edit'
        calls = [c for t in evidence['turns'] if t['label']==label for c in t['tool_calls']
                 if c['tool']==tool and c.get('result',{}).get('status')=='ok']
        identifiers = {c['result']['data'].get('proposal_id') or c['result']['data'].get('edit_id') for c in calls}
        matches = [v for v in state()['items'] if v['type']=='edit_version' and v['is_current']
                   and v['kind']==kind and v['id'] in identifiers]
        check(f'unique {kind} {letter}', len(matches)==1, count=len(matches))
        if f'验收{letter}' not in matches[0]['title']:
            issue = dict(kind=kind, requested_alias=letter, actual_title=matches[0]['title'], id=matches[0]['id'])
            if issue not in evidence.setdefault('naming_failures', []):
                evidence['naming_failures'].append(issue)
                persist()
        return matches[0]
    def counts():
        with stack['creation_store']._connect() as conn:
            return {t: conn.execute('SELECT count(*) FROM '+t).fetchone()[0]
                    for t in ('investigation_drafts', 'investigation_runs')}
    def turn(label, message):
        if args.resume:
            previous = next((t for t in evidence['turns'] if t['label']==label and t.get('status')=='completed'), None)
            if previous:
                return previous
        record = dict(label=label, input=message, tool_calls=[], before_counts=counts())
        evidence['turns'].append(record)
        persist()
        start = time.monotonic()
        original_execute = stack['tool_service'].execute_with_identity
        def execute(name, arguments, **kw):
            call = dict(tool=name, arguments=arguments)
            record['tool_calls'].append(call)
            persist()
            result = original_execute(name, arguments, **kw)
            call['result'] = result
            persist()
            return result
        accepted, _ = conv.accept_message(session.id, client_message_id=label, content=message, principal=principal)
        log = io.StringIO()
        with patch.object(stack['tool_service'], 'execute_with_identity', side_effect=execute), redirect_stdout(log), redirect_stderr(log):
            result = conv.execute_turn(accepted.id)
        saved = conv.store.get_turn(accepted.id)
        record.update(status=saved.status, answer=result.answer, native_tools=list(result.tool_names),
                      seconds=round(time.monotonic()-start, 2), artifact=saved.public_artifact,
                      counts=counts(), resource_state=state())
        (root/(label+'.runtime.txt')).write_text(clean(log.getvalue()))
        persist()
        print(json.dumps(dict(turn=label, status=saved.status, seconds=record['seconds'],
                              tools=[c['tool'] for c in record['tool_calls']], calls=len(evidence['calls'])), ensure_ascii=False), flush=True)
        check(label+' no unsolicited start', record['counts']['investigation_runs']==record['before_counts']['investigation_runs'])
        check(label+' conversation completed', saved.status=='completed')
        return record
    def choose(target, purpose, event, previous=''):
        body = dict(kind=target['kind'], purpose=purpose, key=target['key'], content_hash=target['content_hash'],
                    event_id=event, expected_event_id=previous)
        result = stack['client'].post(f'/api/investigation-workspaces/{session.id}/resource-selection', json=body)
        evidence['selection_actions'].append(dict(source='test harness explicit UI API request', request=body,
                                                  status=result.status_code, response=result.json()))
        persist()
        check('explicit selection '+event, result.status_code==200)
        return body
    def fresh_state():
        # New process, with SQL authorization denying access to all chat-history tables.
        script = '''import json,sys,sqlite3
from backend.resource_management.session_state import SessionResourceReader
from backend.investigation_creation.principal import Principal
original=sqlite3.connect
def connect(*a,**kw):
 c=original(*a,**kw)
 c.set_authorizer(lambda action,table,*unused: sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_READ and table in {'investigation_messages','investigation_turns','investigation_events'} else sqlite3.SQLITE_OK)
 return c
sqlite3.connect=connect
print(json.dumps(SessionResourceReader(*sys.argv[1:4]).read(sys.argv[4],principal=Principal('principal-a'),limit=100)))
'''
        before = dump()
        child = subprocess.run([sys.executable, '-B', '-c', script, *map(str, paths), session.id],
                               cwd=repo, capture_output=True, text=True, timeout=40, check=True)
        result = json.loads(child.stdout)
        check('fresh process read has no writes', before==dump())
        (root/'recovered-state.json').write_text(clean(result))
        return result
    try:
        with patch.object(Completions, 'create', observed_create):
            if args.confirm_after_failure:
                if not args.resume:
                    raise ValueError('--confirm-after-failure requires --resume')
                a = current('ruleset', 'A')
                b = current('ruleset', 'B')
                b3 = b
                check('recovery starts from B v3 with no draft', b['version']==3 and counts()['investigation_drafts']==0)
                turn('06b-additional-confirmation', '确认采用刚刚完整展示的B规则v3并创建草案。平台抖音，搜索词就是克拉玛依出租车、克拉玛依停车，不搜索变体。规则继续用临时版本，不保存，不启动。')
            else:
                turn('01-generate-a', '请生成一套临时审核规则，名称为“验收A旅游服务规则”，检查克拉玛依旅游服务投诉，涵盖酒店收费不透明、景点排队服务。共3条规则，明确普通询价和客观体验分享不算风险。同时生成独立临时关键词库“验收A旅游词库”，两个主词：克拉玛依酒店、克拉玛依景点，各一个变体。完整展示，先不保存，不创建草案，不启动任务。')
                a = current('ruleset', 'A'); la = current('lexicon', 'A')
                turn('02-generate-b', '保留刚才的A不动。另生成一套独立临时规则“验收B交通服务规则”，关注克拉玛依旅游交通投诉，只有3条：出租车绕路、停车收费不透明、接驳车排队；客观询价和单纯旅游分享不算风险。同时独立生成临时词库“验收B交通词库”，两个主词克拉玛依出租车和克拉玛依停车，每个一个变体。不要保存，不创建草案，不启动。')
                b = current('ruleset', 'B'); lb = current('lexicon', 'B')
                check('A and B independent', a['id']!=b['id'] and la['id']!=lb['id'])
                check('generation did not invent explicit selection', state()['selection']=={'status':'unknown'})
                choose(b, 'edit', 'choose-b-v1')
                choose(a, 'view', 'view-a-v1')
                turn('03-save-a', '现在只把“验收A旅游服务规则”和“验收A旅游词库”正式保存到后台资源库。B规则和B词库保持临时，不要采用，不要创建调查草案或启动。')
                receipts = [v for v in state()['items'] if v['type']=='save_receipt']
                check('only A saved', {v['edit_id'] for v in receipts}=={a['id'],la['id']}, receipts=receipts)
                check('save A does not move B selection', next(v for v in state()['selection']['slots'] if v['purpose']=='edit')['key']==b['key'])
                turn('04-edit-b-v2', '继续修改尚未保存的“验收B交通服务规则”：只把规则集描述改为“关注国庆期间克拉玛依旅游交通服务的真实投诉”。其余规则原样保留，名称不变。修改原来的B编辑稿并完整展示，不要另建、不保存、不创建调查。')
                check('B v2 same identity', current('ruleset','B')['id']==b['id'] and current('ruleset','B')['version']==2)
                check('old edit choice stale not silently upgraded', next(v for v in state()['selection']['slots'] if v['purpose']=='edit')['status']=='stale')
                turn('05-edit-b-v3', '仍修改原来的临时“验收B交通服务规则”：只把规则集描述改为“关注国庆克拉玛依出租车、停车、接驳服务投诉，普通询价与中性分享不纳入风险”。其余不变，完整展示最新内容。不要保存，不要创建草案，不启动。')
                b3 = current('ruleset','B')
                check('B v3 same identity', b3['id']==b['id'] and b3['version']==3)
                choose(b3, 'edit', 'choose-b-v3', 'choose-b-v1')
                turn('06-draft-b-v3', '我确认使用刚才完整展示的“验收B交通服务规则”当前临时版本，不使用A。请创建克拉玛依国庆交通服务调查草案，平台抖音，只用“验收B交通词库”的两个主词搜索，不用变体，先保留临时，不保存到资源库。这一步只创建草案，不启动任务。')
            draft_items = [v for v in state()['items'] if v['type']=='draft_revision']
            check('one draft created', counts()['investigation_drafts']==1 and bool(draft_items))
            draft = max(draft_items, key=lambda v:v['version'])
            check('draft really adopted B v3', draft['ruleset'].get('proposal_id')==b['id'] and draft['ruleset'].get('proposal_version')==3)
            # Explicit fixture action: real application freeze, no worker or crawling.
            task = app.confirm_and_queue(ConfirmAndQueueCommand(draft_id=draft['id'], expected_revision=draft['version'],
                confirmed=True, idempotency_key='acceptance-freeze-b-v3'), principal=principal)
            evidence['freeze'] = dict(source='test harness application confirm; no model and no worker', run_id=task.id)
            frozen_before = reader.detail(session.id, 'run/'+task.id, principal=principal)
            turn('07-edit-b-v4', '继续编辑临时“验收B交通服务规则”，只把描述改为“后续工作稿：继续关注克拉玛依交通服务投诉”。保留其余规则和名称不变，不保存。不要修改已经确认的任务配置，不创建新调查，不再次启动。请完整展示这个编辑稿。')
            b4 = current('ruleset','B')
            check('B current v4', b4['id']==b['id'] and b4['version']==4)
            check('task frozen snapshot unchanged', reader.detail(session.id, 'run/'+task.id, principal=principal)==frozen_before)
            choose(b4, 'edit', 'choose-b-v4', 'choose-b-v3')
            # Replay a committed old response must not roll selection back.
            replay = choose(b3, 'edit', 'choose-b-v3', 'choose-b-v1')
            check('old response replay did not roll back selection', next(v for v in state()['selection']['slots'] if v['purpose']=='edit')['key']==b4['key'])
            conv.close()
            recovered = fresh_state()
            check('recovered exact projection', recovered==state())
            # Component probe, explicitly NOT the product prompt/tool integration.
            with OpenAI(api_key=settings.dashscope_api_key, base_url=settings.dashscope_base_url, max_retries=0, timeout=120) as client:
                question = '这是隔离测试中新进程从数据库恢复的资源目录，不包含历史聊天。仅依据目录回答：A规则保存的是哪版，B当前编辑稿哪版，任务实际用B哪版，明确选择的编辑/查看对象各是谁，是否能把B当前稿当成任务版本。给出具体ID与版本；无法确定就说无法确定。不要执行任何写入。'
                answer = client.chat.completions.create(model=settings.qwen_text_model,
                    messages=[{'role':'user','content':question+'\n'+json.dumps(recovered,ensure_ascii=False)}])
                evidence['fresh_qwen_component_probe'] = dict(question=question, answer=answer.choices[0].message.content,
                    mode='direct real Qwen with recovered projection, no product prompt or chat history')
            before = dump()
            stack['principals'].current = Principal('other-user')
            denied = stack['client'].get(f'/api/investigation-workspaces/{session.id}/resource-state')
            stack['principals'].current = principal
            check('other principal denied without writes', denied.status_code==404 and dump()==before)
            evidence['status'] = 'COMPONENT_CHECKS_PASS_WITH_WORKFLOW_FAILURES' if evidence.get('naming_failures') or any(not c['passed'] for c in evidence['checks']) else 'COMPONENT_CHECKS_PASS_REQUIRES_MANUAL_MODEL_ANSWER_REVIEW'
    except Exception as exc:
        evidence.update(status='INCOMPLETE', error_type=type(exc).__name__, error=str(exc))
        try:
            evidence['last_state'] = state()
            fresh_state()
        except Exception as recovery_error:
            evidence['recovery_error'] = str(recovery_error)
        print(clean(dict(status=evidence['status'], error_type=type(exc).__name__, error=str(exc))), flush=True)
    finally:
        persist()
        next(fixture, None)
    print(clean(dict(status=evidence['status'], provider_calls=len(evidence['calls']), output=str(root))), flush=True)
    # A recovered later turn must not make a failed workflow green in CI.
    if evidence.get('naming_failures') or any(not c['passed'] for c in evidence['checks']):
        return 2
    return 0 if evidence['status'].startswith('COMPONENT_CHECKS_PASS') else 1


if __name__ == '__main__':
    raise SystemExit(main())
