"""Serial, alternating baseline/new M3 latency comparison with a real Qwen key.

The parent reads a hidden key once and passes it through child stdin. Each sample
imports one checkout in a fresh process and uses its disposable creation fixture.
No server, worker, crawler, production database or configuration is opened.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import getpass
import io
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BASELINE = 'cad3eabd583543fac27a45f211122e2ca924ed2b'
REQUESTS = {
    'existing_task': '我想调查小红书上的赌博博彩推广风险，帮我准备抓取审核任务配置，先不要开始执行。',
    'generate_rule': '请生成3条招聘押金诈骗推广审核规则，保护正常招聘与揭露诈骗的讨论。先完整展示，不保存，不创建或启动任务。',
}


def worker():
    args = json.loads(sys.stdin.read())
    repo = Path(args['repo'])
    sys.path[:0] = [str(repo), str(repo / 'tests')]
    from resource_experiment import environment
    with tempfile.TemporaryDirectory(prefix='m3-latency-sample-') as directory:
        temp = Path(directory)
        env = environment(temp)
        os.environ.clear()
        os.environ.update(env)
        from backend.audit_agent.config import settings
        settings.dashscope_api_key = args['key']
        settings.dashscope_base_url = args['base_url']
        from test_investigation_creation_conversation import creation_stack
        from backend.investigation_creation.principal import Principal
        from backend.investigation_creation.tools import configure_hermes_investigation_creation_tools, M3_TOOL_INPUTS
        from openai.resources.chat.completions import Completions
        fixture = creation_stack.__wrapped__(temp)
        stack = next(fixture)
        conversation = stack['conversation']
        conversation.fake_runtime = False
        configure_hermes_investigation_creation_tools(stack['tool_service'], principal_provider=conversation.principal_for_session)
        principal = Principal('principal-a')
        session = conversation.create_session(principal=principal)
        result = {'variant': args['variant'], 'case': args['case'], 'pair': args['pair'],
                  'tool_schema_count': len(M3_TOOL_INPUTS), 'provider': [], 'tools': [], 'status': 'INCOMPLETE'}
        original = Completions.create
        execute = stack['tool_service'].execute
        started = time.perf_counter()

        def completion(client, *a, **kw):
            if len(result['provider']) >= 12:
                raise RuntimeError('Latency sample provider call budget reached')
            assert kw.get('model') == 'qwen3.7-plus'
            kw['extra_body'] = {**kw.get('extra_body', {}), 'enable_thinking': True}
            kw['max_tokens'] = 6000
            if kw.get('stream'):
                kw['stream_options'] = {'include_usage': True}
            tick = time.perf_counter()
            call = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'cached_tokens': 0}
            result['provider'].append(call)
            def usage(value):
                if value:
                    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                        call[key] = int(getattr(value, key, 0) or 0)
                    call['cached_tokens'] = int(getattr(getattr(value, 'prompt_tokens_details', None), 'cached_tokens', 0) or 0)
            answer = original(client, *a, **kw)
            if not kw.get('stream'):
                usage(answer.usage)
                call['seconds'] = round(time.perf_counter() - tick, 3)
                return answer
            def chunks():
                try:
                    for chunk in answer:
                        if chunk.choices and 'first_delta_seconds' not in call:
                            delta = chunk.choices[0].delta
                            if delta.content or delta.tool_calls or getattr(delta, 'reasoning_content', None):
                                call['first_delta_seconds'] = round(time.perf_counter() - tick, 3)
                        usage(chunk.usage)
                        yield chunk
                finally:
                    answer.close()
                    call['seconds'] = round(time.perf_counter() - tick, 3)
            return chunks()

        def tool(*a, **kw):
            tick = time.perf_counter()
            try:
                return execute(*a, **kw)
            finally:
                result['tools'].append({'name': a[0], 'seconds': round(time.perf_counter() - tick, 4)})
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), patch.object(Completions, 'create', completion), patch.object(stack['tool_service'], 'execute', side_effect=tool):
                accepted, _ = conversation.accept_message(session.id, client_message_id='latency', content=REQUESTS[args['case']], principal=principal)
                completed = conversation.execute_turn(accepted.id)
            result['wall_seconds'] = round(time.perf_counter() - started, 3)
            saved = conversation.store.get_turn(accepted.id)
            result['answer'] = completed.answer
            artifact = saved.public_artifact or {}
            with stack['creation_store']._connect() as conn:
                result['drafts'] = conn.execute('SELECT count(*) FROM investigation_drafts').fetchone()[0]
                result['runs'] = conn.execute('SELECT count(*) FROM investigation_runs').fetchone()[0]
            assert saved.status == 'completed' and result['runs'] == 0
            if args['case'] == 'existing_task':
                assert result['drafts'] == 1
                assert artifact['draft']['configuration']['judgement']['strategy'] == 'existing_ruleset'
                assert artifact['draft']['configuration']['investigation']['recall_plan']['strategy'] == 'existing_lexicon'
            else:
                assert result['drafts'] == 0
                assert artifact['proposal_presentations']
                assert sum(len(c['rules']) for c in artifact['proposal_presentations'][0]['snapshot']['content']['categories']) == 3
            result['status'] = 'PASS'
        except Exception as exc:
            result.update(failure_type=type(exc).__name__, failure=str(exc).replace(args['key'], '[redacted]'))
            result.setdefault('wall_seconds', round(time.perf_counter() - started, 3))
        finally:
            next(fixture, None)
        print(json.dumps(result, ensure_ascii=False).replace(args['key'], '[redacted]'))


def summarize(samples):
    summary = {}
    for case in REQUESTS:
        all_groups = {v: [s for s in samples if s['case'] == case and s['variant'] == v] for v in ('baseline', 'new')}
        groups = {v: [s for s in rows if s['status'] == 'PASS'] for v, rows in all_groups.items()}
        result = {v: {'attempted_samples': len(all_groups[v]), 'successful_samples': len(rows),
                      'wall_seconds': [r['wall_seconds'] for r in rows],
                      'median_seconds': statistics.median(r['wall_seconds'] for r in rows) if rows else None,
                      'provider_calls': [len(r['provider']) for r in rows],
                      'tool_seconds': [round(sum(t['seconds'] for t in r['tools']), 4) for r in rows]} for v, rows in groups.items()}
        pairs = sorted({s['pair'] for s in groups['baseline']} & {s['pair'] for s in groups['new']})
        paired = {'pair_ids': pairs, 'count': len(pairs)}
        if pairs:
            for variant in groups:
                values = [s['wall_seconds'] for s in groups[variant] if s['pair'] in pairs]
                paired[variant + '_median_seconds'] = statistics.median(values)
            paired['median_change_seconds'] = round(paired['new_median_seconds'] - paired['baseline_median_seconds'], 3)
            paired['median_change_percent'] = round((paired['new_median_seconds'] / paired['baseline_median_seconds'] - 1) * 100, 1)
        result['matched_successful_pairs'] = paired
        summary[case] = result
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--base-url', default='https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
    parser.add_argument('--pairs', type=int, default=3)
    parser.add_argument('--output', default='qwen-latency-comparison-20260911.json')
    args = parser.parse_args()
    if args.worker:
        return worker()
    key = getpass.getpass('Experiment API key (hidden): ').replace('\\_', '_').strip()
    evidence = {'baseline': BASELINE, 'new_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'new_has_uncommitted_changes': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT)),
                'model': 'qwen3.7-plus', 'thinking': True, 'pairs': args.pairs, 'samples': [],
                'method': 'serial alternating baseline/new; fresh process/session/database each sample; identical request and provider settings; cache not forced',
                'limits': 'small sample; first delta includes model thinking/tool output, not guaranteed user-visible text; no p95 claim'}
    target = ROOT / 'docs/evidence/m3-resource-lifecycle' / args.output
    def persist():
        target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2).replace(key, '[redacted]') + '\n')
    with tempfile.TemporaryDirectory(prefix='m3-latency-baseline-') as directory:
        old = Path(directory)
        archive = subprocess.check_output(['git', 'archive', BASELINE], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(old, filter='data')
        for case in REQUESTS:
            for pair in range(1, args.pairs + 1):
                for variant in (('baseline', 'new') if pair % 2 else ('new', 'baseline')):
                    spec = {'repo': str(old if variant == 'baseline' else ROOT), 'key': key, 'base_url': args.base_url,
                            'variant': variant, 'case': case, 'pair': pair}
                    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker'], input=json.dumps(spec), text=True, capture_output=True, timeout=360)
                    try:
                        record = json.loads(child.stdout)
                    except ValueError:
                        record = {'variant': variant, 'case': case, 'pair': pair, 'status': 'ERROR', 'returncode': child.returncode}
                    evidence['samples'].append(record)
                    persist()
                    print(json.dumps({k: record.get(k) for k in ('case', 'pair', 'variant', 'status', 'wall_seconds')}, ensure_ascii=False), flush=True)
    evidence['summary'] = summarize(evidence['samples'])
    evidence['status'] = 'PASS' if all(s['status'] == 'PASS' for s in evidence['samples']) else 'INCOMPLETE'
    persist()
    print(json.dumps(evidence['summary'], ensure_ascii=False), flush=True)
    if evidence['status'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
