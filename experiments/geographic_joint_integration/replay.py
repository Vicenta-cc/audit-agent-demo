"""Guarded subprocess recovery of frozen joint input through original P4."""
import argparse
import json
from pathlib import Path

from .run_offline import bootstrap

if bootstrap() is None:
    raise RuntimeError('replay requires the guarded offline runner environment')

from .scenarios import JointHarness


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', required=True, type=Path)
    parser.add_argument('--expected', required=True, type=Path)
    parser.add_argument('--state-db', required=True, type=Path)
    args = parser.parse_args()
    if not args.state_db.is_absolute() or not args.state_db.is_file():
        raise ValueError('existing absolute state database required for recovery')
    payload = json.loads(args.case.read_text())
    expected = json.loads(args.expected.read_text())
    # Expected values are a separate caller-held file, not derived from payload.
    h = JointHarness(payload['bundle'], expected, args.state_db,
                     {int(k): v for k, v in payload['rows'].items()})
    before = h.store.load(h.plan['execution_id'], h.plan['plan_digest'])
    result = h.runner().run()
    output = h.evidence(payload['case_id'] + '-child', result,
                        restored_in_separate_process=True, before_state=before['state'])
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
