from copy import deepcopy
import sqlite3
import threading
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


def test_runtime_snapshot_load_does_not_compete_for_writer_lock(tmp_path):
    """Status/fence reads must stay readable while another audit writer is active."""
    from audit_agent.geographic_execution_store import GeographicExecutionStore

    p = runtime_plan()
    path = tmp_path / 'jobs.db'
    store = GeographicExecutionStore(
        path,
        p['binding'] | p['runtime_binding'],
        p['plan_digest'],
        lambda db: True,
    )
    store.create(p)
    store._connection = lambda: sqlite3.connect(path, timeout=0.05, isolation_level=None)

    writer = sqlite3.connect(path, timeout=0.05, isolation_level=None)
    writer.execute('BEGIN IMMEDIATE')
    try:
        assert store.load('e', p['plan_digest'])['plan'] == p
    finally:
        writer.rollback()
        writer.close()


def test_runtime_state_and_job_writes_follow_one_local_order(tmp_path):
    """The P4 ledger and Job progress writer must not race on one SQLite file."""
    from audit_agent.geographic_execution_store import GeographicExecutionStore

    p = runtime_plan()
    path = tmp_path / 'jobs.db'
    write_lock = threading.Lock()
    store = GeographicExecutionStore(
        path,
        p['binding'] | p['runtime_binding'],
        p['plan_digest'],
        lambda db: True,
        state_lock=write_lock,
    )
    store.create(p)

    entered_commit = threading.Event()
    release_commit = threading.Event()
    original_commit = store._commit

    def delayed_commit(db):
        entered_commit.set()
        assert release_commit.wait(2)
        original_commit(db)

    store._commit = delayed_commit
    state_errors = []
    progress_errors = []

    def write_state():
        try:
            store.record_query('e', 'q', 'capturing', [], 'capture-start')
        except BaseException as exc:  # pragma: no cover - asserted below
            state_errors.append(exc)

    def write_progress():
        try:
            with write_lock, sqlite3.connect(path, timeout=0.05) as db:
                db.execute('CREATE TABLE IF NOT EXISTS progress(value TEXT)')
                db.execute('INSERT INTO progress VALUES (?)', ('saved',))
        except BaseException as exc:  # pragma: no cover - asserted below
            progress_errors.append(exc)

    state_thread = threading.Thread(target=write_state)
    progress_thread = threading.Thread(target=write_progress)
    state_thread.start()
    assert entered_commit.wait(2)
    progress_thread.start()
    release_commit.set()
    state_thread.join(2)
    progress_thread.join(2)

    assert not state_thread.is_alive()
    assert not progress_thread.is_alive()
    assert state_errors == []
    assert progress_errors == []
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT value FROM progress').fetchone()[0] == 'saved'


def test_runtime_store_closes_each_short_lived_connection(tmp_path):
    """A completed state read/write must not leave a SQLite handle behind."""
    from audit_agent.geographic_execution_store import GeographicExecutionStore

    p = runtime_plan()
    path = tmp_path / 'jobs.db'
    store = GeographicExecutionStore(
        path,
        p['binding'] | p['runtime_binding'],
        p['plan_digest'],
        lambda db: True,
    )
    store.create(p)

    closed = []

    class TrackingConnection(sqlite3.Connection):
        def close(self):
            closed.append(True)
            return super().close()

    store._connection = lambda: sqlite3.connect(
        path,
        timeout=0.05,
        isolation_level=None,
        factory=TrackingConnection,
    )

    store.load('e', p['plan_digest'])
    store.record_query('e', 'q', 'capturing', [], 'capture-start')

    assert closed == [True, True]
