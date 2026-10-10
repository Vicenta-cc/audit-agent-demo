"""Reconstruct ORIGINAL failing scenes; preserve all historical evidence and bugs.

Stored transcripts omit the system envelope and transport bookkeeping. Rebuild
these from the unchanged original prompt assembly and a later captured envelope,
and validate reconstruction against a later transcript with an actual SDK dump.
This is a complete-history answer-boundary reproduction, NOT a full live session
rerun and NOT a claim of possessing the original byte-for-byte wire request.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import sqlite3
import time

from scripts.report_expression_trial import PROMPT as SEMANTIC_PROMPT, project
from backend.report_collection.tools import FOCUSED_ANSWER_INSTRUCTION

BRIEF_PROMPT = FOCUSED_ANSWER_INSTRUCTION
SCENES = [
    ('followup', 'report-query-autonomy-20261010-paired', 'investigation-turn:55485e540b084db8b433f26814431a89'),
    ('publisher', 'report-query-autonomy-20261010-single', 'investigation-turn:f9b1adcecab24227849a57661e85cb80'),
]


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def load_transcript(base, turn):
    with sqlite3.connect(f'file:{base}/turns.sqlite3?mode=ro', uri=True) as db:
        row = db.execute('SELECT messages_json,messages_sha256 FROM investigation_hermes_transcripts WHERE turn_id=?', (turn,)).fetchone()
    assert row
    return json.loads(row[0]), row[1]


def api_message(original):
    message = deepcopy(original)
    for key in ('timestamp', 'tool_name', 'reasoning', 'finish_reason'):
        message.pop(key, None)
    if message['role'] == 'assistant':
        # Hermes/DeepSeek transport supplies a blank compatibility field, not
        # generated chain-of-thought. Verified against captured SDK requests.
        message.setdefault('reasoning_content', ' ')
    for call in message.get('tool_calls') or []:
        call.pop('call_id', None)
        call.pop('response_item_id', None)
        call['function']['arguments'] = json.dumps(json.loads(call['function']['arguments']),
            ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return message


def envelope(root):
    base = root / 'report-context-20261010-paired'
    evidence = json.loads((base/'evidence.json').read_text())
    turn = next(t for t in evidence['turns'] if t['mode'] == 'full-1' and t['case'] == 'unique_comment')
    call = evidence['calls'][turn['call_start'] + turn['model_calls'] - 1]
    path = base / call['request_file']
    request = json.loads(path.read_text())
    transcript, _ = load_transcript(base, turn['turn_id'])
    rebuilt = [request['messages'][0]] + [api_message(m) for m in transcript[:-1]]
    assert rebuilt == request['messages'], 'Reconstruction must exactly match known captured SDK input'
    from backend.hermes_runtime.creation_prompt import CREATION_IDENTITY
    from backend.report_collection.tools import AGENT_BASE_PROMPT
    # Historical controls must not silently pick up the retained candidate.
    assert request['messages'][0]['content'] == CREATION_IDENTITY + '\n\n' + AGENT_BASE_PROMPT
    assert request['extra_body']['thinking'] == {'type': 'disabled'}
    request.pop('temperature', None)  # It was None/omitted at provider call.
    return request, dict(captured_envelope=str(path), envelope_sha256=sha256(path.read_bytes()).hexdigest(),
        reconstruction_crosscheck='EXACT_MATCH_WITH_CAPTURED_LATER_REQUEST',
        original_prompt_source='e3e30a2 creation_prompt.py + AGENT_PROMPT unchanged in working tree')


def expression_only(request):
    # Reuse the tested formatter, then restore every original statistic.
    # No normalization of the known unique_evidence_count bug in this trial.
    out, _ = project(request, express=True)
    for old, new in zip(request['messages'], out['messages']):
        if old.get('role') != 'tool':
            continue
        try:
            a, b = json.loads(old['content']), json.loads(new['content'])
        except (ValueError, TypeError):
            continue
        if isinstance(a, dict) and isinstance(b, dict) and a.get('ok') and 'unique_evidence_count' in a.get('data', {}):
            b['data']['unique_evidence_count'] = a['data']['unique_evidence_count']
            new['content'] = json.dumps(b, ensure_ascii=False)
    return out


def prepare(root, output):
    template, provenance = envelope(root)
    scenes = []
    for label, folder, turn in SCENES:
        base = root/folder
        transcript, stored_hash = load_transcript(base, turn)
        assert transcript[-1]['role'] == 'assistant' and not transcript[-1].get('tool_calls')
        source = json.loads((base/'evidence.json').read_text())
        original_turn = next(t for t in source['turns'] if t.get('turn_id', t.get('result', {}).get('turn_id')) == turn)
        assert original_turn['result']['answer'] == transcript[-1]['content']
        request = deepcopy(template)
        request['messages'] = [template['messages'][0]] + [api_message(m) for m in transcript[:-1]]
        question = next(m['content'] for m in reversed(request['messages']) if m['role']=='user')
        assert question == original_turn['question']
        args = []
        for m in request['messages']:
            args.extend(m.get('tool_calls', []))
        scene = dict(scene=label, source_directory=str(base), turn_id=turn, question=question,
            transcript_sha256=stored_hash, transcript=transcript, historical_answer=transcript[-1]['content'],
            original_turn=original_turn, request=request, request_sha256=digest(request),
            original_tool_responses_sha256=digest([m['content'] for m in request['messages'] if m['role']=='tool']),
            known_tool_bug_preserved=True, reconstruction=provenance)
        (output/f'scene-{label}.json').write_text(json.dumps(scene, ensure_ascii=False, indent=2))
        scenes.append(scene)
    return scenes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--phase', choices=('prepare','baseline','compare','confirm'), required=True)
    parser.add_argument('--scenes', nargs='+', choices=('followup','publisher'), default=['followup','publisher'])
    args = parser.parse_args()
    os.umask(0o077)
    if args.phase=='prepare':
        args.output.mkdir(parents=True, exist_ok=False)
        scenes = prepare(args.root, args.output)
        print(json.dumps([dict(scene=s['scene'], question=s['question'], messages=len(s['request']['messages']),
                               reconstruction=s['reconstruction']) for s in scenes], ensure_ascii=False))
        return
    scenes = [json.loads((args.output/f'scene-{s}.json').read_text()) for s in args.scenes]
    arms = ['original'] if args.phase=='baseline' else ['original','data','semantic','brief','data_semantic','data_brief']
    repeats = 5 if args.phase == 'confirm' else 3
    jobs = [(scene, arm, repeat) for repeat in range(1,repeats + 1) for scene in scenes for arm in arms]
    random.Random(1013 if args.phase == 'confirm' else 1012).shuffle(jobs)
    evidence_path = args.output/f'{args.phase}.json'
    assert not evidence_path.exists()
    meta = dict(phase=args.phase, status='RUNNING', model=os.environ['CONVERSATION_MODEL'],
        planned_initial_calls=len(jobs), repeats_per_scene_arm=repeats, thinking='disabled', retries=0, production_changed=False,
        prompts=dict(semantic=SEMANTIC_PROMPT, brief=BRIEF_PROMPT), records=[],
        evaluation='Whole-answer pass/fail/incomplete; quote and explain every failure; no headline-number scoring.',
        limits='Historical reconstruction, not original wire capture or full live service rerun. New tool requests unresolved at this boundary.')
    def save():
        evidence_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    save()
    from openai import OpenAI
    def run(job):
        scene, arm, repeat = job
        request = deepcopy(scene['request'])
        if arm.startswith('data'):
            request = expression_only(request)
        prompt = SEMANTIC_PROMPT if 'semantic' in arm else BRIEF_PROMPT if 'brief' in arm else ''
        if prompt:
            request['messages'][0]['content'] += '\n' + prompt
        assert request['tools'] == scene['request']['tools']
        assert [m for m in request['messages'] if m['role'] in ('user','assistant')] == [m for m in scene['request']['messages'] if m['role'] in ('user','assistant')]
        if not arm.startswith('data'):
            assert [m for m in request['messages'] if m['role']=='tool'] == [m for m in scene['request']['messages'] if m['role']=='tool']
        assert request['model'] == os.environ['CONVERSATION_MODEL']
        # A common output cap, no tool_choice constraint, unchanged sampling.
        request.update(stream=False, max_tokens=4096)
        identifier = f'{args.phase}-{scene["scene"]}-{arm}-{repeat}'
        (args.output/f'request-{identifier}.json').write_text(json.dumps(request, ensure_ascii=False, indent=2))
        result = dict(id=identifier, scene=scene['scene'], arm=arm, repeat=repeat,
                      request_file=f'request-{identifier}.json', input_characters=len(json.dumps(request['messages'],ensure_ascii=False)))
        started=time.monotonic()
        try:
            with OpenAI(api_key=os.environ['CONVERSATION_API_KEY'], base_url=os.environ['CONVERSATION_BASE_URL'],
                        timeout=60, max_retries=0) as client:
                response=client.chat.completions.create(**request)
            choice=response.choices[0]; msg=choice.message
            result.update(answer=msg.content or '', tool_calls=[c.model_dump() for c in msg.tool_calls or []],
                response_model=response.model, usage=response.usage.model_dump() if response.usage else None,
                nonempty_reasoning_returned=bool(getattr(msg,'reasoning_content',None)))
            result['status']='needs_tool' if msg.tool_calls else 'completed' if choice.finish_reason=='stop' and msg.content else 'incomplete'
            if result['nonempty_reasoning_returned']:result['status']='unexpected_reasoning'
        except Exception as exc:
            result.update(status='request_error', error_type=type(exc).__name__)
        result['seconds']=round(time.monotonic()-started,3)
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(run,j) for j in jobs]):
            r=future.result();meta['records'].append(r);save()
            print(json.dumps({k:r[k] for k in ('id','status','seconds')}),flush=True)
    meta['status']='COMPLETE_NEEDS_WHOLE_ANSWER_REVIEW';save()


if __name__=='__main__':
    main()
