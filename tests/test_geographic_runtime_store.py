from copy import deepcopy
import sqlite3
import pytest
from audit_agent.scoped_selection import digest
from audit_agent.scoped_selection_store import Store
from test_scoped_selection_store import plan


def runtime_plan():
    p = plan()
    p.update(schema_version='p4-confirmed-input-v1', validation_origin='confirmed_run',
             runtime_binding=dict(run_id='run', job_id='job', config_hash=digest('config'), p2_plan_digest=digest('p2')))
    p['plan_digest'] = digest({k:v for k,v in p.items() if k != 'plan_digest'})
    return p


def test_runtime_store_fence_binding_and_recovery(tmp_path):
    from audit_agent.geographic_execution_store import GeographicExecutionStore
    p = runtime_plan()
    valid = [True]
    def fence(db):
        assert db.in_transaction
        return valid[0]
    path = tmp_path / 'jobs.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE jobs(id TEXT)')
    s = GeographicExecutionStore(path, p['binding'] | p['runtime_binding'], p['plan_digest'], fence)
    assert s.create(p)['plan'] == p
    valid[0] = False
    for action in (lambda:s.load('e', p['plan_digest']), lambda:s.create(p),
                   lambda:s.record_query('e','q','capturing',[],'start')):
        with pytest.raises(ValueError, match='lease'):
            action()
    valid[0] = True
    assert s.load('e',p['plan_digest'])['queries']['q']['status'] == 'pending'
    wrong = GeographicExecutionStore(path, p['binding'] | p['runtime_binding'] | {'owner':'other'}, p['plan_digest'], fence)
    with pytest.raises(ValueError, match='binding'):
        wrong.load('e',p['plan_digest'])


def test_experiment_store_does_not_accept_runtime_plan(tmp_path):
    with pytest.raises(ValueError):
        Store(tmp_path/'fixture.sqlite').create(runtime_plan())


def test_commit_fence_rolls_back_events_with_operation(tmp_path):
    from audit_agent.geographic_execution_store import GeographicExecutionStore
    p=runtime_plan()
    s=GeographicExecutionStore(tmp_path/'jobs.db',p['binding']|p['runtime_binding'],p['plan_digest'],lambda db:True)
    before=s.create(p)
    calls=[]
    def fence(db):
        calls.append(True)
        return len(calls)==1
    s.lease_fence=fence
    with pytest.raises(ValueError,match='lease'):
        s.record_query('e','q','capturing',[],'capture')
    s.lease_fence=lambda db:True
    assert s.load('e',p['plan_digest'])==before
