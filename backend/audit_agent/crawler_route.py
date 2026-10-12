"""Application bridge for frozen account routes and inherited secret pipes."""
from __future__ import annotations

import hashlib
import importlib.util
import sys
import os
import selectors
import threading
import time
from functools import lru_cache

from .config import settings


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
    return _load_contract(str(settings.media_crawler_dir.resolve() / 'tools' / 'collection_route.py'))


# A single exception class across application callers and dynamic contract loads.
class CollectionRouteError(RuntimeError):
    """Application errors never contain transport secrets or URLs."""
    def __init__(self, code):
        allowed = {
            "PROXY_BUSY", "SUPPLEMENT_DEFERRED", "PROXY_REVISION_MISMATCH",
            "PROXY_EXIT_CHANGED", "PROXY_UNAVAILABLE", "PROXY_AUTH_FAILED",
            "PROXY_CONFIG_INVALID", "PROBE_BUDGET_EXHAUSTED", "SCHEDULER_POLICY_CONFLICT",
        }
        self.code = code if isinstance(code, str) and code in allowed else "PROXY_CONFIG_INVALID"
        super().__init__(self.code)


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
    if code in {'PROXY_EXPIRED', 'PROXY_NOT_FOUND', 'PROXY_BINDING_REQUIRED', 'PROXY_LOGIN_VALIDATION_REQUIRED'}:
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
    if not getattr(settings, 'crawler_proxy_enabled', False) and (
        account is None or not account.get('proxy_required') and not account.get('proxy_id')
    ):
        return None
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
        return contract.validate_route_envelope(dict(schema_version=1, route_mode='proxy_required',
            platform=account['platform'], account_id=account_id, profile_key=contract.derive_profile_key(account_id),
            proxy_id=resource['id'], binding_revision=account['binding_revision'], config_revision=resource['config_revision'],
            endpoint=transport['endpoint'], credentials=transport['credentials'], expires_at=resource['expires_at'],
            observed_exit=None if observed_ip is None else dict(ip=observed_ip, observed_at=observed_at),
            scheduler=dict(db_path=str(settings.request_scheduler_db.resolve()),
                           scope_ids=list(contract.derive_scope_ids('dy', account_id, resource['id'], observed_ip)))), account_id=account_id)
    except Exception as exc:
        raise _safe_error(exc) from None


def acquire_route_execution(account_id, execution_id, owner_id, *, account_store=None,
                            account_handle=None, snapshot=None, allow_probe=False):
    from backend.task_admission.resources import ExecutionLeaseBundle
    snapshot = snapshot or resolve_account_route(account_id, account_store=account_store, allow_probe=allow_probe)
    store = get_proxy_store(account_store) if snapshot else None
    resource = store.get_public(snapshot.proxy_id) if snapshot else None
    if snapshot:
        authorize_route_owner(account_id, snapshot.proxy_id, owner_id, account_store=account_store)
    bundle = ExecutionLeaseBundle(execution_id, owner_id, account_id, snapshot)
    bundle.proxy_store = store
    if store is not None:
        bundle.account_store = store.accounts
    elif account_store is not None:
        bundle.account_store = account_store
    else:
        from .crawler_account_store import crawler_account_store
        bundle.account_store = crawler_account_store
    if not bundle.acquire(account_handle=account_handle, max_active_accounts=min(resource['max_active_accounts'], max(1, int(settings.request_concurrency))) if resource else 1):
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
    return bundle


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
        if required:
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
    authorize_route_owner(account_id, value['proxy_id'], owner_id, account_store=account_store, auth_store=auth_store)
    snapshot = resolve_account_route(account_id, account_store=account_store,
        expected_binding_revision=value['binding_revision'], expected_config_revision=value['config_revision'])
    if (snapshot is None) != (value['route_mode'] == 'legacy_direct'):
        raise CollectionRouteError('PROXY_REVISION_MISMATCH')
    if snapshot and (snapshot.proxy_id != value['proxy_id'] or snapshot.observed_exit_ip is not None and snapshot.observed_exit_ip != value['observed_exit_ip']):
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
        'MEDIACRAWLER_PROXY_REQUIRED': 'true' if bundle.snapshot else 'false',
    }
    if trial_id:
        result['MEDIACRAWLER_PROXY_TRIAL_ID'] = trial_id
    return result


