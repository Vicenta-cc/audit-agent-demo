"""Offline request projection + bounded non-thinking answer replay, never production.

All historical messages and tool capabilities stay present. The four arms isolate
result expression and a short factual-reading instruction. New tool requests are
saved as unfinished; no answer repair or hidden tool-choice restriction is used.
"""
import argparse
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import time
from urllib.parse import urlsplit

PROMPT = ('报告内引用标识一次收录，evidence_key 标识原始证据。计数区分原始内容和研判记录；'
          '筛选结果只代表实际查询条件；身份未知不能确认或排除。历史回答不作为事实证据，结论以工具数据为准。')
CORE = [('count', 'paired', 'full-1', 'unique_comment'),
        ('unknown', 'repaired', 'full-1', 'unknown_identity'),
        ('filter', 'repaired', 'full-1', 'filtered_accounts')]
CONTROLS = [('single', 'repaired', 'full-1', 'b_comments'),
            ('ordinal', 'repaired', 'full-1', 'ordinal_followup'),
            ('comparison', 'repaired', 'full-1', 'read_then_compare')]
ARMS = ('baseline', 'data', 'prompt', 'both')


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


def grouped(rows):
    groups = OrderedDict()
    for i, original in enumerate(rows):
        row = deepcopy(original)
        key = row.get('evidence_key') or ('unresolved', i)
        row['identity_status'] = 'resolved' if row.get('account_ref') else 'unknown'
        row['source_result_position'] = i
        groups.setdefault(key, []).append(row)
    return [{'original_evidence_key': key if isinstance(key, str) else None,
             'identity_relation': 'same_original_evidence' if isinstance(key, str) else 'unresolved',
             'visible_observation_count': len(values),
             'report_observations': values} for key, values in groups.items()]


def complete_page(data):
    return (data.get('has_more') is False and
            data.get('next_offset') == len(data.get('items', [])) == data.get('total_count'))


def project(request, *, express=False):
    """Normalize known statistics defect in ALL arms; optionally express facts.

Only complete synthetic account results qualify for normalization. Original
request files and durable stored results remain untouched. Historical prose is
never edited, including statements based on the old incorrect statistic.
"""
    result = deepcopy(request)
    calls, result_filters, edits = {}, {}, []
    for i, msg in enumerate(result['messages']):
        for call in msg.get('tool_calls') or []:
            calls[call['id']] = call['function']
        if msg.get('role') != 'tool':
            continue
        try:
            response = json.loads(msg['content'])
        except (ValueError, TypeError):
            continue
        if not isinstance(response, dict) or not response.get('ok'):
            continue
        data = response.get('data', {})
        if data.get('kind') not in ('accounts', 'comments') or 'items' not in data:
            continue
        func = calls.get(msg.get('tool_call_id'), {})
        args = json.loads(func.get('arguments', '{}'))
        # Hermes exposes business tools through its tool_call dispatcher.
        if func.get('name') == 'tool_call':
            func = {'name': args.get('name')}
            args = args.get('arguments') or {}
        filters = None
        if func.get('name') == 'query_conversation_reports':
            filters = {key: args.get(key) for key in
                       ('risk_level', 'account_ref', 'common_only', 'from_result_ref')}
            filters['parent_result_ref'] = data.get('parent_result_ref') or None
            filters['sources'] = deepcopy(data.get('sources', []))
            if data.get('result_ref'):
                result_filters[data['result_ref']] = deepcopy(filters)
        elif func.get('name') == 'read_conversation_result':
            filters = result_filters.get(args.get('result_ref'))
        if data['kind'] == 'accounts' and complete_page(data) and all(
                a.get('observations_truncated') is False for a in data['items']):
            rows = [r for a in data['items'] for r in a.get('observations', [])]
            if all(r.get('evidence_key') for r in rows):
                fixed = len({r['evidence_key'] for r in rows})
                if fixed != data.get('unique_evidence_count'):
                    edits.append({'message': i, 'old': data.get('unique_evidence_count'), 'new': fixed})
                    data['unique_evidence_count'] = fixed
        if express:
            data['query_coverage'] = {
                'actual_filters': filters,
                'filters_known': filters is not None,
                'page_covers_complete_query': complete_page(data),
                'counts_apply_to': 'selected_reports_with_query_and_parent_result_filters',
            }
            if data['kind'] == 'comments':
                data['total_count_unit'] = 'report_observations'
                data['items'] = grouped(data['items'])
                data['items_unit'] = 'original_evidence_groups_on_returned_page'
            else:
                data['total_count_unit'] = 'accounts_matching_query'
                for account in data['items']:
                    account['evidence_entities'] = grouped(account.pop('observations', []))
                data['query_coverage']['account_observation_previews_complete'] = all(
                    a.get('observations_truncated') is False for a in data['items'])
        msg['content'] = encoded(response)
    return result, edits


