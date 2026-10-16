"""App-owner authority for one child, carried only by a private AF_UNIX FD.

No listening address, database copies or account locks live here. Closing a
channel never declares a live child dead and never releases its account lock.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path

MAX_FRAME = 16384

_OWNER_OPERATIONS = frozenset({
    "initialize", "cache_lookup", "check_stop", "fail", "browser_validate",
    "probe_reserve", "probe_confirm", "probe_snapshot", "make_gate", "reserve",
    "consume", "release", "renew_request", "cooldown", "gate_snapshot", "trial_operation",
    "trial_begin_media", "trial_media_timeout", "trial_media_bytes", "trial_finish_media",
    "trial_snapshot",
})


def _log_owner_failure(operation, error):
    """Owner-only diagnostics: fixed fields, never exception text or payload."""
    operation = (
        operation
        if isinstance(operation, str) and operation in _OWNER_OPERATIONS
        else "unknown"
    )
    exception_class = type(error).__name__
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", exception_class):
        exception_class = "Exception"
    if isinstance(error, sqlite3.OperationalError):
        category = "database_operational"
    elif isinstance(error, sqlite3.Error):
        category = "database"
    elif isinstance(error, TimeoutError):
        category = "timeout"
    elif isinstance(error, OSError):
        category = "os"
    elif isinstance(error, (ValueError, TypeError, KeyError)):
        category = "validation"
    elif isinstance(error, RuntimeError):
        category = "runtime"
    else:
        category = "unexpected"
    logging.getLogger(__name__).warning(
        "CRAWLER_AUTHORITY_FAILURE operation=%s exception_class=%s category=%s",
        operation,
        exception_class,
        category,
    )



def ensure_owner_request_policy(db_path):
    """Seed only a missing global policy; existing limits/history remain authoritative."""
    from tools.persistent_request_gate import RequestScheduler

    from .config import settings

    defaults = [settings.request_min_interval, settings.requests_per_minute,
                settings.request_concurrency, settings.media_request_interval]
    for _ in range(3):
        with sqlite3.connect(db_path, timeout=.2) as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='request_scheduler_policy'").fetchone()
            row = db.execute("SELECT limits_json FROM request_scheduler_policy WHERE platform='dy'").fetchone() if exists else None
        limits = json.loads(row[0]) if row else defaults
        if (not isinstance(limits,list) or len(limits)!=4
                or any(type(value) not in (int,float) or not math.isfinite(value) for value in limits)
                or min(limits[0],limits[3])<0 or min(limits[1],limits[2])<1
                or any(int(limits[index])!=limits[index] for index in (1,2))):
            raise RuntimeError('SCHEDULER_POLICY_CONFLICT')
        try:
            return RequestScheduler(db_path,platform='dy',min_interval=limits[0],per_minute=limits[1],
                max_concurrency=limits[2],media_interval=limits[3],
                cooldown_seconds=settings.request_cooldown_seconds,replace_policy=False)
        except ValueError:
            # Another owner may have registered the row between read and API
            # transaction. Re-read; never overwrite the winner or its counters.
            continue
    raise RuntimeError('SCHEDULER_POLICY_CONFLICT')


def tighten_owner_account_policy(session):
    """Only narrow existing account/degraded-exit policies, retaining all history."""
    def valid(value):
        if (not isinstance(value,list) or len(value)!=4
                or any(type(item) not in (int,float) or not math.isfinite(item) for item in value)
                or min(value[0],value[3])<0 or min(value[1],value[2])<1
                or any(int(value[index])!=value[index] for index in (1,2))):
            raise RuntimeError('SCHEDULER_POLICY_CONFLICT')
        return value

    parent = session._account_policy
    if (not isinstance(parent,dict) or set(parent)!={'min_interval','per_minute'}
            or type(parent['min_interval']) not in (int,float)
            or not math.isfinite(parent['min_interval']) or parent['min_interval']<0
            or type(parent['per_minute']) is not int or parent['per_minute']<1):
        raise RuntimeError('SCHEDULER_POLICY_CONFLICT')
    scopes=session.guard.require_business(session.receipt,session.context_id)
    with sqlite3.connect(session.snapshot.scheduler_db_path,timeout=.2) as db:
        db.execute('BEGIN IMMEDIATE')
        baseline_row=db.execute("SELECT limits_json FROM request_scheduler_policy WHERE platform='dy'").fetchone()
        if baseline_row is None:
            raise RuntimeError('SCHEDULER_POLICY_CONFLICT')
        baseline=valid(json.loads(baseline_row[0]))
        for scope in scopes:
            if scope=='dy:account:'+session.snapshot.account_id:
                target=[max(baseline[0],parent['min_interval']),min(baseline[1],parent['per_minute']),baseline[2],baseline[3]]
                if session.snapshot.route_mode=='direct_degraded':
                    target[0],target[1]=max(target[0],9),min(target[1],6)
            elif session.snapshot.route_mode=='direct_degraded' and scope.startswith('dy:service_exit:'):
                target=[max(baseline[0],9),min(baseline[1],6),baseline[2],baseline[3]]
            else:
                continue
            row=db.execute('SELECT limits_json FROM request_scheduler_policy WHERE platform=?',(scope,)).fetchone()
            if row is None:
                continue
            existing=valid(json.loads(row[0]))
            stricter=[max(existing[0],target[0]),min(existing[1],target[1]),
                      min(existing[2],target[2]),max(existing[3],target[3])]
            if stricter!=existing:
                db.execute('UPDATE request_scheduler_policy SET limits_json=? WHERE platform=?',
                    (json.dumps(stricter,allow_nan=False),scope))


def process_identity(pid):
    """PID plus kernel start identity prevents accepting a recycled PID."""
    if type(pid) is not int or pid < 1:
        raise ValueError("AUTHORITY_CHILD_INVALID")
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat[stat.rfind(")") + 2 :].split()[19]
    except FileNotFoundError:
        # macOS has no /proc. ps is read-only and works for nonroot workers.
        import subprocess

        value = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "lstart="], text=True
        ).strip()
        if not value:
            raise ValueError("AUTHORITY_CHILD_INVALID")
        return value


class RequestAuthority:
    def __init__(self, snapshot, *, crawler_root, session_options=None):
        self.snapshot = snapshot
        root = str(Path(crawler_root).resolve())
        if root not in sys.path:
            sys.path.insert(0, root)
        from tools.authority_ledger import initialize

        self.options = dict(session_options or {})
        self.initial_ack = self.options.pop("initial_ack", None)
        if snapshot.managed and not (
            callable(self.initial_ack) or isinstance(self.initial_ack, dict)
        ):
            raise ValueError("AUTHORITY_INITIAL_TICKET_REQUIRED")
        if isinstance(self.initial_ack, dict):
            self.initial_ack = dict(self.initial_ack)
        reusable_db = self.options.pop("reusable_db", "")
        outputs_root = self.options.pop("reusable_outputs_root", "")
        self.reusable_db = str(Path(reusable_db).resolve()) if reusable_db else ""
        self.reusable_outputs_root = (
            str(Path(outputs_root).resolve()) if outputs_root else ""
        )
        self.current_task_id = self.options.pop("current_task_id", "")
        self.keywords = tuple(self.options.pop("keywords", ()))
        if (
            self.reusable_db
            and (
                not self.reusable_outputs_root
                or not self.current_task_id
                or not self.keywords
            )
            or len(self.keywords) > 100
            or any(
                not isinstance(word, str) or not 0 < len(word) <= 512
                for word in self.keywords
            )
        ):
            raise ValueError("AUTHORITY_CACHE_INVALID")
        self.parent, self.child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.parent.settimeout(0.5)
        self.parent.set_inheritable(False)
        self.child.set_inheritable(False)
        self.channel = secrets.token_hex(16)
        self.env = {
            "MEDIACRAWLER_REQUEST_AUTHORITY_FD": str(self.child.fileno()),
            "MEDIACRAWLER_REQUEST_AUTHORITY_CHANNEL": self.channel,
        }
        self.pass_fds = (self.child.fileno(),)
        self.identity = {
            key: getattr(snapshot, key)
            for key in (
                "account_id",
                "owner_id",
                "operation_id",
                "lease_id",
                "lease_version",
                "snapshot_digest",
                "context_id",
            )
        }
        self.pid = self.start_identity = None
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.session = None
        self.sequence = 0
        self.cache = {}
        self.seen = {}
        self.owned_tokens = set()
        self.media_grants = {}
        self.trial_tokens = set()
        try:
            with sqlite3.connect(snapshot.scheduler_db_path, timeout=0.2) as db:
                initialize(db)
        except BaseException:
            self.parent.close()
            self.child.close()
            raise
        self.thread = threading.Thread(
            target=self._serve, name="crawler-request-authority", daemon=True
        )
        self.thread.start()

    def bind_child(self, pid):
        if self.pid is not None or self.closed.is_set():
            raise ValueError("AUTHORITY_ALREADY_BOUND")
        self.start_identity = process_identity(pid)
        self.pid = pid
        self.child.close()
        self.ready.set()

    def close(self):
        self.closed.set()
        self.ready.set()
        for stream in (self.parent, self.child):
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            stream.close()
        # Tokens are deliberately retained while the real child remains alive.
        # Existing scheduler process-death cleanup frees capacity afterwards.

    def _alive(self):
        return (
            self.pid is not None and process_identity(self.pid) == self.start_identity
        )

    def _serve(self):
        raw = bytearray()
        self.ready.wait(10)
        try:
            if self.pid is None:
                return
            while not self.closed.is_set():
                try:
                    chunk = self.parent.recv(4096)
                except TimeoutError:
                    continue
                if not chunk:
                    return
                raw.extend(chunk)
                if len(raw) > MAX_FRAME:
                    return
                while b"\n" in raw:
                    frame, _, rest = raw.partition(b"\n")
                    raw = bytearray(rest)
                    request = json.loads(frame)
                    request_id = request.get("request_id")
                    try:
                        result = self._dispatch(request)
                        response = {"request_id": request_id, "ok": True, "result": result}
                    except Exception as exc:  # noqa: BLE001 - Protocol boundary redacts arbitrary owner errors and fails closed.
                        _log_owner_failure(request.get("operation"), exc)
                        code = getattr(exc, "code", str(exc))
                        allowed = {
                            "PROXY_UNAVAILABLE",
                            "PROXY_REVISION_MISMATCH",
                            "PROXY_CONFIG_INVALID",
                            "PROBE_BUDGET_EXHAUSTED",
                            "PROXY_EXIT_CHANGED",
                            "SCHEDULER_POLICY_CONFLICT",
                            "PROXY_AUTH_FAILED",
                            "PROXY_EXPIRED",
                            "PROXY_TRANSPORT_FAILED",
                            "PROXY_CONNECTION_REFUSED",
                        }
                        response = {
                            "request_id": request_id,
                            "ok": False,
                            "error": code if code in allowed else "PROXY_UNAVAILABLE",
                        }
                    data = (
                        json.dumps(response, allow_nan=False, separators=(",", ":"))
                        + "\n"
                    ).encode()
                    if len(data) > MAX_FRAME:
                        return
                    self.parent.sendall(data)
        except (OSError, ValueError, TypeError):
            pass
        finally:
            self.close()

    def _dispatch(self, frame):
        if (
            set(frame)
            != {
                "version",
                "channel",
                "sequence",
                "request_id",
                "identity",
                "pid",
                "deadline",
                "operation",
                "payload",
            }
            or frame["version"] != 1
            or frame["channel"] != self.channel
            or frame["identity"] != self.identity
            or frame["pid"] != self.pid
            or not self._alive()
            or type(frame["sequence"]) is not int
            or frame["sequence"] != self.sequence + 1
            or type(frame["deadline"]) not in (float, int)
            or not math.isfinite(frame["deadline"])
            or not time.time() < frame["deadline"] <= time.time() + 6
            or not isinstance(frame["request_id"], str)
            or len(frame["request_id"]) != 32
            or not isinstance(frame["payload"], dict)
        ):
            raise RuntimeError("PROXY_REVISION_MISMATCH")
        self.sequence = frame["sequence"]
        operation, payload = frame["operation"], frame["payload"]
        digest = hashlib.sha256(
            json.dumps([operation, payload], sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        request_id = frame["request_id"]
        if request_id in self.seen and self.seen[request_id] != digest:
            raise RuntimeError("PROXY_REVISION_MISMATCH")
        if request_id not in self.seen and len(self.seen) >= 20000:
            raise RuntimeError("PROXY_UNAVAILABLE")
        self.seen[request_id] = digest
        if request_id in self.cache:
            previous_digest, result = self.cache[request_id]
            if previous_digest != digest:
                raise RuntimeError("PROXY_REVISION_MISMATCH")
            return result
        if len(self.cache) >= 20000:
            raise RuntimeError("PROXY_UNAVAILABLE")
        request = {
            "channel": self.channel,
            "request_id": request_id,
            "digest": digest,
            "pid": self.pid,
            "start_identity": self.start_identity,
        }
        from tools.authority_ledger import owner_access

        token = owner_access.set(True)
        try:
            result = self._execute(operation, payload, request)
        finally:
            owner_access.reset(token)
        self.cache[request_id] = digest, result
        return result

    def _execute(self, operation, payload, request):
        from tools.bound_proxy import BoundProxySession
        from tools.collection_route import ProbeReceipt
        from tools.persistent_request_gate import (
            _try_reserve_scopes,
            release_scope_tokens,
            validate_safety_connection,
        )

        if operation == "initialize":
            if self.session is not None:
                raise RuntimeError("PROXY_REVISION_MISMATCH")
            options = dict(self.options)
            options["authority_owner"] = True
            if self.snapshot.managed and self.initial_ack is not None:
                options["ack"] = (
                    self.initial_ack()
                    if callable(self.initial_ack)
                    else self.initial_ack
                )
            if options.get('reuse_existing_policy'):
                ensure_owner_request_policy(self.snapshot.scheduler_db_path)
            self.session = BoundProxySession(self.snapshot, **options)
            return {"trial_enabled": bool(self.options.get("trial_id"))}
        session = self.session
        if session is None:
            raise RuntimeError("PROXY_UNAVAILABLE")
        if session.maintenance_only and (
            operation
            in {
                "make_gate",
                "reserve",
                "consume",
                "release",
                "renew_request",
                "cooldown",
                "gate_snapshot",
            }
            or operation.startswith("trial_")
        ):
            raise RuntimeError("PROXY_UNAVAILABLE")
        if operation == "cache_lookup":
            return self._cache_lookup(payload)
        if operation == "check_stop":
            session.check_stop()
            return {"ack": session._lease_ack}
        if operation == "fail":
            return session.fail(payload["code"]).code
        if operation == "browser_validate":
            session._validate_browser_safety()
            return None
        if operation == "probe_reserve":
            session.check_stop()
            return session.budget.reserve(**payload)
        if operation == "probe_confirm":
            return session.budget.confirm_baseline(self.snapshot, payload["ip"])
        if operation == "probe_snapshot":
            return session.budget.snapshot()
        if operation == "make_gate":
            receipt = ProbeReceipt(**payload["receipt"])
            session.guard.confirm_pair(
                receipt.context_id,
                receipt.ip,
                receipt.ip,
                observed_at=receipt.observed_at,
            )
            session.receipt, session.context_id = receipt, receipt.context_id
            if session._reuse_existing_policy:
                tighten_owner_account_policy(session)
            session._gate = session._make_gate()
            from tools.trial_limits import from_env

            session.trial_limits = from_env(
                self.snapshot.scheduler_db_path,
                env={"MEDIACRAWLER_PROXY_TRIAL_ID": self.options.get("trial_id", "")},
            )
            if session.trial_limits is not None:
                session.trial_limits.owner_pid = self.pid
            return {"trial_enabled": session.trial_limits is not None}
        if operation == "reserve":
            session._validate_browser_safety()
            lease = payload.get("lease_seconds")
            if lease is not None and (
                type(lease) not in (int, float) or not 0 < lease <= 3630
            ):
                raise ValueError("invalid lease")
            from tools.request_authority import REQUEST_KINDS

            if payload.get("operation") not in REQUEST_KINDS:
                raise ValueError("invalid operation")
            if session._gate is None:
                raise RuntimeError("PROXY_UNAVAILABLE")
            result = _try_reserve_scopes(
                self.snapshot.scheduler_db_path,
                session._gate.scope_ids,
                payload["operation"],
                lease,
                time.time(),
                safety_fence=session._safety_fence(),
                authority=request,
            )
            if result["delay"] == 0:
                self.owned_tokens.update(result["scope_tokens"].items())
                if payload['operation'] == 'media' and result['scope_tokens']:
                    self.media_grants[result['grant']] = {
                        'tokens': dict(result['scope_tokens']), 'consumed': False, 'released': False,
                    }
            return result
        if operation == "consume":
            session.check_stop()
            now = time.time()
            with sqlite3.connect(self.snapshot.scheduler_db_path, timeout=0.2) as db:
                fence = session._safety_fence()
                if fence:
                    db.execute("ATTACH DATABASE ? AS safety", (fence["db_path"],))
                db.execute("BEGIN IMMEDIATE")
                validate_safety_connection(db, fence, now)
                for scope in session._gate.scope_ids:
                    row = db.execute(
                        "SELECT cooldown_until FROM request_scheduler_state WHERE platform=?",
                        (scope,),
                    ).fetchone()
                    if row and row[0] > now:
                        raise RuntimeError("PROXY_UNAVAILABLE")
                row = db.execute(
                    "SELECT expires_at,consumed FROM crawler_authority_ledger WHERE channel=? AND grant=?",
                    (self.channel, payload["grant"]),
                ).fetchone()
                if not row or row[1] or row[0] <= now:
                    raise RuntimeError("PROXY_UNAVAILABLE")
                db.execute(
                    "UPDATE crawler_authority_ledger SET consumed=1 WHERE channel=? AND grant=?",
                    (self.channel, payload["grant"]),
                )
            media_grant = self.media_grants.get(payload['grant'])
            if media_grant is not None:
                media_grant['consumed'] = True
            return None
        if operation == 'renew_request':
            if set(payload) != {'scope_tokens'} or not isinstance(payload['scope_tokens'],dict):
                raise RuntimeError('PROXY_REVISION_MISMATCH')
            tokens = payload['scope_tokens']
            matched = next((grant for grant in self.media_grants.values()
                            if grant['consumed'] and not grant['released']
                            and tokens and grant['tokens'] == tokens), None)
            if matched is None:
                raise RuntimeError('PROXY_REVISION_MISMATCH')
            session.check_stop()
            session._validate_browser_safety()
            from tools.persistent_request_gate import renew_scope_tokens
            return renew_scope_tokens(self.snapshot.scheduler_db_path,tokens,
                pid=self.pid,start_identity=self.start_identity,safety_fence=session._safety_fence())
        if operation == "release":
            tokens = payload["scope_tokens"]
            if not isinstance(tokens, dict) or any(
                (scope, token) not in self.owned_tokens
                for scope, token in tokens.items()
            ):
                raise RuntimeError("PROXY_REVISION_MISMATCH")
            release_scope_tokens(self.snapshot.scheduler_db_path, tokens)
            for grant in self.media_grants.values():
                if any(grant['tokens'].get(scope) == token for scope,token in tokens.items()):
                    grant['released'] = True
            return None
        if operation == "cooldown":
            return session._gate.enter_cooldown(**payload)
        if operation == "gate_snapshot":
            return session._gate.snapshots()
        if operation.startswith("trial_") and session.trial_limits is not None:
            method = {
                "trial_operation": "reserve_operation",
                "trial_begin_media": "begin_media",
                "trial_media_timeout": "media_timeout",
                "trial_media_bytes": "reserve_media_bytes",
                "trial_finish_media": "finish_media",
                "trial_snapshot": "snapshot",
            }.get(operation)
            if method is None or (
                "token" in payload and payload["token"] not in self.trial_tokens
            ):
                raise RuntimeError("PROXY_UNAVAILABLE")
            result = getattr(session.trial_limits, method)(**payload)
            if operation == "trial_begin_media":
                self.trial_tokens.add(result)
            return result
        raise RuntimeError("PROXY_UNAVAILABLE")

    def _cache_lookup(self, payload):
        if not self.reusable_db:
            return {"ids": [], "next_offset": None}
        keyword = payload.get("keyword")
        if keyword not in self.keywords:
            raise RuntimeError("PROXY_REVISION_MISMATCH")
        from urllib.parse import quote

        from tools.reusable_content import reusable_aweme_is_complete

        with sqlite3.connect(
            "file:" + quote(self.reusable_db) + "?mode=ro", uri=True, timeout=0.2
        ) as db:
            if "ids" in payload:
                if set(payload) != {"keyword", "ids"}:
                    raise ValueError("invalid cache request")
                ids = payload["ids"]
                if (
                    not isinstance(ids, list)
                    or len(ids) > 100
                    or any(
                        not isinstance(value, str)
                        or not value.isdigit()
                        or len(value) > 32
                        for value in ids
                    )
                ):
                    raise ValueError("invalid cache IDs")
                if not ids:
                    return {"ids": [], "next_offset": None}
                placeholders = ",".join("?" for _ in ids)
                rows = db.execute(
                    "SELECT content_key,raw_item_path FROM contents WHERE platform='dy' AND collection_status='complete' AND content_key IN ("
                    + placeholders
                    + ")",
                    ids,
                ).fetchall()
                next_offset = None
            else:
                if set(payload) != {"keyword", "offset", "limit"}:
                    raise ValueError("invalid cache request")
                offset, limit = payload["offset"], payload["limit"]
                if (
                    type(offset) is not int
                    or not 0 <= offset <= 1000000
                    or type(limit) is not int
                    or not 1 <= limit <= 128
                ):
                    raise ValueError("invalid cache range")
                rows = db.execute(
                    "SELECT c.content_key,c.raw_item_path FROM contents c JOIN content_matches m ON m.content_id=c.id WHERE c.platform='dy' AND c.collection_status='complete' AND m.task_id=? AND m.keyword=? ORDER BY c.id LIMIT ? OFFSET ?",
                    (self.current_task_id, keyword, limit, offset),
                ).fetchall()
                next_offset = offset + len(rows) if len(rows) == limit else None
        complete = sorted(
            {
                str(key)
                for key, path in rows
                if reusable_aweme_is_complete(
                    path, key, outputs_root=self.reusable_outputs_root
                )
            }
        )
        return {"ids": complete, "next_offset": next_offset}


def parent_authority_ticket(bundle):
    """Read an already ACKed ticket after the child completed its route ACK."""
    snapshot = bundle.snapshot
    return bundle.route_operation_store.ticket(
        snapshot.operation_id,
        owner_id=bundle.owner_id,
        snapshot_digest=snapshot.snapshot_digest,
        context_id=snapshot.context_id,
    )


def parent_authority_renew_ticket(bundle, prior):
    """Authority owner performs renewal; never waits for its child to renew it."""
    from .crawler_child_runtime import require_browser_available

    require_browser_available(
        bundle.account_id,
        store=getattr(bundle, "account_store", None),
        owner_id=bundle.owner_id,
        migration_id=getattr(bundle, "migration_id", None),
        operation_id=bundle.execution_id,
    )
    current = parent_authority_ticket(bundle)
    if current["lease_version"] > prior["lease_version"]:
        return current
    if current["lease_version"] != prior["lease_version"]:
        raise RuntimeError("PROXY_REVISION_MISMATCH")
    try:
        return bundle.route_operation_store.renew(
            secrets.token_hex(16),
            route_operation_id=bundle.snapshot.operation_id,
            owner_id=bundle.owner_id,
            lease_version=prior["lease_version"],
        )
    except ValueError:
        # A concurrent caller may have won the same CAS. Return only the already
        # committed newer ticket, never mint another lease or extend it twice.
        current = parent_authority_ticket(bundle)
        if current["lease_version"] > prior["lease_version"]:
            return current
        raise


def open_authority(snapshot, *, crawler_root, session_options=None):
    return RequestAuthority(
        snapshot, crawler_root=crawler_root, session_options=session_options
    )