def raise_route_log_error(log):
    for code in ('PROXY_BUSY', 'SUPPLEMENT_DEFERRED', 'PROXY_REVISION_MISMATCH',
                 'PROXY_EXIT_CHANGED', 'PROXY_UNAVAILABLE', 'PROXY_AUTH_FAILED',
                 'PROXY_CONFIG_INVALID', 'PROBE_BUDGET_EXHAUSTED', 'SCHEDULER_POLICY_CONFLICT'):
        if code in log:
            raise CollectionRouteError(code)


class _AssetBrowserSession:
    """Original account browser remains live on a dedicated async loop."""
    def __init__(self, bundle, *, lazy=False):
        import asyncio
        self.bundle = bundle
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

    async def open(self):
        path = str(settings.media_crawler_dir.resolve())
        if path not in sys.path:
            sys.path.insert(0, path)
        route_contract()
        from tools.bound_proxy import BoundProxySession
        from .crawler_browser import load_account_browser
        self.session = BoundProxySession(self.bundle.snapshot,
            echo_url=getattr(settings, 'crawler_proxy_echo_url', ''),
            budget_id=probe_env(self.bundle)['MEDIACRAWLER_PROXY_PROBE_BUDGET_ID'],
            ttl=getattr(settings, 'crawler_proxy_probe_ttl', 300),
            max_calls=getattr(settings, 'crawler_proxy_probe_budget', 96),
            trial_id=getattr(settings, 'crawler_proxy_trial_id', ''), reuse_existing_policy=True,
            account_policy=parent_account_policy(self.bundle))
        try:
            self.context, _ = await load_account_browser().launch_account_context(
                self.bundle.account_id, settings.crawler_browser_profile_root, headless=True,
                route_snapshot=self.bundle.snapshot, bound_proxy_session=self.session)
        except RuntimeError as exc:
            code = getattr(exc, "code", None)
            if code == "PROXY_BUSY":
                raise CollectionRouteError("SUPPLEMENT_DEFERRED") from None
            if code is not None:
                raise _safe_error(exc) from None
            raise CollectionRouteError("PROXY_UNAVAILABLE") from None

    def close(self):
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
        if helper is None:
            helper = (_BorrowedChildSession if child_active else _AssetBrowserSession)(bundle, lazy=lazy)
            bundle.add_closer(helper.close)
            setattr(bundle, key, helper)
    return helper


@contextmanager
def asset_request_slot(bundle, timeout=60):
    """Reauthorize every hop; proxy route must have real paired browser receipt."""
    snapshot = bundle.snapshot
    authorize_route_owner(bundle.account_id, snapshot.proxy_id if snapshot else None, bundle.owner_id, account_store=getattr(bundle, "account_store", None))
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


class RouteReceiptPipe:
    """Public child-browser receipts, invalidated on EOF or process death."""
    def __init__(self, bundle):
        self.bundle = bundle
        self.read_fd, self.write_fd = os.pipe()
        self.thread = None
        self.process = None
        self.stopped = threading.Event()

    @property
    def pass_fds(self):
        return (self.write_fd,)

    @property
    def env(self):
        return {'MEDIACRAWLER_PROXY_RECEIPT_FD': str(self.write_fd)}

    def start(self, process):
        import json
        self.process = process
        os.close(self.write_fd)
        self.write_fd = None
        self.bundle.child_receipt_pipe = self
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
                        fields = {'schema_version', 'type', 'platform', 'account_id', 'proxy_id', 'binding_revision', 'config_revision', 'context_id', 'ip', 'observed_at'}
                        snapshot = self.bundle.snapshot
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
                        receipt = route_contract().ProbeReceipt(snapshot.proxy_id, snapshot.config_revision, value['context_id'], value['ip'], value['observed_at'])
                        route_contract().require_probe_receipt(snapshot, receipt, value['context_id'])
                        with self.bundle._lock:
                            if process.poll() is not None:
                                return
                            self.bundle.child_receipt = receipt
                        store = getattr(self.bundle, "proxy_store", None)
                        if store is not None:
                            store.observe_exit(snapshot.proxy_id, receipt.ip, observed_at=receipt.observed_at)
            except Exception:
                # Invalid input or persistence failure invalidates the receipt;
                # downstream use reports SUPPLEMENT_DEFERRED, never success.
                pass
            finally:
                poll.close()
                with self.bundle._lock:
                    self.bundle.child_receipt = None
        self.thread = threading.Thread(target=read, daemon=True)
        self.thread.start()

    def receipt(self):
        with self.bundle._lock:
            receipt = getattr(self.bundle, 'child_receipt', None)
            if receipt is None or self.process is None or self.process.poll() is not None or self.stopped.is_set():
                raise CollectionRouteError('SUPPLEMENT_DEFERRED')
            try:
                route_contract().require_probe_receipt(self.bundle.snapshot, receipt, receipt.context_id,
                    ttl=getattr(settings, 'crawler_proxy_probe_ttl', 300))
            except Exception:
                raise CollectionRouteError('SUPPLEMENT_DEFERRED') from None
            return receipt

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=1)
        for name in ('read_fd', 'write_fd'):
            fd = getattr(self, name)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, name, None)
        self.bundle.child_receipt = None


