"""Confirmed-run persistence using the original P4 transitions and budgets."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path

from . import geographic_storage as records
from .scoped_selection import validate_plan
from .scoped_selection_store import Store


class GeographicExecutionStore(Store):
    _table = 'geographic_executions'

    def __init__(self, path, expected_binding, expected_plan_digest, lease_fence, *, state_lock=None):
        if not callable(lease_fence):
            raise ValueError('lease fence required')
        self.path = Path(path).resolve()
        self.expected_binding = deepcopy(expected_binding)
        self.expected_plan_digest = expected_plan_digest
        self.lease_fence = lease_fence
        self._state_lock = state_lock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._state_guard(), closing(self._connection()) as db:
            db.execute(f'CREATE TABLE IF NOT EXISTS {self._table} '
                       '(execution_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)')
            records.init_schema(db)

    def _fence(self, db):
        if self.lease_fence(db) is False:
            raise ValueError('worker lease fence expired')

    def _validate_plan(self, plan):
        validate_plan(plan, expected_plan_digest=self.expected_plan_digest)
        if plan['validation_origin'] != 'confirmed_run':
            raise ValueError('confirmed run required')
        if plan['binding'] | plan['runtime_binding'] != self.expected_binding:
            raise ValueError('trusted runtime binding mismatch')

    @staticmethod
    def _current(db, execution_id, expected_digest):
        h = records.header(db, execution_id)
        tree = records.Tree(db, h['summaries'])
        snap = tree.node(h['root'])
        plan = snap['plan']
        if (plan['execution_id'] != execution_id or plan['plan_digest'] != expected_digest
                or snap['revision'] != h['revision']):
            raise ValueError('snapshot binding or integrity mismatch')
        return h, tree, snap

    @staticmethod
    def read_connection(db, execution_id, expected_plan_digest, reader):
        """Read in the caller's transaction, including delivery's fenced write."""
        if records.is_segmented(db, execution_id):
            _, _, snap = GeographicExecutionStore._current(db, execution_id, expected_plan_digest)
            validate_plan(snap['plan'], expected_plan_digest=expected_plan_digest)
            if snap['plan']['validation_origin'] != 'confirmed_run':
                raise ValueError('confirmed run required')
        else:
            row = db.execute('SELECT snapshot FROM geographic_executions WHERE execution_id=?',
                             (execution_id,)).fetchone()
            if row is None:
                raise ValueError('unknown execution')
            snap = json.loads(row[0])
            plan = snap['plan']
            checker = object.__new__(GeographicExecutionStore)
            checker.expected_binding = plan['binding'] | plan['runtime_binding']
            checker.expected_plan_digest = expected_plan_digest
            checker._validate_snapshot(snap, execution_id, digest_function=Store._streamed_digest)
        return records.materialize(reader(snap))

    def read_state(self, execution_id, expected_plan_digest, reader):
        if expected_plan_digest != self.expected_plan_digest:
            raise ValueError('plan digest mismatch')

        def bound_reader(snap):
            plan = snap['plan']
            if plan['binding'] | plan['runtime_binding'] != self.expected_binding:
                raise ValueError('trusted runtime binding mismatch')
            return reader(snap)
        with self._state_guard(), closing(self._connection()) as db:
            db.execute('BEGIN')
            self._fence(db)
            result = self.read_connection(db, execution_id, expected_plan_digest, bound_reader)
            self._fence(db)
            db.commit()
            return result

    @staticmethod
    def _install(db, execution_id, snap):
        # The source may be a decoded legacy snapshot. Pop its history to keep
        # it outside both the current-state root and future hot transactions.
        operations = snap.pop('operations')
        events = snap.pop('events')
        snap.pop('snapshot_digest')
        tree = records.Tree(db)
        h = records.new_header(execution_id)
        h['root'] = tree.save(snap)
        h['summaries'] = tree.summaries
        for revision, event in enumerate(events):
            if event['revision'] != revision or (revision == 0 and event['operation_id'] != 'create'):
                raise ValueError('invalid geographic event sequence')
            op = operations.pop(event['operation_id']) if revision else None
            records.append_entry(tree, h, revision, event['operation_id'], event['payload'],
                                 op['digest'] if op else None, op['result'] if op else None)
        if operations or h['revision'] != snap['revision']:
            raise ValueError('invalid geographic operation ledger')
        records.write_header(db, h)

    def open_execution(self, plan):
        self._validate_plan(plan)
        eid = plan['execution_id']
        with self._state_guard(), closing(self._connection()) as db:
            db.execute('BEGIN IMMEDIATE')
            self._fence(db)
            if not db.execute('SELECT 1 FROM geographic_executions WHERE execution_id=?', (eid,)).fetchone():
                self._install(db, eid, self._initial_snapshot(plan))
            elif self.read_connection(db, eid, plan['plan_digest'], lambda s: s['plan']) != plan:
                raise ValueError('immutable execution plan conflict')
            self._fence(db)
            self._commit(db)

    def create(self, plan):
        self.open_execution(plan)
        return self.load(plan['execution_id'], plan['plan_digest'])

    def create_legacy(self, plan):
        """Create a v1 row for compatibility rehearsals."""
        return super().create(plan)

    def _transaction(self, execution_id, operation_id, payload, action):
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError('nonempty operation ID required')
        with self._state_guard(), closing(self._connection()) as db:
            db.execute('BEGIN IMMEDIATE')
            self._fence(db)
            if not records.is_segmented(db, execution_id):
                # Keep the format decision and update in one transaction so a
                # concurrent migration cannot race a legacy write.
                return self._legacy_transaction(db, execution_id, operation_id, payload, action)
            h, tree, snap = self._current(db, execution_id, self.expected_plan_digest)
            self._validate_plan(snap['plan'])
            row = db.execute('SELECT revision,entry_hash FROM geographic_operations '
                             'WHERE execution_id=? AND operation_id=? AND revision>0',
                             (execution_id, operation_id)).fetchone()
            fingerprint = self._streamed_digest(payload)
            if row:
                op = records.checked_entry(tree, row[1], execution_id, row[0], operation_id)
                if op['fingerprint'] != fingerprint:
                    raise ValueError('operation ID payload conflict')
                result = records.materialize(tree.node(op['result']))
                db.rollback()
                return result
            # Detect a lost ledger row before an external operation can be
            # claimed again. The unique index also serializes duplicate claims.
            count = db.execute('SELECT count(*) FROM geographic_operations WHERE execution_id=?',
                               (execution_id,)).fetchone()[0]
            if count != h['revision'] + 1:
                raise ValueError('incomplete geographic operation ledger')
            result = action(snap)
            self._refresh(snap)
            snap['revision'] += 1
            h['root'] = tree.save(snap)
            h['summaries'] = tree.summaries
            records.append_entry(tree, h, snap['revision'], operation_id, payload, fingerprint, result)
            records.write_header(db, h)
            self._fence(db)
            self._commit(db)
            return records.materialize(result)

    def _full_snapshot(self, db, execution_id, expected_plan_digest):
        if not records.is_segmented(db, execution_id):
            return self.read_connection(db, execution_id, expected_plan_digest, lambda s: s)
        h, tree, snap = self._current(db, execution_id, expected_plan_digest)
        self._validate_plan(snap['plan'])
        result = records.materialize(snap)
        events, operations, previous = [], {}, ''
        for rev, oid, key in db.execute('SELECT revision,operation_id,entry_hash FROM geographic_operations '
                                       'WHERE execution_id=? ORDER BY revision', (execution_id,)):
            entry = records.checked_entry(tree, key, execution_id, rev, oid)
            if rev != len(events) or entry['previous'] != previous:
                raise ValueError('invalid geographic operation ledger')
            previous = key
            events.append({'revision': rev, 'operation_id': oid,
                           'payload': records.materialize(tree.node(entry['payload']))})
            if rev:
                operations[oid] = {'digest': entry['fingerprint'],
                                   'result': records.materialize(tree.node(entry['result']))}
        if previous != h['ledger'] or len(events) != h['revision'] + 1:
            raise ValueError('incomplete geographic operation ledger')
        result.update(events=events, operations=operations)
        result['snapshot_digest'] = self._streamed_digest(result)
        return result

    def load(self, execution_id, expected_plan_digest):
        """Explicit full export for diagnostics; live callers use read_state."""
        with self._state_guard(), closing(self._connection()) as db:
            db.execute('BEGIN')
            self._fence(db)
            snap = self._full_snapshot(db, execution_id, expected_plan_digest)
            self._validate_plan(snap['plan'])
            self._fence(db)
            db.commit()
            return snap

    def migrate_legacy(self, execution_id, expected_plan_digest):
        with self._state_guard(), closing(self._connection()) as db:
            db.execute('BEGIN IMMEDIATE')
            self._fence(db)
            if not records.is_segmented(db, execution_id):
                row = db.execute('SELECT snapshot FROM geographic_executions WHERE execution_id=?',
                                 (execution_id,)).fetchone()
                if row is None:
                    raise ValueError('unknown execution')
                snap = json.loads(row[0])
                del row
                self._validate_snapshot(snap, execution_id, digest_function=self._streamed_digest)
                if snap['plan']['plan_digest'] != expected_plan_digest:
                    raise ValueError('plan digest mismatch')
                db.execute('INSERT OR IGNORE INTO geographic_legacy_backups SELECT * '
                           'FROM geographic_executions WHERE execution_id=?', (execution_id,))
                self._install(db, execution_id, snap)
            else:
                _, _, snap = self._current(db, execution_id, expected_plan_digest)
                self._validate_plan(snap['plan'])
            self._fence(db)
            self._commit(db)

    def export_legacy(self, execution_id, expected_plan_digest):
        with self._state_guard(), closing(self._connection()) as db:
            db.execute('BEGIN IMMEDIATE')
            self._fence(db)
            if records.is_segmented(db, execution_id):
                snap = self._full_snapshot(db, execution_id, expected_plan_digest)
                db.execute('UPDATE geographic_executions SET snapshot=? WHERE execution_id=?',
                           (json.dumps(snap, ensure_ascii=False), execution_id))
                db.execute('DELETE FROM geographic_operations WHERE execution_id=?', (execution_id,))
            self._fence(db)
            self._commit(db)