def prepare(root, cases):
    prepared = []
    for label, folder, mode, name in cases:
        base = root / f'report-context-20261010-{folder}'
        evidence = json.loads((base / 'evidence.json').read_text())
        turn = next(t for t in evidence['turns'] if t['mode'] == mode and t['case'] == name)
        call = evidence['calls'][turn['call_start'] + turn['model_calls'] - 1]
        source = base / call['request_file']
        original = json.loads(source.read_text())
        assert original['extra_body']['thinking'] == {'type': 'disabled'}
        if original.get('temperature') is None:
            original.pop('temperature', None)
        base_request, corrections = project(original)
        data_request, same_corrections = project(original, express=True)
        assert corrections == same_corrections
        arms = {}
        for arm in ARMS:
            request = deepcopy(data_request if arm in ('data', 'both') else base_request)
            if arm in ('prompt', 'both'):
                system = next(m for m in request['messages'] if m['role'] == 'system')
                system['content'] += '\n' + PROMPT
            assert request['tools'] == original['tools']
            assert [m for m in request['messages'] if m['role'] in ('user', 'assistant')] == [
                m for m in original['messages'] if m['role'] in ('user', 'assistant')]
            arms[arm] = request
        prepared.append(dict(case=label, question=turn['question'], source=str(source),
            source_sha256=sha256(source.read_bytes()).hexdigest(), corrections=corrections,
            historical_answer=turn['result']['answer'], arms=arms))
    return prepared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--controls', action='store_true')
    parser.add_argument('--candidate', choices=ARMS[1:], default='both')
    args = parser.parse_args()
    os.umask(0o077)
    cases = prepare(args.root, CONTROLS if args.controls else CORE)
    model = os.environ['CONVERSATION_MODEL']
    assert all(c['arms']['baseline']['model'] == model for c in cases)
    args.output.mkdir(parents=True, exist_ok=False)
    variants = ('baseline', args.candidate) if args.controls else ARMS
    jobs = [(i, r + 1, arm) for r in range(1 if args.controls else 2)
            for i in range(len(cases)) for arm in variants]
    random.Random(1011).shuffle(jobs)
    meta = dict(status='RUNNING', model=model,
        host=urlsplit(os.environ['CONVERSATION_BASE_URL']).hostname,
        thinking='disabled', prompt=PROMPT, prompt_characters=len(PROMPT),
        planned_calls=len(jobs), retries=0, concurrency=2, production_changed=False,
        limitations=['Answer-stage replay, not full conversation acceptance.',
                    'Historical user/assistant text, including old errors, is unchanged.',
                    'Known account-count defect normalized equally in all arms.',
                    'Unknown/filter source answers were previously correct controls, not three reproduced failures.',
                    'New tool requests are unfinished and not counted as correct answers.',
                    'Small synthetic fixtures and repeated questions do not measure production accuracy.'],
        rubric={
            'count': 'Exactly one original comment, two assessments, A low/B high; no conflicting identity claim.',
            'unknown': 'Cannot confirm or exclude shared account; no invented cause or definitive negative.',
            'filter': 'B has two high-risk commenters, zero high-risk publishers; no whole-report absence claim.',
            'single': 'B exactly b/shared comments and their correct accounts and sources.',
            'ordinal': 'Second previously shown B comment is shared, author account_819f2fda16130edd0dfbdda2.',
            'comparison': 'Shared original comment is A low/B high; preserve provenance, no causal certainty.'},
        cases=[{k: v for k, v in c.items() if k != 'arms'} for c in cases], records=[])
    def save():
        (args.output / 'evidence.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    save()
    from openai import OpenAI
    def run(job):
        index, repeat, arm = job
        case = cases[index]
        identifier = f'{case["case"]}-{arm}-{repeat}'
        request = deepcopy(case['arms'][arm])
        request.update(stream=False, max_tokens=4096)
        (args.output / f'request-{identifier}.json').write_text(json.dumps(request, ensure_ascii=False, indent=2))
        record = dict(id=identifier, case=case['case'], arm=arm, repeat=repeat,
                      request_file=f'request-{identifier}.json', question=case['question'],
                      input_characters=len(encoded(request['messages'])))
        started = time.monotonic()
        try:
            with OpenAI(api_key=os.environ['CONVERSATION_API_KEY'], base_url=os.environ['CONVERSATION_BASE_URL'],
                        timeout=60, max_retries=0) as client:
                response = client.chat.completions.create(**request)
            choice = response.choices[0]
            msg = choice.message
            record.update(answer=msg.content or '', finish_reason=choice.finish_reason,
                tool_calls=[t.model_dump() for t in msg.tool_calls or []], response_model=response.model,
                usage=response.usage.model_dump() if response.usage else None,
                nonempty_reasoning_returned=bool(getattr(msg, 'reasoning_content', None)))
            record['status'] = ('needs_tool' if msg.tool_calls else 'completed'
                                if choice.finish_reason == 'stop' and msg.content else 'incomplete')
            if record['nonempty_reasoning_returned']:
                record['status'] = 'unexpected_reasoning'
        except Exception as exc:
            record.update(status='request_error', error_type=type(exc).__name__,
                          http_status=getattr(exc, 'status_code', None))
        record['seconds'] = round(time.monotonic() - started, 3)
        return record
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(run, job) for job in jobs]):
            record = future.result()
            meta['records'].append(record)
            save()
            print(encoded({k: record[k] for k in ('id', 'status', 'seconds')}), flush=True)
    meta['status'] = 'COMPLETE_PENDING_MANUAL_ANSWER_REVIEW'
    save()


if __name__ == '__main__':
    main()
