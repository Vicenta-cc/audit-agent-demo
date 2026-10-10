"""Bounded non-thinking answer-stage ablation using recorded synthetic requests.

No live application state, no tool execution, no answer repair. A new tool request
is preserved and reported as incomplete, never forced into a final answer.
Requires CONVERSATION_API_KEY/BASE_URL/MODEL in the process environment.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import time
from urllib.parse import urlsplit
from uuid import uuid4


CASES = [
    ('count_failure_1', 'paired', 'full-1', 'unique_comment'),
    ('count_failure_2', 'paired', 'full-2', 'unique_comment'),
    ('b_only', 'repaired', 'full-1', 'b_subset'),
    ('excluded_version', 'repaired', 'compact-1', 'metadata_exclusion'),
    ('unknown_identity', 'repaired', 'full-1', 'unknown_identity'),
    ('ordinal', 'repaired', 'full-1', 'ordinal_followup'),
    ('comparison', 'repaired', 'full-1', 'read_then_compare'),
    ('high_filter', 'repaired', 'full-1', 'filtered_accounts'),
]


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def digest(value):
    return sha256(dumps(value).encode()).hexdigest()


def without_old_answers(request, question):
    result = deepcopy(request)
    messages = result['messages']
    indices = [i for i, m in enumerate(messages)
               if m.get('role') == 'user' and m.get('content') == question]
    if len(indices) != 1:
        raise ValueError('Current question must identify exactly one message')
    boundary = indices[0]
    removed = [i for i, m in enumerate(messages) if i < boundary
               and m.get('role') == 'assistant' and not m.get('tool_calls')]
    result['messages'] = [m for i, m in enumerate(messages) if i not in removed]
    return result, removed


def prepare(root):
    cases = []
    for label, folder, mode, case in CASES:
        base = root / f'report-context-20261010-{folder}'
        evidence = json.loads((base / 'evidence.json').read_text())
        t = next(t for t in evidence['turns'] if t['mode'] == mode and t['case'] == case)
        final_call = evidence['calls'][t['call_start'] + t['model_calls'] - 1]
        path = base / final_call['request_file']
        request = json.loads(path.read_text())
        if request.get('extra_body', {}).get('thinking') != {'type': 'disabled'}:
            raise ValueError('Refusing a source request without explicit thinking disabled')
        if request.get('temperature') is None:
            request.pop('temperature', None)
        projected, removed = without_old_answers(request, t['question'])
        assert request['tools'] == projected['tools']
        # No evidence, user instruction, or tool-call arguments are altered.
        for role in ('system', 'user', 'tool'):
            assert [m for m in request['messages'] if m['role'] == role] == [
                m for m in projected['messages'] if m['role'] == role]
        assert [m for m in request['messages'] if m.get('tool_calls')] == [
            m for m in projected['messages'] if m.get('tool_calls')]
        assert removed
        cases.append(dict(case=label, source_file=str(path), source_sha256=sha256(path.read_bytes()).hexdigest(),
            question=t['question'], original_answer=t['result']['answer'], removed_message_indices=removed,
            preserved_system_sha256=digest([m for m in request['messages'] if m['role'] == 'system']),
            preserved_tools_sha256=digest(request['tools']),
            full=request, no_old_answers=projected))
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acceptance-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeat', type=int, choices=[1, 2, 3], default=3)
    args = parser.parse_args()
    os.umask(0o077)
    cases = prepare(args.acceptance_root)
    model = os.environ['CONVERSATION_MODEL']
    if any(c['full']['model'] != model for c in cases):
        raise ValueError('Configured model must match original request model')
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    jobs = []
    rng = random.Random(1010)
    for repeat in range(args.repeat):
        order = list(range(len(cases)))
        rng.shuffle(order)
        for i in order:
            variants = ['full', 'no_old_answers'] if (i + repeat) % 2 else ['no_old_answers', 'full']
            for variant in variants:
                jobs.append((uuid4().hex[:12], i, repeat + 1, variant))
    meta = dict(status='RUNNING', model=model, host=urlsplit(os.environ['CONVERSATION_BASE_URL']).hostname,
        planned_calls=len(jobs), thinking='disabled', concurrency=2, automatic_retries=0,
        max_output_tokens=4096, production_changed=False,
        limitations=[
            'Answer-stage replay, not live agent acceptance; recorded tools are unchanged and available.',
            'New tool calls are not executed and count as incomplete.',
            'Only older assistant messages without tool_calls are removed; all original evidence remains.',
            'Four failure-selected cases and four controls, not representative production sampling.',
            'Excluded-version case already contains compact tool history; no uniform context claim.',
            'Deleting past prose may lose displayed ordering or commitments; no rollout authorized by this result.',
        ], cases=[{k: v for k, v in c.items() if k not in ('full', 'no_old_answers')} for c in cases], records=records)
    def save():
        (args.output / 'evidence.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    save()
    from openai import OpenAI
    def run(job):
        identifier, index, repeat, variant = job
        case = cases[index]
        request = deepcopy(case[variant])
        request.update(stream=False, max_tokens=4096)
        (args.output / f'request-{identifier}.json').write_text(json.dumps(request, ensure_ascii=False, indent=2))
        record = dict(id=identifier, case=case['case'], repeat=repeat, variant=variant,
            request_file=f'request-{identifier}.json', question=case['question'],
            input_characters=len(dumps(request['messages'])))
        started = time.monotonic()
        try:
            with OpenAI(api_key=os.environ['CONVERSATION_API_KEY'], base_url=os.environ['CONVERSATION_BASE_URL'],
                        timeout=60, max_retries=0) as client:
                response = client.chat.completions.create(**request)
            choice = response.choices[0]
            message = choice.message
            record.update(answer=message.content or '', finish_reason=choice.finish_reason,
                response_model=response.model, usage=response.usage.model_dump() if response.usage else None,
                tool_calls=[t.model_dump() for t in message.tool_calls or []],
                nonempty_reasoning_returned=bool(getattr(message, 'reasoning_content', None)))
            record['status'] = ('needs_tool' if message.tool_calls else 'completed'
                                if choice.finish_reason == 'stop' and message.content else 'incomplete')
            if record['nonempty_reasoning_returned']:
                record['status'] = 'unexpected_reasoning'
        except Exception as exc:
            # Don't serialize arbitrary SDK exception text or credentials.
            record.update(status='request_error', error_type=type(exc).__name__,
                          http_status=getattr(exc, 'status_code', None))
        record['seconds'] = round(time.monotonic() - started, 3)
        return record
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(run, job) for job in jobs]):
            record = future.result()
            records.append(record)
            save()
            print(json.dumps({k: record[k] for k in ('id', 'case', 'variant', 'repeat', 'status', 'seconds')}, ensure_ascii=False), flush=True)
    meta['status'] = 'REPLAY_COMPLETE_NEEDS_BLIND_ANSWER_REVIEW'
    save()
    blind = [{k: r[k] for k in ('id', 'case', 'question', 'status', 'answer', 'tool_calls') if k in r} for r in records]
    rng.shuffle(blind)
    (args.output / 'blind-answers.json').write_text(json.dumps(blind, ensure_ascii=False, indent=2))
    print(json.dumps({'status': meta['status'], 'calls': len(records)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
