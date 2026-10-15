"""Confirmed-run persistence using the original P4 transitions and budgets."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
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
        with self._state_guard():
            with closing(self._connection()) as db:
                db.execute(f'CREATE TABLE IF NOT EXISTS {self._table} '
                           '(execution_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL)')

    def _fence(self, db):
        if self.lease_fence(db) is False:
            raise ValueError('worker lease fence expired')

    def _validate_plan(self, plan):
        validate_plan(plan, expected_plan_digest=self.expected_plan_digest)
        if plan['validation_origin'] != 'confirmed_run':
            raise ValueError('confirmed run required')
        if plan['binding'] | plan['runtime_binding'] != self.expected_binding:
            raise ValueError('trusted runtime binding mismatch')