class _BorrowedChildSession(_AssetBrowserSession):
    """Use actual live child browser sample; compare a fresh HTTP-only echo."""
    async def open(self):
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
            account_policy=parent_account_policy(self.bundle))
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
    return BoundProxySession(snapshot, stop_checker=check, login_bootstrap=login_bootstrap,
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


def _run_maintenance_probe(bundle, *, timeout=45):
    """Bounded subprocess protocol; dependency imports stay in crawler Python."""
    import json
    import signal
    import subprocess
    from pathlib import Path
    from .crawler_browser import account_browser_env, scheduler_env
    snapshot = bundle.snapshot
    pipe = RouteConfigPipe(snapshot)
    process = None
    poll = selectors.DefaultSelector()
    try:
        env = os.environ.copy()
        for name in ('MEDIACRAWLER_PROXY_CONFIG_FD', 'MEDIACRAWLER_PROXY_RECEIPT_FD',
                     'MEDIACRAWLER_PROXY_REQUIRED', 'MEDIACRAWLER_PROXY_PROBE_BUDGET_ID'):
            env.pop(name, None)
        env.update(account_browser_env(bundle.account_id))
        env.update(scheduler_env())
        env.update(probe_env(bundle))
        env.update(pipe.env)
        env['MEDIACRAWLER_DIR'] = str(settings.media_crawler_dir.resolve())
        env['PYTHONUNBUFFERED'] = '1'
        helper = Path(__file__).resolve().parents[2] / 'scripts' / 'crawler_proxy_probe.py'
        process = subprocess.Popen([str(settings.crawler_login_python), str(helper),
            '--account-id', bundle.account_id], cwd=settings.media_crawler_dir,
            env=env, pass_fds=(*bundle.pass_fds, *pipe.pass_fds), stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True)
        pipe.start(process)
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
        for line in data.decode('utf-8').splitlines():
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
            result['context_id'],result['ip'],result['observed_at'])
        # Maintenance observes a changed exit; it must not authorize business
        # against the old baseline. Validate identity, expiry and receipt freshness.
        from dataclasses import replace
        diagnostic_snapshot = replace(snapshot, observed_exit_ip=None, observed_exit_at=None)
        route_contract().require_probe_receipt(diagnostic_snapshot,receipt,result['context_id'])
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
        finally:
            if process is not None and process.stdout is not None:
                process.stdout.close()
            pipe.close()


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
    authorize_route_owner(bundle.account_id, snapshot.proxy_id, bundle.owner_id,
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
    if source['route_mode'] != 'proxy_required' or error.code in {'PROXY_BUSY', 'SUPPLEMENT_DEFERRED'}:
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
        store = bundle.proxy_store
        if event['code'] == 'ROUTE_OBSERVED':
            ip = route_contract().canonical_exit_ip(event['observed_exit_ip'])
            current = store.get_public(snapshot.proxy_id)
            if current['config_revision'] != snapshot.config_revision:
                raise CollectionRouteError('PROXY_REVISION_MISMATCH')
            if current['observed_exit_at'] is None or at >= current['observed_exit_at']:
                store.observe_exit(snapshot.proxy_id, ip, observed_at=at)
            return True
        error = CollectionRouteError(event['code'])
        if error.code != event['code'] or event['observed_exit_ip'] is not None:
            raise CollectionRouteError('PROXY_CONFIG_INVALID')
        publish_route_stop(snapshot.proxy_id,snapshot.config_revision,error.code)
        store.set_health(snapshot.proxy_id,status='exit_changed' if error.code=='PROXY_EXIT_CHANGED' else 'unavailable',stop_reason=error.code)
        raise error
    except CollectionRouteError:
        raise
    except Exception as exc:
        raise _safe_error(exc) from None
