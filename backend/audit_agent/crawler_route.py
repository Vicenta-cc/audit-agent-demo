"""Application bridge for frozen account routes and inherited secret pipes."""
from __future__ import annotations

import math
import ipaddress
import hashlib
import importlib.util
import sys
import os
import selectors
import threading
import time
import json
import secrets
import logging
from dataclasses import dataclass, asdict
from functools import lru_cache

from .config import settings

BROWSER_GUARD_REASONS = frozenset({'BOUND_REQUEST_GUARD','BOUND_RUNTIME_GUARD','BOUND_DATABASE_GUARD',
    'SHARED_ADMISSION_GUARD','SHARED_COOLDOWN_GUARD','SHARED_REQUEST_LIMIT','SHARED_ACTIVE_LIMIT',
    'PAGE_REQUEST_LIMIT','PAGE_ROLLING_LIMIT','PAGE_LIFETIME_LIMIT','PAGE_BYTE_LIMIT','PAGE_SIZES_FAILED',
    'CDP_GUARD_TIMEOUT','CDP_GUARD_FAILED','DOCUMENT_GUARD_TIMEOUT','DOCUMENT_GUARD_FAILED',
    'PERIODIC_PROBE_GUARD','BROWSER_QUEUE_LIMIT'})


@lru_cache(maxsize=8)
def _load_contract(path):
    name = 'tools.collection_route'
    existing = sys.modules.get(name)
    if existing is not None and getattr(existing, '__file__', None) == path:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_route_module():
    return route_contract()


def route_contract():
    crawler_root = str(settings.media_crawler_dir.resolve())
    if crawler_root not in sys.path:
        sys.path.insert(0, crawler_root)
    return _load_contract(str(settings.media_crawler_dir.resolve() / 'tools' / 'collection_route.py'))


# A single exception class across application callers and dynamic contract loads.
class CollectionRouteError(RuntimeError):
    """Application errors never contain transport secrets or URLs."""
    def __init__(self, code):
        allowed = {
            "PROXY_BUSY", "SUPPLEMENT_DEFERRED", "PROXY_REVISION_MISMATCH",
            "PROXY_EXIT_CHANGED", "PROXY_UNAVAILABLE", "PROXY_AUTH_FAILED",
            "PROXY_CONFIG_INVALID", "PROBE_BUDGET_EXHAUSTED", "SCHEDULER_POLICY_CONFLICT",
            "PROXY_EXPIRED", "PROXY_CONNECTION_REFUSED", "PROXY_TRANSPORT_FAILED",
        }
        self.code = code if isinstance(code, str) and code in allowed else "PROXY_CONFIG_INVALID"
        super().__init__(self.code)


ROUTE_MODES = frozenset({'proxy_required', 'proxy_preferred', 'direct_degraded', 'legacy_direct'})
OPERATION_STATES = frozenset({'issued', 'acked', 'draining', 'ended', 'expired', 'drain_timeout'})


