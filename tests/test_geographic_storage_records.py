"""Check the storage invariants independently of the selection implementation."""
import json
import sqlite3

import pytest

from audit_agent import geographic_storage as records
from audit_agent.geographic_execution_store import GeographicExecutionStore
from test_geographic_runtime_store import runtime_plan


def store_for(path):
    p = runtime_plan()
    store = GeographicExecutionStore(path, p['binding'] | p['runtime_binding'], p['plan_digest'], lambda db: True)
    return store, p


def test_catalog_is_shared_and_a_sibling_edit_reuses_immutable_nodes(tmp_path):
    with sqlite3.connect(tmp_path / 'objects') as db:
        records.init_schema(db)
        tree = records.Tree(db)
        catalog = {'cities': ['旧名称', '新名称']}
        root = tree.pack({'left': {'admin_catalog': catalog}, 'right': {'admin_catalog': catalog},
                          'progress': {'steps': [{'state': 'started'}]}})
        snap = tree.node(root)
        catalog_hash = tree.pack(catalog, 'admin_catalog')
        assert tree.pack(snap['left']['admin_catalog'], 'admin_catalog') == catalog_hash
        assert tree.pack(snap['right']['admin_catalog'], 'admin_catalog') == catalog_hash
        before = db.execute('SELECT count(*) FROM geographic_objects').fetchone()[0]
        snap['progress']['steps'][0]['state'] = 'done'
        new_root = tree.pack(snap)
        assert new_root != root
        assert dict(tree.node(new_root))['progress']['steps'][0]['state'] == 'done'
        assert tree.node(root)['progress']['steps'][0]['state'] == 'started'
        assert db.execute('SELECT count(*) FROM geographic_objects').fetchone()[0] == before + 4
        db.execute('UPDATE geographic_objects SET body=? WHERE hash=?', (b'[]', catalog_hash))
        assert tree.node(new_root)['progress']['steps'][0]['state'] == 'done'
        with pytest.raises(ValueError, match='integrity'):
            _ = tree.node(new_root)['left']['admin_catalog']


def test_replay_and_missing_ledger_entry_cannot_double_apply(tmp_path):
    store, p = store_for(tmp_path / 'audit')
    store.open_execution(p)
    # A user operation can share the initial event's name in the old format.
    result = store.record_query('e', 'q', 'capturing', [], 'create')
    assert store.record_query('e', 'q', 'capturing', [], 'create') == result
    with pytest.raises(ValueError, match='payload conflict'):
        store.record_query('e', 'q', 'paused', [], 'create')
    with sqlite3.connect(store.path) as db:
        db.execute('DELETE FROM geographic_operations WHERE execution_id=? AND revision=1', ('e',))
    with pytest.raises(ValueError, match='incomplete'):
        store.record_query('e', 'q', 'capturing', [], 'create')


def test_summary_is_bound_to_query_and_an_expired_writer_cannot_advance_it(tmp_path):
    store, p = store_for(tmp_path / 'audit')
    store.open_execution(p)
    get = lambda: store.read_state('e', p['plan_digest'], lambda s: s['queries'].summary('q'))
    first = get()
    checks = []
    def lease(db):
        checks.append(True)
        return len(checks) == 1
    store.lease_fence = lease
    with pytest.raises(ValueError, match='lease'):
        store.record_query('e', 'q', 'capturing', [], 'claim')
    store.lease_fence = lambda db: True
    assert get() == first
    store.record_query('e', 'q', 'capturing', [], 'claim')
    assert get()['status'] == 'capturing'
    with sqlite3.connect(store.path) as db:
        h = records.header(db, 'e')
        summary = h['summaries']['q']
        previous = json.loads(db.execute('SELECT body FROM geographic_objects WHERE hash=?', (summary,)).fetchone()[0])
        previous[1]['status'] = 'paused'
        db.execute('UPDATE geographic_objects SET body=? WHERE hash=?',
                   (json.dumps(previous).encode(), summary))
    with pytest.raises(ValueError, match='integrity'):
        get()


def test_scoped_reader_rejects_a_different_trusted_plan(tmp_path):
    store, p = store_for(tmp_path / 'audit')
    store.open_execution(p)
    other = GeographicExecutionStore(store.path, store.expected_binding, 'other', lambda db: True)
    with pytest.raises(ValueError, match='plan digest mismatch'):
        other.read_state('e', p['plan_digest'], lambda s: s['revision'])
