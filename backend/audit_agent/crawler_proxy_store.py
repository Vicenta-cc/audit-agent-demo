"""Persistent proxy resources. Public reads never decrypt transport secrets.

Configuration mutations require an external nonblocking exclusive lease. The caller
may supply a mutation_guard to enforce that lease; SQLite only protects atomic data
invariants and does not represent live browser/process occupancy.
"""
from __future__ import annotations

import ipaddress
from contextlib import contextmanager
from functools import wraps
import math
import re
import time
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

from .crawler_account_store import CrawlerAccountStore
from .config import settings

PROXY_STATUSES = frozenset({'active', 'maintenance', 'unavailable', 'expired', 'exit_changed'})
PUBLIC_PROXY_FIELDS = frozenset({
    'id', 'provider', 'platform', 'display_name', 'config_revision', 'status',
    'expires_at', 'max_bound_accounts', 'max_active_accounts', 'cooldown_until',
    'observed_exit_ip', 'observed_exit_at', 'stop_reason', 'created_at', 'updated_at',
    'bound_accounts', 'has_credentials', 'endpoint', 'shared_exit_key',
    'shared_exit_generation', 'paused_until', 'pause_reason',
})
_UNSET = object()


def _epoch(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('PROXY_INVALID_TIME')
    return float(value)


def _limit(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError('PROXY_INVALID_CAPACITY')
    return value


def _endpoint(value):
    if isinstance(value, str):
        try:
            parsed = urlsplit(value)
            if parsed.username is not None or parsed.password is not None or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
                raise ValueError('PROXY_INVALID_ENDPOINT')
            value = {'scheme': parsed.scheme, 'host': parsed.hostname, 'port': parsed.port}
        except ValueError:
            raise ValueError('PROXY_INVALID_ENDPOINT') from None
    if not isinstance(value, dict) or set(value) != {'scheme', 'host', 'port'} or value['scheme'] != 'http':
        raise ValueError('PROXY_INVALID_ENDPOINT')
    host, port = value['host'], value['port']
    if not isinstance(host, str) or not host or host != host.strip() or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('PROXY_INVALID_ENDPOINT')
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        try:
            host = host.encode('idna').decode('ascii').lower()
        except UnicodeError:
            raise ValueError('PROXY_INVALID_ENDPOINT') from None
        labels = host.rstrip('.').split('.')
        if len(host) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
            raise ValueError('PROXY_INVALID_ENDPOINT')
    return {'scheme': 'http', 'host': host, 'port': port}


def _credentials(value):
    if value is None:
        return {'username': '', 'password': ''}
    if not isinstance(value, dict) or set(value) != {'username', 'password'} or any(not isinstance(v, str) for v in value.values()):
        raise ValueError('PROXY_INVALID_CREDENTIALS')
    if any(len(v) > 4096 or any(c in v for c in '\x00\r\n') for v in value.values()):
        raise ValueError('PROXY_INVALID_CREDENTIALS')
    if bool(value['username']) != bool(value['password']):
        raise ValueError('PROXY_INVALID_CREDENTIALS')
    return dict(value)


def _notify_health_after_commit(method):
    @wraps(method)
    def write(self, *args, **kwargs):
        resource = method(self, *args, **kwargs)
        if self.health_notifier is not None:
            self.health_notifier(resource['id'], resource['config_revision'], resource['status'], resource['stop_reason'])
        return resource
    return write


class ProxyTransportSecrets(dict):
    """Internal decrypted transport with a redacted default representation."""
    def __repr__(self):
        return "<ProxyTransportSecrets redacted>"

    __str__ = __repr__


class CrawlerProxyStore:
    def __init__(self, account_store=None, *, db_path: Path | None = None, cipher=None, mutation_guard=None, health_notifier=None, max_active_limit=None):
        if account_store is not None and db_path is not None and Path(account_store.db_path) != Path(db_path):
            raise ValueError('PROXY_DATABASE_MISMATCH')
        self.accounts = account_store or CrawlerAccountStore(db_path)
        self.db_path = self.accounts.db_path
        self._lock = self.accounts._lock
        if cipher is None:
            from .auth_state_cipher import auth_state_cipher
            cipher = auth_state_cipher
        self.cipher = cipher
        self.mutation_guard = mutation_guard
        self.health_notifier = health_notifier
        self.max_active_limit = _limit(settings.request_concurrency if max_active_limit is None else max_active_limit)
        self._init_db()

    def _init_db(self):
        with self._lock, self.accounts._connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS proxy_resources (
                    id TEXT PRIMARY KEY, provider TEXT NOT NULL, platform TEXT NOT NULL,
                    display_name TEXT NOT NULL, transport_ciphertext TEXT NOT NULL,
                    has_credentials INTEGER NOT NULL DEFAULT 0,
                    config_revision INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'active', expires_at REAL,
                    max_bound_accounts INTEGER NOT NULL DEFAULT 1,
                    max_active_accounts INTEGER NOT NULL DEFAULT 1,
                    cooldown_until REAL, observed_exit_ip TEXT, observed_exit_at REAL,
                    shared_exit_key TEXT NOT NULL DEFAULT '', shared_exit_generation INTEGER NOT NULL DEFAULT 0,
                    paused_until REAL, pause_reason TEXT NOT NULL DEFAULT '',
                    stop_reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resource_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, proxy_id TEXT NOT NULL,
                    kind TEXT NOT NULL, code TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_proxy_resource_events ON resource_events(proxy_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_crawler_accounts_proxy ON crawler_accounts(proxy_id);
            ''')
            columns = {row[1] for row in conn.execute('PRAGMA table_info(proxy_resources)')}
            for name, definition in (('shared_exit_key', "TEXT NOT NULL DEFAULT ''"), ('shared_exit_generation', 'INTEGER NOT NULL DEFAULT 0'), ('paused_until', 'REAL'), ('pause_reason', "TEXT NOT NULL DEFAULT ''")):
                if name not in columns:
                    conn.execute(f'ALTER TABLE proxy_resources ADD COLUMN {name} {definition}')

    @contextmanager
    def _guard(self, operation, proxy_id, account_id=None):
        if self.mutation_guard is None:
            yield
            return
        # Runtime owns account→proxy ordering and all relevant resource locks.
        # Entering the callback keeps its FD leases alive through transaction commit.
        with self.mutation_guard(operation, proxy_id, account_id):
            yield

    @staticmethod
    def _check_revision(row, expected_revision):
        if expected_revision is None or row['config_revision'] != expected_revision:
            raise ValueError('PROXY_REVISION_MISMATCH')

    def _public(self, conn, row):
        if row is None:
            return None
        data = dict(row)
        data['bound_accounts'] = conn.execute('SELECT COUNT(*) FROM crawler_accounts WHERE proxy_id=?', (row['id'],)).fetchone()[0]
        data['has_credentials'] = bool(data['has_credentials'])
        data['endpoint'] = self.cipher.decrypt(row['transport_ciphertext'])['endpoint']
        return {key: data[key] for key in PUBLIC_PROXY_FIELDS}

    def get(self, proxy_id):
        with self._lock, self.accounts._connect() as conn:
            return self._public(conn, conn.execute('SELECT * FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone())

    get_public = get

    def list(self, *, platform=None):
        with self._lock, self.accounts._connect() as conn:
            rows = conn.execute('SELECT * FROM proxy_resources WHERE (? IS NULL OR platform=?) ORDER BY created_at,id', (platform, platform)).fetchall()
            return [self._public(conn, row) for row in rows]

    def create(self, *, display_name, endpoint, credentials=None, provider='wukong', platform='dy', expires_at=None, max_bound_accounts=1, max_active_accounts=1):
        if platform != 'dy':
            raise ValueError('PROXY_PLATFORM_UNSUPPORTED')
        if provider not in ('manual', 'wukong'):
            raise ValueError('PROXY_PROVIDER_UNSUPPORTED')
        name = self.accounts._validate_display_name(display_name)
        transport = {'endpoint': _endpoint(endpoint), 'credentials': _credentials(credentials)}
        bound, active = _limit(max_bound_accounts), _limit(max_active_accounts)
        if active > bound or active > self.max_active_limit:
            raise ValueError('PROXY_INVALID_CAPACITY')
        expiry = _epoch(expires_at)
        now, proxy_id = time.time(), uuid4().hex[:12]
        ciphertext = self.cipher.encrypt(transport)
        with self._lock, self.accounts._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('''INSERT INTO proxy_resources (id,provider,platform,display_name,transport_ciphertext,has_credentials,
                expires_at,max_bound_accounts,max_active_accounts,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (proxy_id, provider, platform, name, ciphertext, bool(transport['credentials']['password']), expiry, bound, active, now, now))
            return self._public(conn, conn.execute('SELECT * FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone())

    @_notify_health_after_commit
    def update(self, proxy_id, *, expected_revision, endpoint=_UNSET, credentials=_UNSET, clear_credentials=False,
               display_name=_UNSET, provider=_UNSET, expires_at=_UNSET, max_bound_accounts=_UNSET,
               max_active_accounts=_UNSET, status=_UNSET, active_count=0):
        with self._guard('update', proxy_id), self._lock, self.accounts._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone()
            if row is None:
                raise ValueError('PROXY_NOT_FOUND')
            self._check_revision(row, expected_revision)
            updates = {}
            if credentials is not _UNSET and clear_credentials:
                raise ValueError('PROXY_INVALID_CREDENTIALS')
            if endpoint is not _UNSET or credentials is not _UNSET or clear_credentials:
                transport = self.cipher.decrypt(row['transport_ciphertext'])
                if endpoint is not _UNSET:
                    transport['endpoint'] = _endpoint(endpoint)
                # Omitted/null/empty credentials preserve; explicit clear is the only deletion path.
                if credentials not in (_UNSET, None, {}) and credentials != {'username': '', 'password': ''}:
                    transport['credentials'] = _credentials(credentials)
                if clear_credentials:
                    transport['credentials'] = _credentials(None)
                if transport != self.cipher.decrypt(row['transport_ciphertext']):
                    updates['observed_exit_ip'] = None
                    updates['observed_exit_at'] = None
                    conn.execute('INSERT INTO resource_events(proxy_id,kind,code,created_at) VALUES (?,?,?,?)', (proxy_id, 'route_configuration_changed', 'PROBE_REQUIRED', time.time()))
                    updates['transport_ciphertext'] = self.cipher.encrypt(transport)
                    updates['has_credentials'] = bool(transport['credentials']['password'])
            if display_name is not _UNSET:
                updates['display_name'] = self.accounts._validate_display_name(display_name)
            if provider is not _UNSET:
                if provider not in ('manual', 'wukong'):
                    raise ValueError('PROXY_PROVIDER_UNSUPPORTED')
                updates['provider'] = provider
            if expires_at is not _UNSET:
                updates['expires_at'] = _epoch(expires_at)
            bound = row['max_bound_accounts'] if max_bound_accounts is _UNSET else _limit(max_bound_accounts)
            active = row['max_active_accounts'] if max_active_accounts is _UNSET else _limit(max_active_accounts)
            count = conn.execute('SELECT COUNT(*) FROM crawler_accounts WHERE proxy_id=?', (proxy_id,)).fetchone()[0]
            if isinstance(active_count, bool) or not isinstance(active_count, int) or active_count < 0:
                raise ValueError('PROXY_INVALID_CAPACITY')
            if bound < count or active < active_count or active > bound or active > self.max_active_limit:
                raise ValueError('PROXY_CAPACITY_CONFLICT')
            updates.update(max_bound_accounts=bound, max_active_accounts=active)
            if status is not _UNSET:
                if status not in PROXY_STATUSES:
                    raise ValueError('PROXY_INVALID_STATUS')
                updates['status'] = status
            updates = {key: value for key, value in updates.items() if value != row[key]}
            if updates:
                updates.update(config_revision=row['config_revision'] + 1, updated_at=time.time())
                conn.execute('UPDATE proxy_resources SET ' + ','.join(key+'=?' for key in updates) + ' WHERE id=?', (*updates.values(), proxy_id))
            return self._public(conn, conn.execute('SELECT * FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone())

    def bind(self, account_id, proxy_id, *, expected_binding_revision):
        with self._guard('bind', proxy_id, account_id), self._lock, self.accounts._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            account = self.accounts.get(account_id, connection=conn)
            resource = conn.execute('SELECT * FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone()
            if account is None or resource is None:
                raise ValueError('PROXY_NOT_FOUND')
            if account['binding_revision'] != expected_binding_revision:
                raise ValueError('PROXY_REVISION_MISMATCH')
            if account['platform'] != resource['platform']:
                raise ValueError('PROXY_PLATFORM_MISMATCH')
            if account['proxy_id'] != proxy_id:
                count = conn.execute('SELECT COUNT(*) FROM crawler_accounts WHERE proxy_id=?', (proxy_id,)).fetchone()[0]
                if count >= resource['max_bound_accounts']:
                    raise ValueError('PROXY_CAPACITY_CONFLICT')
                conn.execute('UPDATE crawler_accounts SET proxy_id=?, proxy_required=1, binding_revision=binding_revision+1 WHERE id=?', (proxy_id, account_id))
            return self.accounts.get(account_id, connection=conn)

    def unbind(self, account_id, *, expected_binding_revision):
        current = self.accounts.get(account_id)
        if current is None:
            raise ValueError('PROXY_NOT_FOUND')
        with self._guard('unbind', current['proxy_id'], account_id), self._lock, self.accounts._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            account = self.accounts.get(account_id, connection=conn)
            if account is None or account['binding_revision'] != expected_binding_revision:
                raise ValueError('PROXY_REVISION_MISMATCH')
            if account['proxy_id']:
                conn.execute('UPDATE crawler_accounts SET proxy_id=NULL,binding_revision=binding_revision+1 WHERE id=?', (account_id,))
            return self.accounts.get(account_id, connection=conn)

    def resolve(self, account_id, *, expected_binding_revision=None, expected_config_revision=None, now=None, allow_probe=False):
        now = time.time() if now is None else _epoch(now)
        with self._lock, self.accounts._connect() as conn:
            account = self.accounts.get(account_id, connection=conn)
            if account is None:
                raise ValueError('PROXY_NOT_FOUND')
            if expected_binding_revision is not None and account['binding_revision'] != expected_binding_revision:
                raise ValueError('PROXY_REVISION_MISMATCH')
            if not account['proxy_id']:
                if account['proxy_required']:
                    raise ValueError('PROXY_BINDING_REQUIRED')
                return {'account': account, 'resource': None}
            row = conn.execute('SELECT * FROM proxy_resources WHERE id=?', (account['proxy_id'],)).fetchone()
            if row is None:
                raise ValueError('PROXY_NOT_FOUND')
            if row['platform'] != account['platform']:
                raise ValueError('PROXY_PLATFORM_MISMATCH')
            if expected_config_revision is not None:
                self._check_revision(row, expected_config_revision)
            if row['status'] == 'expired' or row['expires_at'] is not None and row['expires_at'] <= now:
                raise ValueError('PROXY_EXPIRED')
            if not allow_probe:
                if row['expires_at'] is None:
                    raise ValueError('PROXY_UNAVAILABLE')
                if row['status'] == 'exit_changed':
                    raise ValueError('PROXY_EXIT_CHANGED')
                if row['status'] != 'active' or row['stop_reason'] or row['cooldown_until'] is not None and row['cooldown_until'] > now:
                    raise ValueError('PROXY_UNAVAILABLE')
                if account['validated_proxy_binding_revision'] != account['binding_revision'] or account['validated_proxy_config_revision'] != row['config_revision']:
                    raise ValueError('PROXY_LOGIN_VALIDATION_REQUIRED')
            return {'account': account, 'resource': self._public(conn, row)}

    def mark_route_login_validated(self, account_id, *, expected_binding_revision, expected_config_revision, connection=None):
        """Record the parent-confirmed real login/reuse success for a frozen route.

        This health marker preserves cookies/profile/auth revision and does not
        authorize login: the caller must already own the running execution lease.
        """
        def write(conn):
            account = self.accounts.get(account_id, connection=conn)
            if account is None or not account['proxy_id']:
                raise ValueError('PROXY_NOT_FOUND')
            row = conn.execute('SELECT * FROM proxy_resources WHERE id=?', (account['proxy_id'],)).fetchone()
            if row is None or account['binding_revision'] != expected_binding_revision or row['config_revision'] != expected_config_revision:
                raise ValueError('PROXY_REVISION_MISMATCH')
            if (account['status'] != 'active' or not account['has_auth_state'] or row['platform'] != account['platform']
                or row['status'] != 'active' or row['stop_reason']
                or row['expires_at'] is None or row['expires_at'] <= time.time()
                or row['cooldown_until'] is not None and row['cooldown_until'] > time.time()):
                raise ValueError('PROXY_UNAVAILABLE')
            conn.execute('UPDATE crawler_accounts SET validated_proxy_binding_revision=?,validated_proxy_config_revision=? WHERE id=?',
                         (expected_binding_revision, expected_config_revision, account_id))
            return self.accounts.get(account_id, connection=conn)
        if connection is not None:
            return write(connection)
        with self._lock, self.accounts._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            return write(conn)

    def decrypt_snapshot(self, proxy_id, *, expected_revision=None):
        with self._lock, self.accounts._connect() as conn:
            row = conn.execute('SELECT * FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone()
            if row is None:
                raise ValueError('PROXY_NOT_FOUND')
            if expected_revision is not None:
                self._check_revision(row, expected_revision)
            return ProxyTransportSecrets(self.cipher.decrypt(row['transport_ciphertext']))

    @_notify_health_after_commit
    def set_health(self, proxy_id, *, status, cooldown_until=_UNSET, stop_reason=_UNSET):
        if status not in PROXY_STATUSES:
            raise ValueError('PROXY_INVALID_STATUS')
        if cooldown_until is not _UNSET:
            cooldown_until = _epoch(cooldown_until)
        # Only machine codes: arbitrary messages could accidentally persist credentials.
        reason = _UNSET if stop_reason is _UNSET else str(stop_reason or '')
        if reason is not _UNSET and reason and not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', reason):
            raise ValueError('PROXY_INVALID_EVENT')
        with self._lock, self.accounts._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone()
            if row is None:
                raise ValueError('PROXY_NOT_FOUND')
            cooldown = row['cooldown_until'] if cooldown_until is _UNSET else cooldown_until
            reason = row['stop_reason'] if reason is _UNSET else reason
            cursor = conn.execute('UPDATE proxy_resources SET status=?,cooldown_until=?,stop_reason=?,updated_at=? WHERE id=?', (status, cooldown, reason, time.time(), proxy_id))
            if cursor.rowcount == 0:
                raise ValueError('PROXY_NOT_FOUND')
            return self._public(conn, conn.execute('SELECT * FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone())

    @_notify_health_after_commit
    def observe_exit(self, proxy_id, ip, *, observed_at=None):
        try:
            ip = str(ipaddress.ip_address(ip))
        except ValueError:
            raise ValueError('PROXY_INVALID_EXIT') from None
        observed_at = time.time() if observed_at is None else _epoch(observed_at)
        with self._lock, self.accounts._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone()
            if row is None:
                raise ValueError('PROXY_NOT_FOUND')
            if row['observed_exit_at'] is not None and observed_at < row['observed_exit_at']:
                raise ValueError('PROXY_STALE_OBSERVATION')
            changed = bool(row['observed_exit_ip']) and ip != row['observed_exit_ip']
            status = 'exit_changed' if changed else row['status']
            reason = 'PROXY_EXIT_CHANGED' if changed else row['stop_reason']
            generation = int(row['shared_exit_generation'] or 0) + (1 if changed or not row['shared_exit_key'] else 0)
            conn.execute('UPDATE proxy_resources SET observed_exit_ip=?, observed_exit_at=?, shared_exit_key=?, shared_exit_generation=?, status=?,stop_reason=?,updated_at=? WHERE id=?', (ip, observed_at, ip, generation, status, reason, time.time(), proxy_id))
            if changed:
                conn.execute('INSERT INTO resource_events(proxy_id,kind,code,created_at) VALUES (?,?,?,?)', (proxy_id, 'exit_changed', reason, time.time()))
            return self._public(conn, conn.execute('SELECT * FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone())

    def pause_shared_exit(self, proxy_id, *, reason='ACCOUNT_CHALLENGE', until=None):
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', str(reason)):
            raise ValueError('PROXY_INVALID_EVENT')
        with self._lock, self.accounts._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone()
            if row is None: raise ValueError('PROXY_NOT_FOUND')
            conn.execute('UPDATE proxy_resources SET paused_until=?,pause_reason=?,updated_at=? WHERE id=?', (_epoch(until), str(reason), time.time(), proxy_id))
            return self._public(conn, conn.execute('SELECT * FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone())

    def shared_exit_admission_allowed(self, proxy_id, *, observed_exit=None):
        with self._lock, self.accounts._connect() as conn:
            row = conn.execute('SELECT * FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone()
            if row is None or not row['shared_exit_key'] or (observed_exit and observed_exit != row['shared_exit_key']): return False
            return not row['paused_until'] or row['paused_until'] <= time.time()

    def record_event(self, proxy_id, kind, *, code=''):
        if not isinstance(kind, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', kind) or not isinstance(code, str) or code and not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', code):
            raise ValueError('PROXY_INVALID_EVENT')
        with self._lock, self.accounts._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone():
                raise ValueError('PROXY_NOT_FOUND')
            conn.execute('INSERT INTO resource_events(proxy_id,kind,code,created_at) VALUES (?,?,?,?)', (proxy_id, kind, code, time.time()))

    def delete(self, proxy_id, *, expected_revision):
        with self._guard('delete', proxy_id), self._lock, self.accounts._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone()
            if row is None:
                return False
            self._check_revision(row, expected_revision)
            if conn.execute('SELECT 1 FROM crawler_accounts WHERE proxy_id=?', (proxy_id,)).fetchone():
                raise ValueError('PROXY_RESOURCE_BOUND')
            conn.execute('DELETE FROM proxy_resources WHERE id=?', (proxy_id,))
            return True