@dataclass(frozen=True)
class RouteOperation:
    """Versioned App-owned route envelope. Secrets never belong in this object."""
    operation_id: str
    lease_id: str
    lease_version: int
    owner_id: str
    account_id: str
    profile_key: str
    route_mode: str
    proxy_id: str = ''
    resource_revision: int = 0
    observed_exit: str = ''
    exit_generation: int = 0
    expires_at: float = 0.0
    context_id: str = ''
    scope_ids: tuple = ()
    snapshot_digest: str = ''
    proxy_expires_at: float = 0.0

    def __post_init__(self):
        import re
        if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value) for value in (self.operation_id, self.lease_id, self.owner_id, self.account_id)):
            raise ValueError('ROUTE_OPERATION_INVALID')
        if self.route_mode not in ROUTE_MODES or type(self.lease_version) is not int or self.lease_version < 1:
            raise ValueError('ROUTE_OPERATION_INVALID')
        if self.route_mode == 'direct_degraded' and not self.observed_exit:
            raise ValueError('SHARED_EXIT_UNKNOWN')
        if type(self.expires_at) not in (int, float) or not math.isfinite(self.expires_at) or self.expires_at <= 0:
            raise ValueError('ROUTE_OPERATION_INVALID')
        if (type(self.proxy_expires_at) not in (int,float) or not math.isfinite(self.proxy_expires_at) or self.proxy_expires_at < 0
                or type(self.resource_revision) is not int or self.resource_revision < 0
                or type(self.exit_generation) is not int or self.exit_generation < 0):
            raise ValueError('ROUTE_OPERATION_INVALID')
        if not isinstance(self.context_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',self.context_id):
            raise ValueError('ROUTE_OPERATION_INVALID')
        if not isinstance(self.snapshot_digest,str) or not re.fullmatch(r'[0-9a-f]{64}',self.snapshot_digest):
            raise ValueError('ROUTE_OPERATION_INVALID')

    def to_dict(self):
        result = asdict(self)
        result['scope_ids'] = list(self.scope_ids)
        return result

    @classmethod
    def create(cls, **kwargs):
        kwargs.setdefault('operation_id', secrets.token_hex(16))
        kwargs.setdefault('lease_id', secrets.token_hex(16))
        kwargs.setdefault('lease_version', 1)
        kwargs.setdefault('scope_ids', ())
        return cls(**kwargs)


class RouteOperationStore:
    """Durable idempotency/ACK state for App-owned route operations."""
    def __init__(self, db_path):
        import sqlite3
        self.db_path = db_path
        self._lock = threading.RLock()
        with self._lock, sqlite3.connect(db_path) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS route_operations (
                operation_id TEXT PRIMARY KEY, lease_id TEXT NOT NULL, lease_version INTEGER NOT NULL,
                owner_id TEXT NOT NULL, account_id TEXT NOT NULL, state TEXT NOT NULL,
                request_json TEXT NOT NULL, ack_json TEXT, expires_at REAL NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, child_pgid INTEGER)''')
            if 'child_pgid' not in {row[1] for row in conn.execute('PRAGMA table_info(route_operations)')}:
                conn.execute('ALTER TABLE route_operations ADD COLUMN child_pgid INTEGER')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_route_operations_lease ON route_operations(lease_id, lease_version)')
            conn.execute('CREATE TABLE IF NOT EXISTS route_lease_commands (operation_id TEXT PRIMARY KEY,request_json TEXT NOT NULL,response_json TEXT NOT NULL)')

    def _row(self, row):
        if not row: return None
        return {'operation_id': row[0], 'lease_id': row[1], 'lease_version': row[2], 'owner_id': row[3],
                'account_id': row[4], 'state': row[5], 'request': json.loads(row[6]),
                'ack': json.loads(row[7]) if row[7] else None, 'expires_at': row[8], 'child_pgid': row[11] if len(row)>11 else None}

    def issue(self, operation: RouteOperation):
        import sqlite3
        now = time.time()
        payload = json.dumps(operation.to_dict(), sort_keys=True, allow_nan=False)
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM route_operations WHERE operation_id=?', (operation.operation_id,)).fetchone()
            if row:
                existing = self._row(row)
                if existing['request'] != operation.to_dict(): raise ValueError('ROUTE_OPERATION_CONFLICT')
                return existing
            if conn.execute('SELECT 1 FROM route_lease_commands WHERE operation_id=?',(operation.operation_id,)).fetchone():
                raise ValueError('ROUTE_OPERATION_CONFLICT')
            if operation.expires_at <= now:
                raise ValueError('ROUTE_LEASE_EXPIRED')
            lease = conn.execute('SELECT owner_id,lease_version,state FROM route_operations WHERE lease_id=? ORDER BY lease_version DESC LIMIT 1', (operation.lease_id,)).fetchone()
            if lease and (lease[0] != operation.owner_id or lease[1] >= operation.lease_version or lease[2] not in ('ended', 'expired')):
                raise ValueError('ROUTE_LEASE_REJECTED')
            conn.execute('''INSERT INTO route_operations(operation_id,lease_id,lease_version,owner_id,account_id,state,request_json,expires_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?, ?,?,?)''', (operation.operation_id, operation.lease_id, operation.lease_version,
                operation.owner_id, operation.account_id, 'issued', payload, operation.expires_at, now, now))
            return self._row(conn.execute('SELECT * FROM route_operations WHERE operation_id=?', (operation.operation_id,)).fetchone())

    def ack(self, operation_id, *, owner_id, lease_version, snapshot_digest='', context_id=''):
        import sqlite3
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM route_operations WHERE operation_id=?', (operation_id,)).fetchone()
            current = self._row(row)
            if current is None or current['owner_id'] != owner_id or current['lease_version'] != lease_version:
                raise ValueError('ROUTE_LEASE_REJECTED')
            if type(lease_version) is not int or not snapshot_digest or not context_id:
                raise ValueError('ROUTE_LEASE_REJECTED')
            expected = current['request'].get('snapshot_digest', '')
            if not expected or expected != snapshot_digest or current['request'].get('context_id') != context_id:
                raise ValueError('ROUTE_SNAPSHOT_MISMATCH')
            if current['expires_at'] <= time.time():
                raise ValueError('ROUTE_LEASE_EXPIRED')
            if current['state'] == 'acked':
                return current
            if current['state'] != 'issued':
                raise ValueError('ROUTE_LEASE_EXPIRED')
            ack = {'operation_id': operation_id, 'lease_id': current['lease_id'], 'lease_version': lease_version,
                   'snapshot_digest': snapshot_digest, 'context_id': context_id,
                   'owner_id': owner_id, 'expires_at': current['expires_at'],
                   'ttl': current['expires_at'] - time.time()}
            conn.execute('UPDATE route_operations SET state="acked",ack_json=?,updated_at=? WHERE operation_id=?', (json.dumps(ack, sort_keys=True), time.time(), operation_id))
            return self._row(conn.execute('SELECT * FROM route_operations WHERE operation_id=?', (operation_id,)).fetchone())

    def record_child(self, operation_id, *, owner_id, lease_version, child_pgid):
        import sqlite3
        if type(child_pgid) is not int or child_pgid < 1 or type(lease_version) is not int:
            raise ValueError('ROUTE_LEASE_REJECTED')
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._row(conn.execute('SELECT * FROM route_operations WHERE operation_id=?',(operation_id,)).fetchone())
            if (not row or row['owner_id']!=owner_id or row['lease_version']!=lease_version
                    or row['state'] not in {'issued','acked'} or row['child_pgid'] not in (None,child_pgid)):
                raise ValueError('ROUTE_LEASE_REJECTED')
            conn.execute('UPDATE route_operations SET child_pgid=? WHERE operation_id=?',(child_pgid,operation_id))

    def ticket(self, operation_id, *, owner_id, snapshot_digest, context_id):
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            row = self._row(conn.execute('SELECT * FROM route_operations WHERE operation_id=?',(operation_id,)).fetchone())
        if (not row or row['owner_id'] != owner_id or row['state'] != 'acked' or row['expires_at'] <= time.time()
                or row['request']['snapshot_digest'] != snapshot_digest or row['request']['context_id'] != context_id):
            raise ValueError('ROUTE_LEASE_REJECTED')
        return row['ack']

    def renew(self, operation_id, *, route_operation_id, owner_id, lease_version):
        """Idempotent bounded ticket renewal; frozen route identity never changes."""
        import sqlite3
        import re
        if (type(lease_version) is not int or lease_version < 1
                or any(not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',value) for value in (operation_id,route_operation_id,owner_id))):
            raise ValueError('ROUTE_LEASE_REJECTED')
        request = json.dumps(dict(route_operation_id=route_operation_id, owner_id=owner_id,
            lease_version=lease_version), sort_keys=True)
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM route_operations WHERE operation_id=?',(operation_id,)).fetchone():
                raise ValueError('ROUTE_OPERATION_CONFLICT')
            prior = conn.execute('SELECT request_json,response_json FROM route_lease_commands WHERE operation_id=?', (operation_id,)).fetchone()
            if prior:
                if prior[0] != request:
                    raise ValueError('ROUTE_OPERATION_CONFLICT')
                return json.loads(prior[1])
            raw = conn.execute('SELECT * FROM route_operations WHERE operation_id=?', (route_operation_id,)).fetchone()
            row = self._row(raw)
            now = time.time()
            if (not row or row['owner_id'] != owner_id or row['lease_version'] != lease_version
                    or row['state'] != 'acked' or row['expires_at'] <= now):
                raise ValueError('ROUTE_LEASE_REJECTED')
            limit = min(raw[9] + 1200, row['request'].get('proxy_expires_at') or raw[9] + 1200)
            expiry = min(now + 300, limit)
            if expiry <= now or lease_version >= 5:
                raise ValueError('ROUTE_LEASE_EXPIRED')
            ack = dict(row['ack'], lease_version=lease_version+1, expires_at=expiry, ttl=expiry-now)
            conn.execute('UPDATE route_operations SET lease_version=?,expires_at=?,ack_json=?,updated_at=? WHERE operation_id=?', (lease_version+1,expiry,json.dumps(ack),now,route_operation_id))
            conn.execute('INSERT INTO route_lease_commands VALUES(?,?,?)',(operation_id,request,json.dumps(ack)))
            return ack

    def transition(self, operation_id, *, state, owner_id, lease_version):
        if state not in OPERATION_STATES:
            raise ValueError('ROUTE_OPERATION_INVALID')
        import sqlite3
        with self._lock, sqlite3.connect(self.db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._row(conn.execute('SELECT * FROM route_operations WHERE operation_id=?', (operation_id,)).fetchone())
            if type(lease_version) is not int or row is None or row['owner_id'] != owner_id or row['lease_version'] != lease_version:
                raise ValueError('ROUTE_LEASE_REJECTED')
            if row['state'] == state:
                return row
            if row['state'] in ('ended', 'expired', 'drain_timeout'):
                raise ValueError('ROUTE_TRANSITION_REJECTED')
            allowed = {'issued': {'draining', 'expired'}, 'acked': {'draining', 'expired'}, 'draining': {'ended', 'drain_timeout'}}
            if state not in allowed.get(row['state'], set()):
                raise ValueError('ROUTE_TRANSITION_REJECTED')
            conn.execute('UPDATE route_operations SET state=?,updated_at=? WHERE operation_id=?', (state, time.time(), operation_id))
            return self._row(conn.execute('SELECT * FROM route_operations WHERE operation_id=?', (operation_id,)).fetchone())


def direct_degraded_allowed(*, transport_error, health_pool_exhausted, risk_signal=False,
                            observed_exit='', shared_exit_key='', old_context_drained=False):
    """Pure App gate: only transport failure and exhausted healthy pool may degrade."""
    if any(type(value) is not bool for value in (health_pool_exhausted, risk_signal, old_context_drained)):
        return False
    allowed_errors = {'PROXY_EXPIRED', 'PROXY_TRANSPORT_FAILED', 'PROXY_CONNECTION_REFUSED', 'PROXY_AUTH_FAILED'}
    if transport_error not in allowed_errors or not health_pool_exhausted or risk_signal:
        return False
    if type(observed_exit) is not str or type(shared_exit_key) is not str or not old_context_drained:
        return False
    try:
        return ipaddress.ip_address(observed_exit) == ipaddress.ip_address(shared_exit_key)
    except ValueError:
        return False


RouteError = CollectionRouteError
_store = None
_store_lock = threading.Lock()


def get_proxy_store(account_store=None):
    from .crawler_proxy_store import CrawlerProxyStore
    from backend.task_admission.resources import proxy_mutation_guard
    if account_store is not None:
        from functools import partial
        return CrawlerProxyStore(account_store, mutation_guard=partial(proxy_mutation_guard, account_store=account_store), health_notifier=route_health_notifier)
    global _store
    with _store_lock:
        if _store is None:
            from .crawler_account_store import crawler_account_store
            _store = CrawlerProxyStore(crawler_account_store, mutation_guard=proxy_mutation_guard, health_notifier=route_health_notifier)
        return _store


def _safe_error(exc):
    code = getattr(exc, 'code', str(exc))
    if code in {'PROXY_NOT_FOUND', 'PROXY_BINDING_REQUIRED', 'PROXY_LOGIN_VALIDATION_REQUIRED', 'ACCOUNT_CHALLENGE'}:
        code = 'PROXY_UNAVAILABLE'
    return CollectionRouteError(code)


def resolve_account_route(account_id, *, account_store=None, expected_binding_revision=None,
                          expected_config_revision=None, allow_probe=False):
    from .crawler_account_store import crawler_account_store
    accounts = account_store or crawler_account_store
    getter = getattr(accounts, 'get', None)
    if not callable(getter):
        if getattr(settings, 'crawler_proxy_enabled', False):
            raise CollectionRouteError('PROXY_UNAVAILABLE')
        accounts = crawler_account_store
        getter = accounts.get
    account = getter(account_id)
    if account and account.get('platform') == 'dy' and not account.get('proxy_required') and not account.get('proxy_id') and account.get('route_mode') != 'direct_degraded':
        if expected_binding_revision is not None and expected_binding_revision != account.get('binding_revision', 1):
            raise CollectionRouteError('PROXY_REVISION_MISMATCH')
        contract = route_contract()
        direct = accounts.get_direct_route(account_id, allow_stale_recovery=allow_probe)
        ip = direct['observed_exit_ip'] if direct else None
        observed_at = direct['observed_exit_at'] if direct else None
        scopes = (tuple(sorted(['dy', 'dy:account:' + account_id,
            'dy:service_exit:' + ip, 'dy:exit:' + ip])) if ip else contract.derive_scope_ids('dy', account_id, 'service-direct'))
        return contract.BoundRoute('dy', account_id, contract.derive_profile_key(account_id),
            'service-direct', account.get('binding_revision', 0), 1, 'direct.invalid', 1,
            '', '', time.time() + 1200, ip, observed_at,
            str(settings.request_scheduler_db.resolve()), scopes, route_mode='legacy_direct',
            exit_generation=direct['shared_exit_generation'] if direct else 0)
    if not getattr(settings, 'crawler_proxy_enabled', False) and (
        account is None or not account.get('proxy_required') and not account.get('proxy_id')
    ):
        return None
    if getattr(settings, 'crawler_safety_enabled', False) and account and account.get('route_mode') == 'direct_degraded':
        if not getattr(settings, 'crawler_direct_degrade_enabled', False):
            raise CollectionRouteError('PROXY_UNAVAILABLE')
        direct = accounts.get_direct_route(account_id, allow_stale_recovery=allow_probe)
        if direct is None:
            raise CollectionRouteError('PROXY_UNAVAILABLE')
        contract = route_contract()
        # Template becomes managed after App owns all account/admission locks.
        from dataclasses import replace
        baseline = contract.BoundRoute('dy', account_id, contract.derive_profile_key(account_id),
            'service-direct', max(1, account.get('binding_revision', 1)), 1, 'direct.invalid', 1,
            '', '', time.time() + 1200, direct['observed_exit_ip'], direct['observed_exit_at'],
            str(settings.request_scheduler_db.resolve()), tuple(sorted(['dy', 'dy:account:' + account_id,
                'dy:service_exit:' + direct['observed_exit_ip'], 'dy:exit:' + direct['observed_exit_ip']])),
            route_mode='direct_degraded', exit_generation=direct['shared_exit_generation'])
        return baseline
    store = get_proxy_store(account_store)
    try:
        resolved = store.resolve(account_id, expected_binding_revision=expected_binding_revision,
                                 expected_config_revision=expected_config_revision, allow_probe=allow_probe)
        account, resource = resolved['account'], resolved['resource']
        if resource is None:
            if getattr(settings, 'crawler_proxy_enabled', False) and account['platform'] == 'dy':
                raise CollectionRouteError('PROXY_UNAVAILABLE')
            return None
        if not getattr(settings, 'crawler_proxy_enabled', False):
            raise CollectionRouteError('PROXY_UNAVAILABLE')
        transport = store.decrypt_snapshot(resource['id'], expected_revision=resource['config_revision'])
        observed_ip = resource.get('observed_exit_ip') or resource.get('last_exit_ip')
        observed_at = resource.get('observed_exit_at') or resource.get('last_exit_observed_at')
        contract = route_contract()
        from dataclasses import replace
        result = contract.validate_route_envelope(dict(schema_version=1, route_mode='proxy_required',
            platform=account['platform'], account_id=account_id, profile_key=contract.derive_profile_key(account_id),
            proxy_id=resource['id'], binding_revision=account['binding_revision'], config_revision=resource['config_revision'],
            endpoint=transport['endpoint'], credentials=transport['credentials'], expires_at=resource['expires_at'],
            observed_exit=None if observed_ip is None else dict(ip=observed_ip, observed_at=observed_at),
            scheduler=dict(db_path=str(settings.request_scheduler_db.resolve()),
                           scope_ids=list(contract.derive_scope_ids('dy', account_id, resource['id'], observed_ip)))), account_id=account_id)
        return replace(result, exit_generation=resource.get('shared_exit_generation') or 0)
    except Exception as exc:
        raise _safe_error(exc) from None


def acquire_route_execution(account_id, execution_id, owner_id, *, account_store=None,
                            account_handle=None, snapshot=None, allow_probe=False, recovery_claim=None, human_login_operation_id=None,
                            migration_id=None):
    from .crawler_child_runtime import require_browser_available
    require_browser_available(account_id, store=account_store, owner_id=owner_id,
        migration_id=migration_id, operation_id=execution_id)
    from backend.task_admission.resources import ExecutionLeaseBundle
    snapshot = snapshot or resolve_account_route(account_id, account_store=account_store, allow_probe=allow_probe)
    store = get_proxy_store(account_store) if snapshot and snapshot.route_mode not in {'direct_degraded', 'legacy_direct'} else None
    resource = store.get_public(snapshot.proxy_id) if snapshot and snapshot.route_mode not in {'direct_degraded', 'legacy_direct'} else None
    if snapshot:
        authorize_route_owner(account_id, snapshot.proxy_id if snapshot.route_mode not in {'direct_degraded', 'legacy_direct'} else None, owner_id, account_store=account_store)
    bundle = ExecutionLeaseBundle(execution_id, owner_id, account_id, snapshot)
    bundle.proxy_store = store
    bundle.migration_id = migration_id
    if store is not None:
        bundle.account_store = store.accounts
    elif account_store is not None:
        bundle.account_store = account_store
    else:
        from .crawler_account_store import crawler_account_store
        bundle.account_store = crawler_account_store
    if not bundle.acquire(account_handle=account_handle, max_active_accounts=min(resource['max_active_accounts'], max(1, int(settings.request_concurrency))) if resource else max(1, int(settings.request_concurrency))):
        raise CollectionRouteError('PROXY_BUSY')
    try:
        fresh = resolve_account_route(account_id, account_store=account_store,
            expected_binding_revision=snapshot.binding_revision if snapshot else None,
            expected_config_revision=snapshot.config_revision if snapshot else None, allow_probe=allow_probe)
        if (fresh is None) != (snapshot is None):
            raise CollectionRouteError('PROXY_REVISION_MISMATCH')
    except Exception:
        bundle.close()
        raise
    if snapshot is not None and (getattr(settings, 'crawler_safety_enabled', False) or snapshot.route_mode == 'legacy_direct'):
        try:
            from dataclasses import replace
            contract = route_contract()
            now = time.time()
            expiry = min(snapshot.expires_at or now + 300, now + 300)
            if snapshot.observed_exit_ip is None or (allow_probe and (snapshot.observed_exit_at is None or now-snapshot.observed_exit_at > settings.crawler_exit_observation_ttl)):
                original_snapshot = snapshot
                if snapshot.route_mode == 'direct_degraded':
                    bundle.snapshot = replace(snapshot, route_mode='legacy_direct',
                        scope_ids=contract.derive_scope_ids('dy', account_id, snapshot.proxy_id, snapshot.observed_exit_ip))
                try:
                    observed = _run_maintenance_probe(bundle)
                finally:
                    bundle.snapshot = original_snapshot
                if original_snapshot.observed_exit_ip is not None and observed.ip != original_snapshot.observed_exit_ip:
                    raise CollectionRouteError('PROXY_EXIT_CHANGED')
                snapshot = replace(snapshot, observed_exit_ip=observed.ip, observed_exit_at=observed.observed_at,
                    scope_ids=(tuple(sorted(['dy','dy:account:'+account_id,'dy:service_exit:'+observed.ip,'dy:exit:'+observed.ip])) if snapshot.route_mode in {'direct_degraded','legacy_direct'} else contract.derive_scope_ids('dy', account_id, snapshot.proxy_id, observed.ip)))
                bundle.snapshot = snapshot
            observation = bundle.account_store.consume_cached_exit(snapshot.observed_exit_ip, observed_at=snapshot.observed_exit_at, service_exit=snapshot.route_mode in {'direct_degraded', 'legacy_direct'})
            if not bundle.acquire_exit_capacity(snapshot.observed_exit_ip):
                bundle.close()
                raise CollectionRouteError('PROXY_BUSY')
            if human_login_operation_id and not recovery_claim:
                recovery_claim=bundle.account_store.begin_manual_recovery(account_id,human_login_operation_id,owner_id=owner_id,allow_active=True,observed_exit=snapshot.observed_exit_ip,generation=observation['shared_exit_generation'])
                if not recovery_claim:raise CollectionRouteError('PROXY_BUSY')
            bundle.manual_recovery_claim=recovery_claim if human_login_operation_id else None
            bundle.login_operation_id=human_login_operation_id
            mode = 'legacy_direct' if snapshot.route_mode == 'legacy_direct' else bundle.account_store.get(account_id).get('route_mode', 'proxy_required')
            managed = replace(snapshot, route_mode=mode, operation_id=secrets.token_hex(16), lease_id=secrets.token_hex(16),
                lease_version=1, owner_id=owner_id, context_id=secrets.token_hex(16),
                exit_generation=observation['shared_exit_generation'],
                safety_db_path=str(bundle.account_store.db_path.resolve()), recovery_claim=recovery_claim or '',
                login_bootstrap=bool(human_login_operation_id or allow_probe and not recovery_claim))
            managed = replace(managed, snapshot_digest=contract.snapshot_digest(managed))
            operation = RouteOperation(operation_id=managed.operation_id, lease_id=managed.lease_id,
                lease_version=managed.lease_version, owner_id=owner_id, account_id=account_id,
                profile_key=managed.profile_key, route_mode=managed.route_mode, proxy_id=managed.proxy_id,
                resource_revision=managed.config_revision, observed_exit=managed.observed_exit_ip or '',
                exit_generation=managed.exit_generation, expires_at=expiry, context_id=managed.context_id,
                scope_ids=managed.scope_ids, snapshot_digest=managed.snapshot_digest, proxy_expires_at=managed.expires_at or now+1200)
            bundle.route_operation_store = RouteOperationStore(str(settings.request_scheduler_db.resolve()))
            try:
                token = bundle.account_store.admit_route(account_id, managed.observed_exit_ip, managed.exit_generation, managed.operation_id, recovery_claim=recovery_claim, login_bootstrap=managed.login_bootstrap)
                bundle.add_closer(lambda: bundle.account_store.release_route_admission(token))
                bundle.route_operation_store.issue(operation)
                bundle.add_closer(lambda: end_route_operation(bundle))
            except Exception:
                bundle.close()
                raise
            bundle.snapshot = managed
        except Exception as exc:
            bundle.close()
            raise _safe_error(exc) from None
    return bundle


def parent_renew_ticket(bundle, prior):
    from .crawler_child_runtime import require_browser_available
    require_browser_available(bundle.account_id, store=getattr(bundle, 'account_store', None),
        owner_id=bundle.owner_id, migration_id=getattr(bundle, 'migration_id', None), operation_id=bundle.execution_id)
    snapshot = bundle.snapshot
    store = bundle.route_operation_store
    deadline = time.monotonic()+2
    while True:
        current = store.ticket(snapshot.operation_id,owner_id=bundle.owner_id,
            snapshot_digest=snapshot.snapshot_digest,context_id=snapshot.context_id)
        if current['lease_version'] > prior['lease_version']:
            return current
        pipe = getattr(bundle,'child_receipt_pipe',None)
        child_live = pipe is not None and pipe.process is not None and pipe.process.poll() is None
        if not child_live:
            return store.renew(secrets.token_hex(16),route_operation_id=snapshot.operation_id,
                owner_id=bundle.owner_id,lease_version=prior['lease_version'])
        if time.monotonic() >= deadline:
            raise CollectionRouteError('SUPPLEMENT_DEFERRED')
        time.sleep(.02)


def end_route_operation(bundle):
    """End only after later-registered child/browser drain callbacks succeeded."""
    store = getattr(bundle, 'route_operation_store', None)
    if store is None:
        return
    snapshot = bundle.snapshot
    import sqlite3
    with sqlite3.connect(store.db_path) as conn:
        row = store._row(conn.execute('SELECT * FROM route_operations WHERE operation_id=?',(snapshot.operation_id,)).fetchone())
    if row['state'] == 'ended':
        return
    store.transition(snapshot.operation_id,state='draining',owner_id=snapshot.owner_id,lease_version=row['lease_version'])
    store.transition(snapshot.operation_id,state='ended',owner_id=snapshot.owner_id,lease_version=row['lease_version'])


class RouteConfigPipe:
    """Dedicated read-only inherited FD, with bounded writer lifecycle."""
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.read_fd = self.write_fd = None
        self.thread = None
        self.error = None
        try:
            self.payload = route_contract().serialize_route(snapshot) if snapshot is not None else None
        except Exception as exc:
            raise _safe_error(exc) from None
        if self.payload is not None:
            self.read_fd, self.write_fd = os.pipe()
            os.set_blocking(self.write_fd, False)

    @property
    def pass_fds(self):
        return () if self.read_fd is None else (self.read_fd,)

    @property
    def env(self):
        return {} if self.read_fd is None else {route_contract().PROXY_CONFIG_FD_ENV: str(self.read_fd)}

    def start(self, process):
        if self.read_fd is None:
            return
        os.close(self.read_fd)
        self.read_fd = None
        def write():
            deadline = time.monotonic() + 5
            offset = 0
            poll = selectors.DefaultSelector()
            try:
                poll.register(self.write_fd, selectors.EVENT_WRITE)
                while offset < len(self.payload):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    if poll.select(remaining):
                        offset += os.write(self.write_fd, self.payload[offset:])
            except (OSError, TimeoutError, ValueError):
                self.error = CollectionRouteError('PROXY_CONFIG_INVALID')
                if process.poll() is None:
                    process.terminate()
            finally:
                poll.close()
                if self.write_fd is not None:
                    try:
                        os.close(self.write_fd)
                    except OSError:
                        pass
                    self.write_fd = None
                self.payload = None
        self.thread = threading.Thread(target=write, daemon=True)
        self.thread.start()

    def close(self):
        for name in ('read_fd', 'write_fd'):
            fd = getattr(self, name)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, name, None)
        if self.thread is not None:
            self.thread.join(timeout=5)
        self.payload = None


def load_child_route(account_id):
    required = os.environ.get("MEDIACRAWLER_PROXY_REQUIRED") == "true"
    if "MEDIACRAWLER_PROXY_CONFIG_FD" not in os.environ:
        if required or os.environ.get('MEDIACRAWLER_MANAGED_ROUTE') == '1':
            raise CollectionRouteError("PROXY_CONFIG_INVALID")
        return None
    try:
        return route_contract().load_route_from_env(account_id=account_id, proxy_required=required)
    except Exception as exc:
        raise _safe_error(exc) from None


def authorize_route_owner(account_id, proxy_id, owner_id, *, account_store=None, auth_store=None):
    if settings.app_auth_mode != 'required':
        return
    if not owner_id:
        raise CollectionRouteError('PROXY_UNAVAILABLE')
    from backend.application_auth.store import AuthStore
    from backend.application_auth.service import ApplicationAuthService
    auth = auth_store or AuthStore(settings.app_auth_db)
    try:
        principal = auth.principal_for_user(owner_id)
        account = get_proxy_store(account_store).accounts.get(account_id)
        if not account or (account.get('access_scope') != 'public' and not auth.owns_crawler_account(owner_id, account_id)):
            raise CollectionRouteError('PROXY_UNAVAILABLE')
        if proxy_id and not ApplicationAuthService(auth).can_use_crawler_proxy(principal, proxy_id):
            raise CollectionRouteError('PROXY_UNAVAILABLE')
    except CollectionRouteError:
        raise
    except Exception:
        raise CollectionRouteError('PROXY_UNAVAILABLE') from None


from contextlib import contextmanager
from contextvars import ContextVar
_current_route = ContextVar('crawler_route_context', default=None)


def get_current_route_context():
    """Convenience for the owner thread only; stream receives explicit bundle."""
    return _current_route.get()


@contextmanager
def route_execution_context(bundle):
    token = _current_route.set(bundle)
    try:
        yield bundle
    finally:
        _current_route.reset(token)


@contextmanager
def authorize_source_route(provenance, owner_id, execution_id, *, route_context=None, account_store=None, auth_store=None):
    if provenance is None:
        raise CollectionRouteError('SUPPLEMENT_DEFERRED')
    try:
        value = route_contract().validate_collection_route_provenance(provenance)
    except Exception as exc:
        raise _safe_error(exc) from None
    account_id = value['source_account_id']
    authorize_route_owner(account_id, value['proxy_id'] if value['route_mode'] not in {'direct_degraded', 'legacy_direct'} else None, owner_id, account_store=account_store, auth_store=auth_store)
    snapshot = resolve_account_route(account_id, account_store=account_store,
        expected_binding_revision=value['binding_revision'], expected_config_revision=value['config_revision'])
    if snapshot is None or value['route_mode'] == 'legacy_direct' and not value.get('operation_id'):
        raise CollectionRouteError('SUPPLEMENT_DEFERRED')
    if snapshot and 'exit_generation' in value and snapshot.exit_generation != value['exit_generation']:
        raise CollectionRouteError('PROXY_REVISION_MISMATCH')
    if snapshot and (snapshot.route_mode != value['route_mode'] and not (snapshot.route_mode == 'proxy_required' and value['route_mode'] == 'proxy_preferred') or snapshot.proxy_id != value['proxy_id'] or snapshot.observed_exit_ip is not None and snapshot.observed_exit_ip != value['observed_exit_ip']):
        raise CollectionRouteError('PROXY_EXIT_CHANGED')
    if route_context is not None and route_context.account_id == account_id:
        if any(getattr(route_context.snapshot, key, None) != getattr(snapshot, key, None) for key in ("account_id", "proxy_id", "binding_revision", "config_revision")):
            raise CollectionRouteError("PROXY_REVISION_MISMATCH")
        with route_context.borrow(execution_id=execution_id, owner_id=owner_id, account_id=account_id):
            yield route_context
        return
    bundle = acquire_route_execution(account_id, execution_id, owner_id, snapshot=snapshot, account_store=account_store)
    try:
        yield bundle
    finally:
        bundle.close()

_child_snapshot = None
_child_loaded = False


def child_route_snapshot(account_id):
    """Consume inherited config once; reopen uses identical in-memory snapshot."""
    global _child_snapshot, _child_loaded
    if not _child_loaded:
        _child_snapshot = load_child_route(account_id)
        _child_loaded = True
        if _child_snapshot is not None:
            # ACK intentionally contains only public protocol identity.
            import json
            print(json.dumps({'type': 'proxy_config_ack', 'schema_version': 1,
                              'proxy_id': _child_snapshot.proxy_id, 'code': 'OK'}), flush=True)
    if _child_snapshot is not None and _child_snapshot.account_id != account_id:
        raise CollectionRouteError('PROXY_CONFIG_INVALID')
    return _child_snapshot


def probe_env(bundle):
    trial_id = str(getattr(settings, 'crawler_proxy_trial_id', '') or '').strip()
    budget_identity = trial_id or bundle.execution_id
    budget_prefix = 'trial-' if trial_id else 'exec-'
    budget_digest = hashlib.sha256(budget_identity.encode()).hexdigest()
    result = {
        'MEDIACRAWLER_PROXY_PROBE_BUDGET_ID': budget_prefix + budget_digest,
        'MEDIACRAWLER_PROXY_ECHO_URL': settings.crawler_proxy_echo_url,
        'MEDIACRAWLER_PROXY_REQUIRED': 'true' if bundle.snapshot else 'false',
    }
    if trial_id:
        result['MEDIACRAWLER_PROXY_TRIAL_ID'] = trial_id
    return result


def raise_route_log_error(log):
    for code in ('PROXY_BUSY', 'SUPPLEMENT_DEFERRED', 'PROXY_REVISION_MISMATCH',
                 'PROXY_EXIT_CHANGED', 'PROXY_UNAVAILABLE', 'PROXY_AUTH_FAILED',
                 'PROXY_CONFIG_INVALID', 'PROBE_BUDGET_EXHAUSTED', 'SCHEDULER_POLICY_CONFLICT',
                 'PROXY_EXPIRED', 'PROXY_CONNECTION_REFUSED', 'PROXY_TRANSPORT_FAILED'):
        if code in log:
            raise CollectionRouteError(code)


class _AssetLoopSession:
    """Original account browser remains live on a dedicated async loop."""
    def __init__(self, bundle, *, lazy=False):
        import asyncio
        self.bundle = bundle
        self._snapshot_identity=getattr(bundle.snapshot,'operation_id','')
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.context = self.session = None
        self._opened = False
        self._opening_lock = asyncio.Lock()
        if lazy:
            return
        try:
            self.call(self.ensure_open())
        except Exception as exc:
            self.close()
            raise _safe_error(exc) from None

    def call(self, coroutine):
        import asyncio
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=310)

    async def ensure_open(self):
        async with self._opening_lock:
            if self._opened:
                return
            try:
                await self.open()
                self._opened = True
            except BaseException:
                if self.context is not None:
                    await self.context.close()
                    self.context = None
                elif self.session is not None:
                    await self.session.close()
                self.session = None
                raise

    def close(self):
        if self.loop.is_closed():
            return
        error = None
        try:
            if self.context is not None:
                self.call(self.context.close())
                self.context = None
        except Exception as exc:
            error = _safe_error(exc)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
            if not self.thread.is_alive():
                self.loop.close()
        if error is not None:
            raise error


def parent_account_policy(bundle):
    from .crawler_account_store import account_in_slow_mode
    accounts = getattr(bundle, 'account_store', None)
    account = accounts.get(bundle.account_id) if accounts is not None else None
    factor = settings.crawler_slow_factor if account and account_in_slow_mode(account) else 1
    return dict(min_interval=settings.crawler_account_min_interval * factor,
                per_minute=max(1, settings.crawler_account_requests_per_minute // factor))


def _asset_helper(bundle, *, lazy=False):
    child_active = getattr(bundle, "child_active", False)
    if not hasattr(bundle, "_asset_session_lock"):
        with bundle._lock:
            if not hasattr(bundle, "_asset_session_lock"):
                bundle._asset_session_lock = threading.Lock()
    with bundle._asset_session_lock:
        key = "_borrowed_child_session" if child_active else "_asset_browser"
        helper = getattr(bundle, key, None)
        if helper is not None and getattr(bundle.snapshot,'managed',False) and helper._snapshot_identity!=bundle.snapshot.operation_id:
            helper.close()
            helper=None
        if helper is None:
            helper = _BorrowedChildSession(bundle, lazy=lazy)
            bundle.add_closer(helper.close)
            setattr(bundle, key, helper)
    return helper


@contextmanager
def asset_request_slot(bundle, timeout=60):
    """Reauthorize every hop; proxy route must have real paired browser receipt."""
    snapshot = bundle.snapshot
    authorize_route_owner(bundle.account_id, snapshot.proxy_id if snapshot and snapshot.route_mode not in {'direct_degraded', 'legacy_direct'} else None, bundle.owner_id, account_store=getattr(bundle, "account_store", None))
    fresh = resolve_account_route(bundle.account_id, account_store=getattr(bundle, "account_store", None),
        expected_binding_revision=snapshot.binding_revision if snapshot else None,
        expected_config_revision=snapshot.config_revision if snapshot else None)
    if any(getattr(fresh, key, None) != getattr(snapshot, key, None) for key in ("account_id", "proxy_id", "binding_revision", "config_revision")):
        raise CollectionRouteError("PROXY_REVISION_MISMATCH")
    if snapshot is None:
        yield
        return
    helper = _asset_helper(bundle)
    child_active = getattr(bundle, "child_active", False)
    context = None
    try:
        if child_active:
            context = helper.call(helper.reserve(timeout))
        else:
            context = helper.session.slot("media", timeout=timeout)
            helper.call(context.__aenter__())
        yield
    except CollectionRouteError:
        raise
    except Exception as exc:
        raise _safe_error(exc) from None
    finally:
        if context is not None:
            original_error = sys.exc_info()[0] is not None
            try:
                helper.call(context.__aexit__(None, None, None))
            except Exception as exc:
                if not original_error:
                    raise _safe_error(exc) from None


def record_login_challenge(bundle):
    """Record one confirmed challenge while retaining the authorized human context."""
    if bundle is None or not getattr(bundle.snapshot,'managed',False):
        return
    with bundle._lock:
        if getattr(bundle,'account_risk_recorded',False):
            return
        accounts=bundle.account_store
        account_id=bundle.snapshot.account_id
        claim=getattr(bundle.snapshot,'recovery_claim','')
        with accounts._connect() as db:
            prior=db.execute("SELECT risk_recorded FROM crawler_manual_recovery_operations WHERE account_id=? AND claim=? AND state='claimed'",(account_id,claim)).fetchone() if claim else None
        if not prior or not prior[0]:
            from .account_rotation import AccountRotationManager
            AccountRotationManager(accounts).cool_down_after_risk(account_id)
            if prior:
                with accounts._connect() as db:
                    db.execute('UPDATE crawler_manual_recovery_operations SET risk_recorded=1 WHERE account_id=? AND claim=?',(account_id,claim))
        bundle.account_risk_recorded=True


class RouteReceiptPipe:
    """Public child-browser receipts, invalidated on EOF or process death."""
    def __init__(self, bundle):
        self.bundle = bundle
        self.snapshot = bundle.snapshot
        self.read_fd, self.write_fd = os.pipe()
        self.ack_read_fd, self.ack_write_fd = os.pipe()
        self.thread = None
        self.process = None
        self.stopped = threading.Event()

    @property
    def pass_fds(self):
        return (self.write_fd, self.ack_read_fd)

    @property
    def env(self):
        return {'MEDIACRAWLER_PROXY_RECEIPT_FD': str(self.write_fd),
                'MEDIACRAWLER_ROUTE_ACK_FD': str(self.ack_read_fd),
                'MEDIACRAWLER_MANAGED_ROUTE': '1' if self.snapshot.managed else '0'}

    def start(self, process):
        import json
        self.process = process
        if self.snapshot.managed:
            self.bundle.route_operation_store.record_child(self.snapshot.operation_id,owner_id=self.snapshot.owner_id,lease_version=self.snapshot.lease_version,child_pgid=process.pid)
        os.close(self.write_fd)
        self.write_fd = None
        os.close(self.ack_read_fd)
        self.ack_read_fd = None
        self.bundle.child_receipt_pipe = self
        if self.snapshot.managed and callable(getattr(self.bundle, 'add_closer', None)):
            self.bundle.add_closer(self.drain)
        def read():
            buffer = bytearray()
            poll = selectors.DefaultSelector()
            try:
                poll.register(self.read_fd, selectors.EVENT_READ)
                while not self.stopped.is_set():
                    if not poll.select(.1):
                        if process.poll() is not None:
                            break
                        continue
                    chunk = os.read(self.read_fd, 4096)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                    if len(buffer) > 8192:
                        break
                    while b'\n' in buffer:
                        line, _, tail = buffer.partition(b'\n')
                        buffer[:] = tail
                        if len(line) > 4096:
                            return
                        def unique(pairs):
                            result = {}
                            for key, entry in pairs:
                                if key in result:
                                    raise CollectionRouteError("PROXY_CONFIG_INVALID")
                                result[key] = entry
                            return result
                        value = json.loads(line, object_pairs_hook=unique)
                        snapshot = self.snapshot
                        if isinstance(value,dict) and value.get('type')=='browser_guard_stopped':
                            fields={'type','operation_id','owner_id','lease_version','snapshot_digest','context_id','reason'}
                            if (set(value)!=fields or not snapshot.managed or type(value['lease_version']) is not int
                                    or type(value['reason']) is not str or value['reason'] not in BROWSER_GUARD_REASONS
                                    or any(type(value[key]) is not str or value[key]!=getattr(snapshot,key) for key in fields-{'type','reason','lease_version'})
                                    or value['lease_version']!=snapshot.lease_version):
                                return
                            with self.bundle._lock:
                                if not getattr(self.bundle,'browser_guard_first_reason',None):
                                    self.bundle.browser_guard_first_reason=value['reason']
                                    logging.getLogger(__name__).warning('browser_guard_stopped operation_id=%s reason=%s',snapshot.operation_id,value['reason'])
                            continue
                        if isinstance(value,dict) and value.get('type')=='route_fault':
                            if set(value)!={'type','event'}:
                                return
                            try:
                                consume_route_event('COLLECTION_ROUTE_EVENT '+json.dumps(value['event'],allow_nan=False),self.bundle)
                            except CollectionRouteError as fault:
                                self.bundle.route_error = fault.code
                            continue
                        if isinstance(value, dict) and value.get('type') == 'account_risk':
                            fields = {'type','operation_id','owner_id','lease_version','snapshot_digest','context_id','reason'}
                            if (set(value) != fields or value['reason'] != 'ACCOUNT_CHALLENGE'
                                    or not snapshot.managed or any(value[key] != getattr(snapshot,key) for key in fields - {'type','reason'})):
                                return
                            record_login_challenge(self.bundle)
                            continue
                        if isinstance(value, dict) and value.get('type') == 'route_renew':
                            if set(value) != {'type','operation_id','route_operation_id','owner_id','lease_version'} or value['route_operation_id'] != snapshot.operation_id:
                                return
                            ack = self.bundle.route_operation_store.renew(value['operation_id'], route_operation_id=value['route_operation_id'], owner_id=value['owner_id'], lease_version=value['lease_version'])
                            os.write(self.ack_write_fd, (json.dumps(ack, allow_nan=False) + '\n').encode())
                            continue
                        if isinstance(value, dict) and value.get('type') == 'route_ack':
                            expected = {'type', 'operation_id', 'owner_id', 'lease_version', 'snapshot_digest', 'context_id'}
                            if set(value) != expected or not snapshot.managed:
                                return
                            result = self.bundle.route_operation_store.ack(value['operation_id'],
                                owner_id=value['owner_id'], lease_version=value['lease_version'],
                                snapshot_digest=value['snapshot_digest'], context_id=value['context_id'])
                            os.write(self.ack_write_fd, (json.dumps(result['ack'], allow_nan=False) + '\n').encode())
                            continue
                        fields = {'schema_version', 'type', 'platform', 'account_id', 'proxy_id', 'binding_revision', 'config_revision', 'context_id', 'ip', 'observed_at'}
                        if snapshot.managed:
                            fields |= {'operation_id', 'lease_id', 'lease_version', 'owner_id', 'snapshot_digest', 'exit_generation'}
                            if any(value.get(key) != getattr(snapshot, key) for key in ('operation_id', 'lease_id', 'lease_version', 'owner_id', 'snapshot_digest', 'exit_generation')):
                                return
                        if not isinstance(value, dict) or set(value) != fields:
                            return
                        if type(value['schema_version']) is not int or value['schema_version'] != 1:
                            return
                        if value['type'] not in {'probe_receipt', 'receipt_closed', 'probe_closed'}:
                            return
                        identity_fields = ('platform', 'account_id', 'proxy_id', 'binding_revision', 'config_revision')
                        if any(value[key] != getattr(snapshot, key) for key in identity_fields):
                            return
                        import re
                        if not isinstance(value['context_id'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value['context_id']):
                            return
                        if type(value['binding_revision']) is not int or type(value['config_revision']) is not int:
                            return
                        if value['type'] in {'receipt_closed', 'probe_closed'}:
                            with self.bundle._lock:
                                previous = getattr(self.bundle, 'child_receipt', None)
                                if previous is None or previous.context_id == value['context_id']:
                                    self.bundle.child_receipt = None
                            continue
                        receipt = route_contract().ProbeReceipt(snapshot.proxy_id, snapshot.config_revision, value['context_id'], value['ip'], value['observed_at'], snapshot.exit_generation)
                        route_contract().require_probe_receipt(snapshot, receipt, value['context_id'])
                        with self.bundle._lock:
                            if process.poll() is not None:
                                return
                            self.bundle.child_receipt = receipt
                        store = getattr(self.bundle, "proxy_store", None)
                        if snapshot.route_mode in {'direct_degraded', 'legacy_direct'}:
                            self.bundle.account_store.observe_shared_exit(receipt.ip, observed_at=receipt.observed_at, service_exit=True)
                        elif store is not None:
                            store.observe_exit(snapshot.proxy_id, receipt.ip, observed_at=receipt.observed_at)
            except Exception:
                # Invalid input or persistence failure invalidates the receipt;
                # downstream use reports SUPPLEMENT_DEFERRED, never success.
                pass
            finally:
                poll.close()
                with self.bundle._lock:
                    if getattr(self.bundle,'child_receipt_pipe',None) is self:
                        self.bundle.child_receipt = None
        self.thread = threading.Thread(target=read, daemon=True)
        self.thread.start()

    def drain(self):
        snapshot = self.snapshot
        store = getattr(self.bundle, 'route_operation_store', None)
        version = snapshot.lease_version
        if store:
            import sqlite3
            with sqlite3.connect(store.db_path) as conn:
                row = store._row(conn.execute('SELECT * FROM route_operations WHERE operation_id=?',(snapshot.operation_id,)).fetchone())
            if row['state'] == 'ended':
                return
            version = row['lease_version']
            store.transition(snapshot.operation_id, state='draining', owner_id=snapshot.owner_id, lease_version=version)
        if self.process is not None:
            _stop_probe_process_group(self.process)
        assert_profile_browser_stopped(snapshot.account_id)
        self.close()
        if store:
            store.transition(snapshot.operation_id, state='ended', owner_id=snapshot.owner_id, lease_version=version)

    def receipt(self):
        with self.bundle._lock:
            receipt = getattr(self.bundle, 'child_receipt', None)
            if receipt is None or self.process is None or self.process.poll() is not None or self.stopped.is_set():
                raise CollectionRouteError('SUPPLEMENT_DEFERRED')
            try:
                route_contract().require_probe_receipt(self.snapshot, receipt, receipt.context_id,
                    ttl=getattr(settings, 'crawler_proxy_probe_ttl', 300))
            except Exception:
                raise CollectionRouteError('SUPPLEMENT_DEFERRED') from None
            return receipt

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=1)
        for name in ('read_fd', 'write_fd', 'ack_read_fd', 'ack_write_fd'):
            fd = getattr(self, name)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, name, None)
        if getattr(self.bundle,'child_receipt_pipe',None) is self:
            self.bundle.child_receipt = None


def renew_same_route_phase(bundle, *, allowed_borrowers=1):
    """A closed original browser may yield a fresh lease on the SAME source route."""
    from dataclasses import replace
    old=bundle.snapshot
    if getattr(bundle,'_borrowers',0)>allowed_borrowers:
        raise CollectionRouteError('SUPPLEMENT_DEFERRED')
    pipe=getattr(bundle,'child_receipt_pipe',None)
    if pipe is not None:
        if pipe.process is not None and pipe.process.poll() is None:
            raise CollectionRouteError('SUPPLEMENT_DEFERRED')
        pipe.drain()
    else:
        assert_profile_browser_stopped(bundle.account_id)
        end_route_operation(bundle)
    current=resolve_account_route(bundle.account_id,account_store=bundle.account_store,
        expected_binding_revision=old.binding_revision,expected_config_revision=old.config_revision)
    if (current is None or current.proxy_id!=old.proxy_id or current.observed_exit_ip!=old.observed_exit_ip
            or current.exit_generation!=old.exit_generation):
        raise CollectionRouteError('PROXY_REVISION_MISMATCH')
    bundle.account_store.release_route_admission(old.operation_id)
    new=replace(old,operation_id=secrets.token_hex(16),lease_id=secrets.token_hex(16),lease_version=1,
        context_id=secrets.token_hex(16),snapshot_digest='',login_bootstrap=False,recovery_claim='')
    new=replace(new,snapshot_digest=route_contract().snapshot_digest(new))
    expiry=min(new.expires_at,time.time()+300)
    operation=RouteOperation(operation_id=new.operation_id,lease_id=new.lease_id,lease_version=1,owner_id=new.owner_id,
        account_id=new.account_id,profile_key=new.profile_key,route_mode=new.route_mode,proxy_id=new.proxy_id,
        resource_revision=new.config_revision,observed_exit=new.observed_exit_ip,exit_generation=new.exit_generation,
        expires_at=expiry,context_id=new.context_id,scope_ids=new.scope_ids,snapshot_digest=new.snapshot_digest,proxy_expires_at=new.expires_at)
    token=bundle.account_store.admit_route(new.account_id,new.observed_exit_ip,new.exit_generation,new.operation_id)
    bundle.add_closer(lambda:bundle.account_store.release_route_admission(token))
    bundle.route_operation_store.issue(operation)
    bundle.snapshot=new
    bundle.route_error=None


def prepare_collection_child(bundle, *, allowed_borrowers=0, timeout=15):
    """Drain a supplementary context after every other media borrower finishes."""
    process=getattr(bundle,'asset_browser_process',None)
    if process is None:
        return
    deadline=time.monotonic()+timeout
    while getattr(bundle,'_borrowers',0)>allowed_borrowers:
        if time.monotonic()>=deadline:
            raise CollectionRouteError('PROXY_BUSY')
        time.sleep(.02)
    pipe=bundle.child_receipt_pipe
    close_asset_browser_child(process,pipe)
    bundle.child_active=False
    if bundle.snapshot.managed:
        renew_same_route_phase(bundle,allowed_borrowers=allowed_borrowers)
    bundle.asset_browser_process=None


def close_asset_browser_child(process, pipe):
    """Graceful close first; cleanup proof still fences every held source lock."""
    import subprocess
    if process.poll() is None:
        try:
            process.stdin.write('close\n');process.stdin.flush();process.stdin.close()
        except (BrokenPipeError,OSError):
            pass
        try:process.wait(timeout=15)
        except subprocess.TimeoutExpired:_stop_probe_process_group(process)
    pipe.drain()
    for stream in (process.stdin,process.stdout,process.stderr):
        if stream is not None and not stream.closed:stream.close()


def start_asset_browser_child(bundle):
    """Use the configured crawler interpreter, never import browser deps in App."""
    import subprocess
    from pathlib import Path
    from .crawler_browser import account_browser_env,scheduler_env
    store=getattr(bundle,'route_operation_store',None)
    if store is not None:
        with __import__('sqlite3').connect(store.db_path) as db:
            row=store._row(db.execute('SELECT * FROM route_operations WHERE operation_id=?',(bundle.snapshot.operation_id,)).fetchone())
        if row['child_pgid'] is not None or row['state']=='ended':
            renew_same_route_phase(bundle)
    else:
        old_pipe=getattr(bundle,'child_receipt_pipe',None)
        if old_pipe is not None:
            if old_pipe.process is not None and old_pipe.process.poll() is None:
                raise CollectionRouteError('SUPPLEMENT_DEFERRED')
            old_pipe.drain()
    config=RouteConfigPipe(bundle.snapshot);receipt=RouteReceiptPipe(bundle)
    from .crawler_child_runtime import child_environment, child_process_options, authority_for
    env=child_environment()
    for name in ('MEDIACRAWLER_PROXY_CONFIG_FD','MEDIACRAWLER_PROXY_RECEIPT_FD','MEDIACRAWLER_ROUTE_ACK_FD','MEDIACRAWLER_MANAGED_ROUTE'):
        env.pop(name,None)
    env.update(account_browser_env(bundle.account_id));env.update(scheduler_env());env.update(probe_env(bundle))
    env.update(config.env);env.update(receipt.env)
    env['MEDIACRAWLER_DIR']=str(settings.media_crawler_dir.resolve())
    env['MEDIACRAWLER_ASSET_ACCOUNT_POLICY']=json.dumps(parent_account_policy(bundle),allow_nan=False)
    env['PYTHONPATH']=str(settings.root_dir)+os.pathsep+env.get('PYTHONPATH','')
    helper=Path(__file__).resolve().parents[2]/'scripts/crawler_proxy_probe.py'
    process=None
    authority=authority_for(bundle)
    env.update(authority.env)
    try:
        process=subprocess.Popen([str(settings.crawler_login_python),str(helper),'--account-id',bundle.account_id,'--hold-context'],
            cwd=settings.media_crawler_dir,env=env,pass_fds=(*bundle.pass_fds,*config.pass_fds,*receipt.pass_fds,*authority.pass_fds),
            stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True,start_new_session=True,**child_process_options())
        authority.bind_child(process.pid)
        config.start(process);receipt.start(process)
        bundle.add_closer(authority.close)
        bundle.add_closer(lambda:close_asset_browser_child(process,receipt))
        bundle.add_closer(config.close)
        bundle.asset_browser_process=process
        bundle.child_active=True
    except Exception:
        if process is not None:_stop_probe_process_group(process)
        authority.close()
        config.close();receipt.close()
        raise


class _BorrowedChildSession(_AssetLoopSession):
    """Use actual live child browser sample; compare a fresh HTTP-only echo."""
    async def open(self):
        pipe=getattr(self.bundle,'child_receipt_pipe',None)
        live=pipe is not None and pipe.process is not None and pipe.process.poll() is None and not pipe.stopped.is_set()
        if not live:
            start_asset_browser_child(self.bundle)
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                try:
                    self.bundle.child_receipt_pipe.receipt()
                    break
                except CollectionRouteError:
                    if self.bundle.child_receipt_pipe.process.poll() is not None:
                        raise CollectionRouteError('SUPPLEMENT_DEFERRED')
                    await __import__('asyncio').sleep(.05)
            else:
                raise CollectionRouteError('SUPPLEMENT_DEFERRED')
        path = str(settings.media_crawler_dir.resolve())
        if path not in sys.path:
            sys.path.insert(0, path)
        route_contract()
        from tools.bound_proxy import BoundProxySession
        self.session = BoundProxySession(self.bundle.snapshot,
            echo_url=getattr(settings, 'crawler_proxy_echo_url', ''),
            budget_id=probe_env(self.bundle)['MEDIACRAWLER_PROXY_PROBE_BUDGET_ID'],
            ttl=getattr(settings, 'crawler_proxy_probe_ttl', 300),
            max_calls=getattr(settings, 'crawler_proxy_probe_budget', 96),
            trial_id=getattr(settings, 'crawler_proxy_trial_id', ''), reuse_existing_policy=True,
            account_policy=parent_account_policy(self.bundle),
            ack=self.bundle.route_operation_store.ticket(self.bundle.snapshot.operation_id, owner_id=self.bundle.owner_id,
                snapshot_digest=self.bundle.snapshot.snapshot_digest,
                context_id=self.bundle.snapshot.context_id) if self.bundle.snapshot.managed else None,
            lease_renewer=(lambda prior: parent_renew_ticket(self.bundle,prior)) if self.bundle.snapshot.managed else None)
        from tools.trial_limits import from_env
        self.session.trial_limits = from_env(self.bundle.snapshot.scheduler_db_path, env={
            **os.environ, 'MEDIACRAWLER_PROXY_TRIAL_ID': getattr(settings, 'crawler_proxy_trial_id', '')})
        await self.ensure()

    async def ensure(self):
        pipe = getattr(self.bundle, 'child_receipt_pipe', None)
        if pipe is None:
            raise CollectionRouteError('SUPPLEMENT_DEFERRED')
        receipt = pipe.receipt()
        self.session.budget.reserve('boundary')
        ip = await self.session._http_sample()
        if ip != receipt.ip:
            raise CollectionRouteError('PROXY_EXIT_CHANGED')
        self.session.budget.confirm_baseline(self.bundle.snapshot, ip)
        self.session.receipt = self.session.guard.confirm_pair(receipt.context_id, receipt.ip, ip, observed_at=receipt.observed_at)
        self.session.context_id = receipt.context_id
        self.session.context = pipe  # Liveness sentinel only; no fake browser probe.
        self.session._gate = self.session._make_gate()
        return self.session._gate

    async def reserve(self, timeout):
        gate = await self.ensure()
        slot = self.session.slot('media', timeout=timeout, max_wait=timeout)
        await slot.__aenter__()
        try:
            getattr(self.bundle, 'child_receipt_pipe').receipt()
        except BaseException:
            # Reservation succeeded: release capacity even when the live browser
            # receipt disappears before transmission. Started budget stays spent.
            await slot.__aexit__(*sys.exc_info())
            raise
        return slot

    def close(self):
        try:
            if self.session:
                self.call(self.session.close())
        finally:
            super().close()


def clear_route_stop(proxy_id, expected_revision):
    """Administrative maintenance recovery, protected by the edit descriptor."""
    import sqlite3
    from backend.task_admission.resources import proxy_edit_lease
    with proxy_edit_lease(proxy_id) as acquired:
        if not acquired:
            raise CollectionRouteError('PROXY_BUSY')
        store = get_proxy_store()
        resource = store.get_public(proxy_id)
        if resource is None or resource['config_revision'] != expected_revision:
            raise CollectionRouteError('PROXY_REVISION_MISMATCH')
        if resource['status'] != 'active' or resource.get('stop_reason'):
            raise CollectionRouteError('PROXY_UNAVAILABLE')
        with sqlite3.connect(settings.request_scheduler_db) as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='collection_route_health' AND type='table'").fetchone()
            if exists:
                db.execute('DELETE FROM collection_route_health WHERE proxy_id=? AND config_revision=?', (proxy_id, expected_revision))


def make_bound_proxy_session(snapshot, *, login_bootstrap=False):
    """Application-owned runtime checker bridges shared health to each action."""
    path = str(settings.media_crawler_dir.resolve())
    if path not in sys.path:
        sys.path.insert(0, path)
    route_contract()
    from tools.bound_proxy import BoundProxySession
    def check():
        try:
            current = resolve_account_route(snapshot.account_id,
                expected_binding_revision=snapshot.binding_revision,
                expected_config_revision=snapshot.config_revision, allow_probe=True)
            if current.proxy_id != snapshot.proxy_id:
                return 'PROXY_REVISION_MISMATCH'
            if current.observed_exit_ip is not None and snapshot.observed_exit_ip is not None and current.observed_exit_ip != snapshot.observed_exit_ip:
                return 'PROXY_EXIT_CHANGED'
        except CollectionRouteError as exc:
            return exc.code
        return False
    return BoundProxySession(snapshot, stop_checker=None if os.getenv('CRAWLER_RESTRICTED_CHILD') == '1' else check, login_bootstrap=login_bootstrap,
        trial_id=getattr(settings, 'crawler_proxy_trial_id', ''))


def publish_route_stop(proxy_id, config_revision, code='PROXY_UNAVAILABLE'):
    """Health-only stop marker; intentionally writable during shared use."""
    import sqlite3
    code = CollectionRouteError(code).code
    with sqlite3.connect(settings.request_scheduler_db, timeout=.2) as db:
        db.execute('CREATE TABLE IF NOT EXISTS collection_route_health (proxy_id TEXT NOT NULL, config_revision INTEGER NOT NULL, stop_code TEXT NOT NULL, PRIMARY KEY(proxy_id,config_revision))')
        db.execute('INSERT OR REPLACE INTO collection_route_health VALUES(?,?,?)', (proxy_id,config_revision,code))


def route_health_notifier(proxy_id, config_revision, status, stop_reason):
    if status != 'active' or stop_reason:
        publish_route_stop(proxy_id, config_revision,
                           'PROXY_EXIT_CHANGED' if status == 'exit_changed' else 'PROXY_UNAVAILABLE')


def activate_proxy_route(proxy_id, *, expected_revision, account_store=None, **updates):
    """Explicit admin recovery; subsequent execution still needs a new probe."""
    from backend.task_admission.resources import proxy_edit_lease
    with proxy_edit_lease(proxy_id) as acquired:
        if not acquired:
            raise CollectionRouteError('PROXY_BUSY')
        store = get_proxy_store(account_store)
        resource = store.get_public(proxy_id)
        if resource is None or resource['config_revision'] != expected_revision:
            raise CollectionRouteError('PROXY_REVISION_MISMATCH')
        expiry = updates.get('expires_at', resource['expires_at'])
        if expiry is not None and expiry <= time.time():
            raise CollectionRouteError('PROXY_UNAVAILABLE')
        updates['status'] = 'active'
        resource = store.update(proxy_id, expected_revision=expected_revision, **updates)
        resource = store.set_health(proxy_id, status='active', stop_reason='', cooldown_until=None)
        import sqlite3
        with sqlite3.connect(settings.request_scheduler_db, timeout=.2) as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='collection_route_health' AND type='table'").fetchone()
            if exists:
                db.execute('DELETE FROM collection_route_health WHERE proxy_id=? AND config_revision=?', (proxy_id, resource['config_revision']))
        store.record_event(proxy_id, 'maintenance_recovered', code='PROBE_REQUIRED')
        return resource


def probe_account_route(account_id, owner_id, *, expected_binding_revision=None,
                        expected_config_revision=None, proxy_id=None, account_store=None,
                        auth_store=None):
    """Admin-only diagnostic probe of the actual account profile, no business."""
    if settings.app_auth_mode == 'required':
        from backend.application_auth.store import AuthStore
        auth = auth_store or AuthStore(settings.app_auth_db)
        try:
            if not auth.principal_for_user(owner_id).is_admin:
                raise CollectionRouteError('PROXY_UNAVAILABLE')
        except Exception as exc:
            raise _safe_error(exc) from None
    snapshot = resolve_account_route(account_id, account_store=account_store,
        expected_binding_revision=expected_binding_revision,
        expected_config_revision=expected_config_revision, allow_probe=True)
    if snapshot is None or proxy_id is not None and snapshot.proxy_id != proxy_id:
        raise CollectionRouteError('PROXY_CONFIG_INVALID')
    from uuid import uuid4
    bundle = acquire_route_execution(account_id, 'maintenance-' + uuid4().hex, owner_id,
        account_store=account_store, snapshot=snapshot, allow_probe=True)
    try:
        receipt = _run_maintenance_probe(bundle)
        # Recheck frozen binding/config after the child closed its actual browser.
        resolve_account_route(account_id, account_store=account_store,
            expected_binding_revision=snapshot.binding_revision,
            expected_config_revision=snapshot.config_revision, allow_probe=True)
        if snapshot.route_mode in {'legacy_direct', 'direct_degraded'}:
            bundle.account_store.observe_shared_exit(receipt.ip, observed_at=receipt.observed_at, service_exit=True)
            resource = {'status': 'active'}
        else:
            bundle.proxy_store.observe_exit(snapshot.proxy_id, receipt.ip, observed_at=receipt.observed_at)
            resource = bundle.proxy_store.get_public(snapshot.proxy_id)
        return dict(schema_version=1, proxy_id=snapshot.proxy_id, account_id=account_id,
            config_revision=snapshot.config_revision, binding_revision=snapshot.binding_revision,
            observed_exit_ip=receipt.ip, observed_at=receipt.observed_at,
            status=resource['status'], business_enabled=False)
    except Exception as exc:
        raise _safe_error(exc) from None
    finally:
        bundle.close()


def _consume_probe_diagnostic(line,bundle):
    """Diagnostics are optional, private and never authorize a route or result."""
    def unique(pairs):
        value={}
        for key,entry in pairs:
            if key in value:raise ValueError('duplicate')
            value[key]=entry
        return value
    try:
        value=json.loads(line,object_pairs_hook=unique)
        if not isinstance(value,dict) or set(value)!={'stage','error_class','category'}:return False
        allowed={'stage':{'unknown','browser_startup','browser_echo','http_echo','login_check','cleanup'},
            'error_class':{'unknown','route','timeout','playwright','httpx','os','runtime','value'},
            'category':{'unknown','timeout','proxy_auth','transport'}}
        if any(type(value[key]) is not str or value[key] not in allowed[key] for key in allowed):return False
    except (ValueError,TypeError):return False
    import re
    execution_id=getattr(bundle,'execution_id','')
    if not isinstance(execution_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',execution_id):execution_id='unknown'
    logging.getLogger(__name__).warning('proxy_probe_diagnostic execution_id=%s stage=%s error_class=%s category=%s',execution_id,value['stage'],value['error_class'],value['category'])
    return True


def _run_maintenance_probe(bundle, *, timeout=None, check_login=False, expected_identity='', verified_result=False):
    """Bounded subprocess protocol; dependency imports stay in crawler Python."""
    import json
    import subprocess
    from pathlib import Path
    from .crawler_browser import account_browser_env, scheduler_env
    if timeout is None:
        timeout = min(300, settings.crawler_login_v2_preparation_timeout_seconds + 90) if check_login else 45
    snapshot = bundle.snapshot
    pipe = RouteConfigPipe(snapshot)
    receipt_pipe = RouteReceiptPipe(bundle) if snapshot.managed else None
    process = None
    authority = None
    verified_receipt = None
    poll = selectors.DefaultSelector()
    try:
        from .crawler_child_runtime import child_environment, child_process_options, authority_for
        env = child_environment()
        for name in ('MEDIACRAWLER_PROXY_CONFIG_FD', 'MEDIACRAWLER_PROXY_RECEIPT_FD', 'MEDIACRAWLER_ROUTE_ACK_FD', 'MEDIACRAWLER_MANAGED_ROUTE',
                     'MEDIACRAWLER_PROXY_REQUIRED', 'MEDIACRAWLER_PROXY_PROBE_BUDGET_ID'):
            env.pop(name, None)
        env.update(account_browser_env(bundle.account_id))
        env.update(scheduler_env())
        env.update(probe_env(bundle))
        env.update(pipe.env)
        if receipt_pipe:
            env.update(receipt_pipe.env)
        env['MEDIACRAWLER_DIR'] = str(settings.media_crawler_dir.resolve())
        env['PYTHONUNBUFFERED'] = '1'
        helper = Path(__file__).resolve().parents[2] / 'scripts' / 'crawler_proxy_probe.py'
        env['PYTHONPATH'] = str(settings.root_dir) + os.pathsep + env.get('PYTHONPATH', '')
        command = [str(settings.crawler_login_python), str(helper), '--account-id', bundle.account_id]
        if check_login:
            command += ['--check-login', '--expected-identity', expected_identity]
        authority = authority_for(bundle, maintenance_only=not check_login)
        env.update(authority.env)
        process = subprocess.Popen(command, cwd=settings.media_crawler_dir,
            env=env, pass_fds=(*bundle.pass_fds, *pipe.pass_fds, *(receipt_pipe.pass_fds if receipt_pipe else ()), *authority.pass_fds), stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True, **child_process_options())
        authority.bind_child(process.pid)
        pipe.start(process)
        if receipt_pipe:
            receipt_pipe.start(process)
        poll.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        data = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CollectionRouteError('PROXY_UNAVAILABLE')
            if not poll.select(min(remaining, .25)):
                continue
            chunk = os.read(process.stdout.fileno(), 8192)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 65536:
                raise CollectionRouteError('PROXY_CONFIG_INVALID')
        process.wait(timeout=max(.1, deadline-time.monotonic()))
        if pipe.error:
            raise pipe.error
        result = None
        diagnostic_seen=False
        for line in data.decode('utf-8').splitlines():
            if line.startswith('PROXY_PROBE_DIAGNOSTIC '):
                if not diagnostic_seen:
                    diagnostic_seen=_consume_probe_diagnostic(line[len('PROXY_PROBE_DIAGNOSTIC '):],bundle)
                continue
            if line.startswith('PROXY_PROBE_ERROR '):
                value = json.loads(line[len('PROXY_PROBE_ERROR '):])
                if set(value) != {'code'}:
                    raise CollectionRouteError('PROXY_CONFIG_INVALID')
                raise CollectionRouteError(value['code'])
            if line.startswith('PROXY_PROBE_RESULT '):
                if result is not None:
                    raise CollectionRouteError('PROXY_CONFIG_INVALID')
                result = json.loads(line[len('PROXY_PROBE_RESULT '):])
        fields = {'schema_version','account_id','proxy_id','binding_revision','config_revision','context_id','ip','observed_at'}
        if check_login:
            fields.add('login_result')
        if process.returncode != 0 or not isinstance(result, dict) or set(result) != fields:
            raise CollectionRouteError('PROXY_UNAVAILABLE')
        import re
        if (type(result['schema_version']) is not int or result['schema_version'] != 1
            or type(result['binding_revision']) is not int or type(result['config_revision']) is not int
            or not isinstance(result['context_id'], str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', result['context_id'])
            or any(result[key] != getattr(snapshot,key) for key in ('account_id','proxy_id','binding_revision','config_revision'))):
            raise CollectionRouteError('PROXY_CONFIG_INVALID')
        receipt = route_contract().ProbeReceipt(snapshot.proxy_id,snapshot.config_revision,
            result['context_id'],result['ip'],result['observed_at'],snapshot.exit_generation)
        # Maintenance observes a changed exit; it must not authorize business
        # against the old baseline. Validate identity, expiry and receipt freshness.
        from dataclasses import replace
        diagnostic_snapshot = replace(snapshot, observed_exit_ip=None, observed_exit_at=None)
        route_contract().require_probe_receipt(diagnostic_snapshot,receipt,result['context_id'])
        if check_login:
            if result['login_result'] not in {'success', 'challenge', 'uncertain'}:
                raise CollectionRouteError('PROXY_CONFIG_INVALID')
            if verified_result:
                verified_receipt = receipt
                return {'login_result': result['login_result'], 'receipt': receipt,
                        'child_pid': process.pid, 'process_start': authority.start_identity,
                        'shutdown_verified': True, 'authority_verified': bool(snapshot.managed)}
            return result['login_result']
        verified_receipt=receipt
        return receipt
    except CollectionRouteError:
        raise
    except (OSError, subprocess.TimeoutExpired):
        raise CollectionRouteError('PROXY_UNAVAILABLE') from None
    except Exception as exc:
        raise _safe_error(exc) from None
    finally:
        poll.close()
        try:
            if process is not None:
                _stop_probe_process_group(process)
                if verified_receipt is not None:
                    assert_profile_browser_stopped(snapshot.account_id)
                    _retire_verified_probe_uncertainty(bundle,verified_receipt)
        finally:
            if authority is not None:
                authority.close()
            if process is not None and process.stdout is not None:
                process.stdout.close()
            pipe.close()
            if receipt_pipe:
                receipt_pipe.close()


def _retire_verified_probe_uncertainty(bundle,receipt):
    """Parent-only compare/delete after complete validated probe and group drain."""
    snapshot=bundle.snapshot
    if snapshot.observed_exit_ip is None or receipt.ip!=snapshot.observed_exit_ip:
        return
    now=time.time()
    if snapshot.expires_at is None or snapshot.expires_at<=now or not 0<=now-receipt.observed_at<=settings.crawler_proxy_probe_ttl:
        return
    accounts=getattr(bundle,'account_store',None)
    if accounts is None:return
    with accounts._connect() as db:
        db.execute('ATTACH DATABASE ? AS probe_health',(snapshot.scheduler_db_path,))
        db.execute('BEGIN IMMEDIATE')
        if snapshot.route_mode in {'direct_degraded', 'legacy_direct'}:
            account = db.execute('SELECT * FROM crawler_accounts WHERE id=?', (snapshot.account_id,)).fetchone()
            shared = db.execute('SELECT * FROM crawler_shared_exits WHERE exit_key=?', (receipt.ip,)).fetchone()
            if (not account or account['binding_revision'] != snapshot.binding_revision
                    or account['shared_exit_key'] != receipt.ip
                    or account['shared_exit_generation'] != snapshot.exit_generation
                    or account['status'] == 'disabled' or account['pause_reason']
                    or account['paused_until'] is not None and account['paused_until'] > now
                    or not shared or shared['generation'] != snapshot.exit_generation
                    or shared['pause_reason'] or shared['paused_until'] is not None and shared['paused_until'] > now):
                return
            if snapshot.route_mode == 'legacy_direct' and account['proxy_id']:
                return
            if snapshot.route_mode == 'direct_degraded' and account['route_mode'] != 'direct_degraded':
                return
            from tools.bound_proxy import route_health_key
            key = route_health_key(snapshot)
            exists = db.execute("SELECT 1 FROM probe_health.sqlite_master WHERE type='table' AND name='collection_route_health'").fetchone()
            if exists:
                db.execute("DELETE FROM probe_health.collection_route_health WHERE proxy_id=? AND config_revision=? AND stop_code IN ('PROXY_UNAVAILABLE','PROXY_TRANSPORT_FAILED','PROXY_CONNECTION_REFUSED','PROXY_AUTH_FAILED','PROXY_EXPIRED')",
                           (key, snapshot.config_revision))
            return
        resource=db.execute('SELECT * FROM proxy_resources WHERE id=?',(snapshot.proxy_id,)).fetchone()
        account=db.execute('SELECT proxy_id,binding_revision FROM crawler_accounts WHERE id=?',(snapshot.account_id,)).fetchone()
        if (not account or tuple(account)!=(snapshot.proxy_id,snapshot.binding_revision) or not resource
                or resource['config_revision']!=snapshot.config_revision or resource['observed_exit_ip']!=receipt.ip
                or resource['status']!='active' or resource['stop_reason'] or resource['pause_reason']
                or resource['expires_at'] is None or resource['expires_at']<=now
                or resource['cooldown_until'] is not None and resource['cooldown_until']>now):return
        exists=db.execute("SELECT 1 FROM probe_health.sqlite_master WHERE type='table' AND name='collection_route_health'").fetchone()
        if exists:
            db.execute("DELETE FROM probe_health.collection_route_health WHERE proxy_id=? AND config_revision=? AND stop_code='PROXY_UNAVAILABLE'",(snapshot.proxy_id,snapshot.config_revision))


def _stop_probe_process_group(process):
    """Close the exact session created for this child, even after leader exit."""
    import signal
    import subprocess
    pgid = process.pid  # Popen(start_new_session=True) guarantees this identity.
    def alive():
        # Zombies cannot retain lease FDs. Do not confuse an exited group leader
        # with a live browser descendant, or PID existence with open resources.
        listing = subprocess.run(['ps','-axo','pgid=,stat='], capture_output=True,
            text=True, timeout=2, check=True).stdout
        for line in listing.splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[0] == str(pgid) and not fields[1].startswith('Z'):
                return True
        return False
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 2
    while alive() and time.monotonic() < deadline:
        time.sleep(.02)
    if alive():
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        raise CollectionRouteError('PROXY_UNAVAILABLE') from None
    deadline = time.monotonic() + 3
    while alive() and time.monotonic() < deadline:
        time.sleep(.02)
    if alive():
        raise CollectionRouteError('PROXY_UNAVAILABLE')


def download_asset_hop(bundle, url, headers, timeout, verify, on_chunk, *, media_limits=None, media_token=None):
    """Run setup, gate and one HTTP hop/body against one absolute deadline."""
    started_at = time.monotonic()
    import asyncio
    import httpx
    import math
    from pathlib import Path
    from urllib.parse import urlsplit
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise CollectionRouteError('PROXY_CONFIG_INVALID')
    if verify is not True and not (isinstance(verify, str) and Path(verify).is_file()):
        raise CollectionRouteError('PROXY_CONFIG_INVALID')
    deadline = started_at + timeout
    target = urlsplit(url)
    if target.scheme not in {'http', 'https'} or target.username or target.password:
        raise CollectionRouteError('PROXY_CONFIG_INVALID')
    snapshot = bundle.snapshot
    if snapshot is None:
        raise CollectionRouteError('PROXY_CONFIG_INVALID')
    authorize_route_owner(bundle.account_id, snapshot.proxy_id if snapshot.route_mode not in {'direct_degraded', 'legacy_direct'} else None, bundle.owner_id,
                          account_store=getattr(bundle, 'account_store', None))
    current = resolve_account_route(bundle.account_id, account_store=getattr(bundle, 'account_store', None),
        expected_binding_revision=snapshot.binding_revision, expected_config_revision=snapshot.config_revision)
    if current.proxy_id != snapshot.proxy_id:
        raise CollectionRouteError('PROXY_REVISION_MISMATCH')
    helper = _asset_helper(bundle, lazy=True)
    async def request():
        def remaining():
            duration = deadline - time.monotonic()
            if media_limits is not None:
                duration = min(duration, media_limits.media_timeout(media_token))
            if duration <= 0:
                raise TimeoutError()
            return duration
        async with asyncio.timeout(remaining()) as timer:
            await helper.ensure_open()
            active_snapshot=bundle.snapshot
            duration = remaining()
            timer.reschedule(asyncio.get_running_loop().time() + duration)
            slot = None
            try:
                if getattr(bundle, 'child_active', False):
                    slot = await helper.reserve(duration)
                else:
                    slot = helper.session.slot('media', timeout=duration, max_wait=duration)
                    await slot.__aenter__()
                duration = remaining()
                timer.reschedule(asyncio.get_running_loop().time() + duration)
                async with httpx.AsyncClient(proxy=snapshot.httpx_proxy(), trust_env=False,
                        verify=verify, follow_redirects=False, timeout=duration) as client:
                    async with client.stream('GET', url, headers=headers) as response:
                        if response.status_code == 407:
                            raise CollectionRouteError('PROXY_AUTH_FAILED')
                        public_headers = {key: value for key, value in response.headers.items()
                            if key.lower() in {'location', 'content-length', 'content-type'}}
                        if 200 <= response.status_code < 300:
                            async for chunk in response.aiter_bytes():
                                helper.session.check_stop()
                                if getattr(bundle, 'child_active', False):
                                    bundle.child_receipt_pipe.receipt()
                                on_chunk(chunk)
                        return response.status_code, public_headers
            finally:
                if slot is not None:
                    await slot.__aexit__(*sys.exc_info())
    try:
        return helper.call(request())
    except CollectionRouteError:
        raise
    except httpx.ProxyError as exc:
        raise CollectionRouteError('PROXY_AUTH_FAILED' if '407' in str(exc) else 'PROXY_UNAVAILABLE') from None
    except (TimeoutError, httpx.TimeoutException, httpx.RequestError):
        raise CollectionRouteError('PROXY_UNAVAILABLE') from None
    except Exception as exc:
        raise _safe_error(exc) from None


def stop_source_route(provenance, code, *, route_context=None):
    """Stop only the still-current frozen source; never mutate a newer route."""
    try:
        source = route_contract().validate_collection_route_provenance(provenance)
    except Exception as exc:
        raise _safe_error(exc) from None
    error = CollectionRouteError(code)
    if source['route_mode'] not in {'proxy_required','proxy_preferred','direct_degraded'} or error.code in {'PROXY_BUSY', 'SUPPLEMENT_DEFERRED'}:
        return False
    if route_context is None:
        return False
    snapshot = route_context.snapshot
    if snapshot is None or any(getattr(snapshot, name) != source[field] for name, field in (('account_id', 'source_account_id'), ('proxy_id', 'proxy_id'), ('binding_revision', 'binding_revision'), ('config_revision', 'config_revision'))):
        return False
    if hasattr(route_context, '_active') and not route_context._active:
        return False
    if not route_context.pass_fds:
        return False
    try:
        authorize_route_owner(snapshot.account_id, snapshot.proxy_id, route_context.owner_id, account_store=getattr(route_context, 'account_store', None))
    except CollectionRouteError:
        return False
    store = get_proxy_store(getattr(route_context, 'account_store', None))
    account = store.accounts.get(source['source_account_id'])
    if not account or account.get('proxy_id') != source['proxy_id'] or account.get('binding_revision') != source['binding_revision']:
        return False
    status = 'exit_changed' if error.code == 'PROXY_EXIT_CHANGED' else 'unavailable'
    with store.accounts._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        updated = db.execute('UPDATE proxy_resources SET status=?,stop_reason=?,updated_at=? WHERE id=? AND config_revision=?',
            (status,error.code,time.time(),source['proxy_id'],source['config_revision'])).rowcount
    if updated:
        publish_route_stop(source['proxy_id'],source['config_revision'],error.code)
    return bool(updated)


def consume_route_event(line, bundle):
    """Consume exact public child telemetry alongside the login JSON protocol."""
    import json
    import math
    prefix = 'COLLECTION_ROUTE_EVENT '
    if isinstance(line, bytes):
        line = line.decode('utf-8')
    if not line.startswith(prefix):
        return False
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise CollectionRouteError('PROXY_CONFIG_INVALID')
                result[key] = value
            return result
        event = json.loads(line[len(prefix):], object_pairs_hook=unique)
        fields = {'schema_version','code','proxy_id','config_revision','account_id','observed_exit_ip','observed_at'}
        snapshot = bundle.snapshot if bundle else None
        if not isinstance(event, dict) or set(event) != fields or snapshot is None:
            raise CollectionRouteError('PROXY_CONFIG_INVALID')
        if type(event['schema_version']) is not int or event['schema_version'] != 1 or type(event['config_revision']) is not int:
            raise CollectionRouteError('PROXY_CONFIG_INVALID')
        if any(event[name] != getattr(snapshot,name) for name in ('proxy_id','config_revision','account_id')):
            raise CollectionRouteError('PROXY_REVISION_MISMATCH')
        at = event['observed_at']
        if type(at) not in (int,float) or not math.isfinite(at) or at < 0 or at > time.time()+1:
            raise CollectionRouteError('PROXY_CONFIG_INVALID')
        store = getattr(bundle,'proxy_store',None)
        if event['code'] == 'ROUTE_OBSERVED':
            ip = route_contract().canonical_exit_ip(event['observed_exit_ip'])
            if snapshot.route_mode in {'direct_degraded', 'legacy_direct'}:
                if ip != snapshot.observed_exit_ip:
                    raise CollectionRouteError('PROXY_EXIT_CHANGED')
                bundle.account_store.observe_shared_exit(ip, observed_at=at, service_exit=True)
                return True
            current = store.get_public(snapshot.proxy_id)
            if current['config_revision'] != snapshot.config_revision:
                raise CollectionRouteError('PROXY_REVISION_MISMATCH')
            if current['observed_exit_at'] is None or at >= current['observed_exit_at']:
                store.observe_exit(snapshot.proxy_id, ip, observed_at=at)
            return True
        error = CollectionRouteError(event['code'])
        if error.code != event['code'] or event['observed_exit_ip'] is not None:
            raise CollectionRouteError('PROXY_CONFIG_INVALID')
        if snapshot.route_mode != 'legacy_direct':
            publish_route_stop(snapshot.proxy_id,snapshot.config_revision,error.code)
        if snapshot.route_mode in {'direct_degraded', 'legacy_direct'}:
            raise error
        if store is None:
            raise error
        store.set_health(snapshot.proxy_id,status='exit_changed' if error.code=='PROXY_EXIT_CHANGED' else 'unavailable',stop_reason=error.code)
        raise error
    except CollectionRouteError:
        raise
    except Exception as exc:
        raise _safe_error(exc) from None


def check_recovery_route(account_id, claim, *, account_store=None, owner_id='local-user'):
    """One claimed read-only login observation on the existing account Profile."""
    from .crawler_account_store import crawler_account_store
    accounts = account_store or crawler_account_store
    account = accounts.get(account_id)
    if not account or not account.get('platform_account_id'):
        return None
    bundle = acquire_route_execution(account_id, 'recovery-' + secrets.token_hex(16),
        owner_id, account_store=accounts,
        allow_probe=True, recovery_claim=claim)
    try:
        outcome = _run_maintenance_probe(bundle, check_login=True, expected_identity=account['platform_account_id'])
        return True if outcome == 'success' else False if outcome == 'challenge' else None
    finally:
        bundle.close()



def probe_direct_route(account_id, *, account_store=None, owner_id='local-user'):
    """Echo-only direct observation under the original Profile/account lease."""
    from .crawler_account_store import crawler_account_store
    from backend.task_admission.resources import ExecutionLeaseBundle
    accounts = account_store or crawler_account_store
    authorize_route_owner(account_id, None, owner_id, account_store=accounts)
    contract = route_contract()
    snapshot = contract.BoundRoute('dy', account_id, contract.derive_profile_key(account_id),
        'service-direct', 1, 1, 'direct.invalid', 1, '', '', time.time()+300, None, None,
        str(settings.request_scheduler_db.resolve()), contract.derive_scope_ids('dy', account_id, 'service-direct'),
        route_mode='legacy_direct')
    bundle = ExecutionLeaseBundle('direct-probe-' + secrets.token_hex(16), owner_id, account_id, snapshot)
    bundle.account_store = accounts
    bundle.proxy_store = None
    if not bundle.acquire():
        raise CollectionRouteError('PROXY_BUSY')
    try:
        receipt = _run_maintenance_probe(bundle)
        return accounts.observe_shared_exit(receipt.ip, observed_at=receipt.observed_at, service_exit=True)
    finally:
        bundle.close()

def prepare_direct_degraded(account_id, transport_error, health_pool_exhausted, *, account_store=None,
                            old_context_drained=False, owner_id='local-user'):
    """Explicit new route after drained transport incident; never alter a required account."""
    from .crawler_account_store import crawler_account_store
    accounts = account_store or crawler_account_store
    account = accounts.get(account_id)
    if (not settings.crawler_safety_enabled or not settings.crawler_direct_degrade_enabled or not account
            or account.get('proxy_required') or account.get('route_mode') != 'proxy_preferred'):
        return False
    if (type(health_pool_exhausted) is not bool or type(old_context_drained) is not bool
            or not health_pool_exhausted or not old_context_drained
            or transport_error not in {'PROXY_EXPIRED', 'PROXY_CONNECTION_REFUSED', 'PROXY_TRANSPORT_FAILED', 'PROXY_AUTH_FAILED'}
            or account.get('pause_reason')):
        return False
    direct = accounts.get_direct_route(account_id)
    if direct is None:
        direct = probe_direct_route(account_id, account_store=accounts, owner_id=owner_id)
    if not direct or not direct_degraded_allowed(transport_error=transport_error,
            health_pool_exhausted=health_pool_exhausted, risk_signal=bool(account.get('pause_reason')),
            observed_exit=direct['observed_exit_ip'], shared_exit_key=direct['shared_exit_key'],
            old_context_drained=old_context_drained):
        return False
    # Existing account lease + original persistent browser adapter lock enforce drain.
    with accounts._connect() as conn:
        if conn.execute("SELECT 1 FROM crawler_route_admissions WHERE account_id=? AND state='active'", (account_id,)).fetchone():
            return False
    accounts.set_route_state(account_id, state='active', route_mode='direct_degraded',
        shared_exit_key=direct['shared_exit_key'], generation=direct['shared_exit_generation'])
    bundle = None
    try:
        bundle = acquire_route_execution(account_id, 'direct-check-' + secrets.token_hex(16), owner_id,
            account_store=accounts, allow_probe=True)
        outcome = _run_maintenance_probe(bundle, check_login=True, expected_identity=account.get('platform_account_id') or '')
        if outcome != 'success':
            accounts.set_route_state(account_id, state='manual_required', route_mode='direct_degraded', pause_reason='LOGIN_REVALIDATION_REQUIRED')
            return False
        accounts.mark_direct_validated(account_id, bundle.snapshot.exit_generation)
        return True
    except Exception:
        accounts.set_route_state(account_id, state='manual_required', route_mode='direct_degraded', pause_reason='LOGIN_REVALIDATION_REQUIRED')
        return False
    finally:
        if bundle is not None:
            bundle.close()


def refresh_existing_route(account_id, *, account_store=None, owner_id='local-user'):
    """Refresh a stale known route by echo only; TTL never triggers fallback."""
    from .crawler_account_store import crawler_account_store
    from backend.task_admission.resources import ExecutionLeaseBundle
    accounts=account_store or crawler_account_store
    account=accounts.get(account_id)
    if not account or account['status']!='active' or account.get('route_state')!='active' or account.get('pause_reason'):
        return False
    if account.get('route_mode')=='direct_degraded' or not account.get('proxy_id') and not account.get('proxy_required'):
        with accounts._connect() as db:
            row=db.execute('SELECT * FROM crawler_shared_exits WHERE exit_key=?',(account.get('shared_exit_key'),)).fetchone()
        if (not row or row['pause_reason'] or row['generation']!=account.get('shared_exit_generation')
                or account.get('route_mode')=='direct_degraded' and account.get('validated_direct_generation')!=row['generation']):
            return False
        contract=route_contract()
        snapshot=contract.BoundRoute('dy',account_id,contract.derive_profile_key(account_id),'service-direct',
            account['binding_revision'],1,'direct.invalid',1,'','',time.time()+1200,row['exit_key'],row['observed_at'],
            str(settings.request_scheduler_db.resolve()),tuple(sorted(['dy','dy:account:'+account_id,'dy:service_exit:'+row['exit_key'],'dy:exit:'+row['exit_key']])),route_mode='legacy_direct')
        store=None
    else:
        store=get_proxy_store(accounts)
        resolved=store.resolve(account_id,allow_probe=True)
        resource=resolved['resource']
        if (not resource or resource['status']!='active' or resource['stop_reason']
                or resource['expires_at'] is None or resource['expires_at']<=time.time()
                or account['validated_proxy_binding_revision']!=account['binding_revision']
                or account['validated_proxy_config_revision']!=resource['config_revision']):
            return False
        snapshot=resolve_account_route(account_id,account_store=accounts,allow_probe=True)
    authorize_route_owner(account_id,None if store is None else snapshot.proxy_id,owner_id,account_store=accounts)
    with accounts._connect() as db:
        if db.execute("SELECT 1 FROM crawler_route_admissions WHERE account_id=? AND state='active'",(account_id,)).fetchone():
            return False
    bundle=ExecutionLeaseBundle('refresh-'+secrets.token_hex(16),owner_id,account_id,snapshot)
    bundle.account_store=accounts;bundle.proxy_store=store
    if not bundle.acquire(max_active_accounts=1):
        return False
    try:
        receipt=_run_maintenance_probe(bundle)
        if snapshot.observed_exit_ip is not None and receipt.ip!=snapshot.observed_exit_ip:
            if store is not None:store.set_health(snapshot.proxy_id,status='exit_changed',stop_reason='PROXY_EXIT_CHANGED')
            raise CollectionRouteError('PROXY_EXIT_CHANGED')
        if store is not None:
            store.observe_exit(snapshot.proxy_id,receipt.ip,observed_at=receipt.observed_at)
        else:
            accounts.observe_shared_exit(receipt.ip,observed_at=receipt.observed_at,service_exit=True)
        return True
    finally:
        bundle.close()


def assert_profile_browser_stopped(account_id):
    """Check both archived Chromium ownership and current Firefox/profile fences."""
    import fcntl
    import re
    import socket
    key = route_contract().derive_profile_key(account_id)
    directories = {settings.crawler_browser_profile_root.expanduser().resolve() / key,
                   (settings.crawler_camoufox_state_root / 'profiles').expanduser().resolve() / key}
    for directory in directories:
        for name in ('SingletonLock', 'lock'):
            path = directory / name
            if path.is_symlink():
                target = os.readlink(path)
                pattern = r'(.+)-(\d+)' if name == 'SingletonLock' else r'(.+):\+?(\d+)'
                match = re.fullmatch(pattern, target)
                if not match or match[1] not in {socket.gethostname(), socket.gethostname().split('.')[0], '127.0.0.1', '127.0.1.1', 'localhost'}:
                    raise CollectionRouteError('PROXY_BUSY')
                try:
                    os.kill(int(match[2]), 0)
                except ProcessLookupError:
                    continue
                except (PermissionError, ValueError):
                    raise CollectionRouteError('PROXY_BUSY') from None
                raise CollectionRouteError('PROXY_BUSY')
            if path.exists():
                raise CollectionRouteError('PROXY_BUSY')
        for name in ('adapter.lock', '.parentlock', 'parent.lock'):
            path = directory / name
            if path.is_symlink():
                raise CollectionRouteError('PROXY_BUSY')
            if not path.exists():
                continue
            try:
                fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
                with os.fdopen(fd, 'a') as handle:
                    if name == 'adapter.lock':
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    else:
                        fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, ValueError):
                raise CollectionRouteError('PROXY_BUSY') from None


def reconcile_stopped_route_admissions(accounts, account_id, fresh_account_handle):
    """A fresh account FD plus independently free Profile proves local child drain."""
    import fcntl
    from backend.task_admission.resources import account_lock_path
    expected=account_lock_path('dy',account_id)
    actual=os.fstat(fresh_account_handle.fileno());wanted=expected.stat()
    if (actual.st_dev,actual.st_ino)!=(wanted.st_dev,wanted.st_ino):
        raise CollectionRouteError('PROXY_BUSY')
    directory=settings.crawler_browser_profile_root.expanduser().resolve()/route_contract().derive_profile_key(account_id)
    profile_handle=None
    try:
        assert_profile_browser_stopped(account_id)
        import sqlite3,subprocess
        path=settings.request_scheduler_db
        if path.exists():
            with sqlite3.connect(path) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='route_operations'").fetchone() and 'child_pgid' in {row[1] for row in db.execute('PRAGMA table_info(route_operations)')}:
                    groups={row[0] for row in db.execute("SELECT child_pgid FROM route_operations WHERE account_id=? AND state<>'ended' AND child_pgid IS NOT NULL",(account_id,))}
                else:groups=set()
            listing=subprocess.run(['ps','-axo','pgid=,stat='],capture_output=True,text=True,timeout=2,check=True).stdout
            for line in listing.splitlines():
                fields=line.split()
                if len(fields)==2 and fields[0].isdigit() and int(fields[0]) in groups and not fields[1].startswith('Z'):
                    raise CollectionRouteError('PROXY_BUSY')
        if directory.exists():
            profile_handle=(directory/'adapter.lock').open('a+b')
            try:fcntl.flock(profile_handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise CollectionRouteError('PROXY_BUSY') from None
        return accounts.release_admissions_for_stopped_account(account_id)
    finally:
        if profile_handle is not None:profile_handle.close()
