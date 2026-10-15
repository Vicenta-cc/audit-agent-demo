"""Replay persisted fixture inputs and an explicit manual response, offline."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    directory = args.evidence_dir.resolve()
    if not directory.is_relative_to(Path(__file__).resolve().parent):
        raise ValueError('fixture evidence must be inside this experiment')
    from .offline_guards import OfflineGuards
    guard = OfflineGuards(experiment_root=directory).install()
    try:
        with tempfile.TemporaryDirectory(prefix='geo-manual-replay-') as temporary:
            for name in ('XHS_AUDIT_DATA_DIR', 'XHS_AUDIT_OUTPUTS_DIR', 'HERMES_HOME'):
                os.environ[name] = str(Path(temporary) / name.lower())
            from .storage import load_plan
            from .p3_bridge import prepare, consume, consume_policy
            evidence = json.loads((directory/'p3-manual-replay.json').read_text(encoding='utf-8'))
            if evidence['validation_origin'] != 'fixture':
                raise ValueError('manual evidence is fixture only')
            plan = load_plan(directory/'example-fixture-plan.sqlite3',
                expected_identity=evidence['expected_identity'], expected_digest=evidence['expected_digest'])
            request = prepare(plan.inputs.configuration.relevance_scope_state.model_dump(mode='json'),
                plan.inputs.p3_inputs, evidence['candidate'])
            decision = consume(request, evidence['manual_response'])
            policy = consume_policy(decision, target_level=plan.inputs.scope_context['target_level'],
                selection_policy=plan.policy.selection_policy, risk={'valid':True, 'strength':'strong'})
            checks = {'request_equal':request == evidence['request'],
                'decision_equal':decision == evidence['decision'], 'consumer_equal':policy == evidence['test_consumer']}
            if not all(checks.values()):
                raise ValueError('manual fixture replay differs: '+str(checks))
            print(json.dumps(dict(validation_origin='fixture', plan_digest=plan.plan_digest,
                **checks, guard_attempts=len(guard.attempts), real_model_calls=0), ensure_ascii=False))
    finally:
        guard.uninstall()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
