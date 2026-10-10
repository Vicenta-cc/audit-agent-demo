"""One continuation of model-requested tools, using exact archived query matches.

Not live execution: only complete same-query saved responses can be replayed.
No arbitrary tool simulation, new evidence, answer checking, repair or retries.
"""
import argparse
import json
import os
from pathlib import Path
import time


def business(call):
    fn = call['function']
    args = json.loads(fn['arguments'])
    return (args['name'], args.get('arguments', {})) if fn['name'] == 'tool_call' else (fn['name'], args)


def query_identity(args):
    return (args.get('kind', 'comments'), tuple(sorted(args.get('report_refs', []))),
            args.get('account_ref') or '', args.get('risk_level') or '',
            bool(args.get('common_only')), args.get('from_result_ref') or '')


def saved_response(request, call):
    name, args = business(call)
    assert name in ('read_conversation_result', 'query_conversation_reports')
    calls = {}
    matches = []
    for msg in request['messages']:
        for original in msg.get('tool_calls') or []:
            calls[original['id']] = business(original)
        if msg.get('role') != 'tool':
            continue
        response = json.loads(msg['content'])
        if not isinstance(response, dict) or not response.get('ok'):
            continue
        data = response.get('data', {})
        if data.get('kind') not in ('comments', 'accounts'):
            continue
        if name == 'read_conversation_result':
            match = data.get('result_ref') == args['result_ref']
        else:
            prior_name, prior_args = calls.get(msg.get('tool_call_id'), ('', {}))
            # Explicit report refs only. Do not emulate inherited or broad scopes.
            match = (bool(args.get('report_refs')) and prior_name == name
                     and query_identity(prior_args) == query_identity(args))
        if not match:
            continue
        assert args.get('offset', 0) == 0 and data.get('has_more') is False
        assert data['next_offset'] == data['total_count'] <= args.get('limit', 20)
        if 'query_coverage' in data:
            assert data['query_coverage']['page_covers_complete_query']
        else:
            assert len(data['items']) == data['total_count']
        matches.append(msg['content'])
    assert matches, 'No exact complete archived result; refusing to invent a tool response'
    return matches[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    evidence = json.loads((args.output / 'evidence.json').read_text())
    jobs = []
    for record in evidence['records']:
        if record['status'] != 'needs_tool':
            continue
        request = json.loads((args.output / record['request_file']).read_text())
        responses = [saved_response(request, c) for c in record['tool_calls']]
        request['messages'].append(dict(role='assistant', content=record.get('answer') or None,
                                        tool_calls=record['tool_calls']))
        for call, response in zip(record['tool_calls'], responses):
            request['messages'].append(dict(role='tool', tool_call_id=call['id'], content=response))
        jobs.append((record, request))
    if args.dry_run:
        print(json.dumps({'exact_archive_matches': [r['id'] for r, _ in jobs]}))
        return
    from openai import OpenAI
    results = []
    path = args.output / 'continuations.json'
    assert not path.exists()
    for parent, request in jobs:
        assert request['extra_body']['thinking'] == {'type': 'disabled'}
        assert request['model'] == os.environ['CONVERSATION_MODEL']
        identifier = parent['id'] + '-continuation'
        (args.output / f'request-{identifier}.json').write_text(json.dumps(request, ensure_ascii=False, indent=2))
        record = dict(id=identifier, parent_id=parent['id'], arm=parent['arm'],
                      case=parent['case'], tool_delivery='exact_archived_response_not_live_query')
        started = time.monotonic()
        try:
            with OpenAI(api_key=os.environ['CONVERSATION_API_KEY'], base_url=os.environ['CONVERSATION_BASE_URL'],
                        timeout=60, max_retries=0) as client:
                response = client.chat.completions.create(**request)
            choice = response.choices[0]
            msg = choice.message
            record.update(answer=msg.content or '', tool_calls=[c.model_dump() for c in msg.tool_calls or []],
                usage=response.usage.model_dump() if response.usage else None,
                response_model=response.model, finish_reason=choice.finish_reason,
                nonempty_reasoning_returned=bool(getattr(msg, 'reasoning_content', None)))
            record['status'] = ('needs_tool' if msg.tool_calls else 'completed'
                                if choice.finish_reason == 'stop' and msg.content else 'incomplete')
            if record['nonempty_reasoning_returned']:
                record['status'] = 'unexpected_reasoning'
        except Exception as exc:
            record.update(status='request_error', error_type=type(exc).__name__)
        record['seconds'] = round(time.monotonic() - started, 3)
        results.append(record)
        path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(json.dumps({k: record[k] for k in ('id', 'status', 'seconds')}), flush=True)


if __name__ == '__main__':
    main()
