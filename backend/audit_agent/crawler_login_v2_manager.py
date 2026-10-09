"""Account-bound login V2 lifecycle, with one serialized control writer per child."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import queue
import secrets
import select
import signal
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .auth_state_cipher import auth_state_cipher
from .config import settings
from .crawler_account_store import account_is_cooling_down, crawler_account_store
from .crawler_browser import account_browser_env, scheduler_env
from .crawler_login_operation_store import CrawlerLoginOperationStore
from .crawler_login_protocol import (
    pack_packet,
    phase_to_status,
    read_packet,
    validate_command,
)

TERMINAL = {"done", "failed", "expired", "cancelled"}
DURABLE_ACTIONS = {
    "request_sms",
    "submit_sms",
    "refresh_qr",
    "refresh_challenge",
    "switch_to_sms",
}


def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LoginV2Error(ValueError):
    def __init__(self, code, message=None, status=409):
        self.code, self.message, self.status = code, message or code, status
        super().__init__(self.message)


@dataclass
class V2Session:
    id: str
    operation_id: str
    account_id: str
    user_id: str
    app_session_id: str
    owner_hash: str
    snapshot: dict
    process: object = None
    media: object = None
    diagnostic: object = None
    handles: list = field(default_factory=list)
    commands: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=64))
    stopped: threading.Event = field(default_factory=threading.Event)
    controller_ready: threading.Event = field(default_factory=threading.Event)
    cleanup_done: threading.Event = field(default_factory=threading.Event)
    commit_requested: bool = False
    epoch: int = 0
    sequence: int = 0
    controller: object = None
    frame: bytes = b""
    frame_revision: int = 0
    results: list = field(default_factory=list)
    result_base: int = 0
    candidate: dict | None = None
    subscribers: list = field(default_factory=list)
    deadline: float = 0
    committing: bool = False
    authorize: object = None


class CrawlerLoginV2Manager:
    def __init__(
        self,
        store=None,
        cipher=None,
        *,
        operation_store=None,
        python_path=None,
        helper_path=None,
        process_factory=None,
        lease_factory=None,
        timeout_seconds=None,
    ):
        self.store = store or crawler_account_store
        self.cipher = cipher or auth_state_cipher
        self.operations = operation_store or CrawlerLoginOperationStore(
            self.store, self.cipher
        )
        self.python_path = Path(python_path or settings.crawler_login_python)
        self.helper_path = Path(
            helper_path or settings.root_dir / "scripts" / "crawler_account_login.py"
        )
        self.process_factory = process_factory or subprocess.Popen
        self.lease_factory = lease_factory
        self.timeout = timeout_seconds or settings.crawler_login_v2_timeout_seconds
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()
        self.sessions = {}
        self.tickets = {}

    def _leases(self, account_id):
        if self.lease_factory:
            return self.lease_factory(account_id)
        import fcntl
        from backend.task_admission.resources import acquire_account_handle

        # The host-wide lease is inherited by the child along with its profile lease.
        root = (
            Path(settings.crawler_browser_profile_root).expanduser().resolve()
            / ".resource-locks"
        )
        root.mkdir(parents=True, exist_ok=True)
        global_handle = (root / "login-global.lock").open("a+b")
        try:
            fcntl.flock(global_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            global_handle.close()
            raise LoginV2Error("ACCOUNT_BUSY")
        handle = acquire_account_handle("dy", account_id)
        if handle is None:
            global_handle.close()
            raise LoginV2Error("ACCOUNT_BUSY")
        return [global_handle, handle]

    def recover_interrupted(self):
        if self.lease_factory:
            return
        import fcntl

        path = (
            Path(settings.crawler_browser_profile_root).expanduser().resolve()
            / ".resource-locks"
            / "login-global.lock"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            for op in self.operations.list():
                if op["stage"] in {
                    "created",
                    "checking",
                    "cleanup_authorized",
                    "reset_authorized",
                    "reset_done",
                    "profile_ready",
                }:
                    self.operations.interrupt_commands(op["id"])
                    self.operations.mark_stage(
                        op["id"], "recovery_required", error_code="SERVER_RESTARTED"
                    )

    def owns(self, session_id):
        with self._lock:
            return session_id in self.sessions

    def _owner(self, s, user_id, app_session_id, owner_token):
        if (
            s.user_id != user_id
            or s.app_session_id != app_session_id
            or not secrets.compare_digest(s.owner_hash, digest(owner_token))
        ):
            raise LoginV2Error(
                "SESSION_OWNER_MISMATCH", "该登录会话属于另一个应用或浏览器会话", 403
            )

    def get(self, session_id, *, user_id, app_session_id, owner_token):
        with self._lock:
            s = self.sessions.get(session_id)
            if s is None:
                raise LoginV2Error("SESSION_NOT_FOUND", status=404)
            self._owner(s, user_id, app_session_id, owner_token)
            return copy.deepcopy(s.snapshot)

    def account_id(self, session_id):
        with self._lock:
            s = self.sessions.get(session_id)
            if not s:
                raise LoginV2Error("SESSION_NOT_FOUND", status=404)
            return s.account_id

    def start(
        self,
        account,
        *,
        user_id,
        app_session_id,
        owner_token,
        request_key,
        intent="check_or_login",
        source_operation_id=None,
        browser_mode="default",
        authorize=None,
    ):
        if not 32 <= len(owner_token) <= 128:
            raise LoginV2Error("OWNER_TOKEN_REQUIRED", status=403)
        if not request_key or len(request_key) > 128:
            raise LoginV2Error("IDEMPOTENCY_KEY_REQUIRED", status=400)
        if intent not in {
            "check_or_login",
            "reauth",
            "recover",
        } or browser_mode not in {"default", "compatibility"}:
            raise LoginV2Error("INVALID_LOGIN_OPTIONS", status=400)
        if (intent == "recover") != bool(source_operation_id):
            raise LoginV2Error("RECOVERY_SOURCE_REQUIRED", status=400)
        with self._start_lock, self._lock:
            active = [
                s for s in self.sessions.values() if s.snapshot["phase"] not in TERMINAL
            ]
            if active:
                s = active[0]
                if s.account_id != account["id"]:
                    raise LoginV2Error("ACCOUNT_BUSY")
                self._owner(s, user_id, app_session_id, owner_token)
            fingerprint = self.cipher.command_fingerprint(
                {
                    "account_id": account["id"],
                    "intent": intent,
                    "source_operation_id": source_operation_id,
                    "browser_mode": browser_mode,
                    "owner": digest(owner_token),
                    "session": app_session_id,
                }
            )
            op = self.operations.begin(
                account_id=account["id"],
                owner_user_id=user_id,
                application_session_id=app_session_id,
                owner_token_hash=digest(owner_token),
                request_key_hash=digest(request_key),
                request_fingerprint=fingerprint,
                intent=intent,
                source_operation_id=source_operation_id,
            )
            for s in self.sessions.values():
                if s.operation_id == op["id"]:
                    self._owner(s, user_id, app_session_id, owner_token)
                    return copy.deepcopy(s.snapshot), False
            if active:
                # A fresh start key may not replace an existing active login.
                self.operations.mark_stage(
                    op["id"], "failed", error_code="ACCOUNT_BUSY"
                )
                return copy.deepcopy(active[0].snapshot), False
            if op["stage"] == "committed":
                return self._committed_snapshot(op), False
            if op["stage"] != "created":
                raise LoginV2Error("RECOVERY_REQUIRED")
            try:
                handles = self._leases(account["id"])
            except Exception:
                self.operations.mark_stage(
                    op["id"], "failed", error_code="ACCOUNT_BUSY"
                )
                raise
            parent, child = socket.socketpair()
            s = V2Session(
                uuid4().hex,
                op["id"],
                account["id"],
                user_id,
                app_session_id,
                digest(owner_token),
                {},
                media=parent,
                handles=handles,
                deadline=time.monotonic() + self.timeout,
                authorize=authorize,
            )
            s.snapshot = {
                "protocol_version": 2,
                "engine": "v2",
                "id": s.id,
                "operation_id": s.operation_id,
                "source_operation_id": source_operation_id,
                "account_id": s.account_id,
                "platform": "dy",
                "phase": "checking",
                "status": "starting",
                "state_revision": 1,
                "controller_epoch": 0,
                "controller_state": "detached",
                "available_actions": ["cancel"],
                "qr": None,
                "sms": None,
                "challenge": None,
                "identity": {"status": "pending", "platform_account_id": ""},
                "result": None,
                "error": None,
                "created_at": utc(),
                "updated_at": utc(),
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=self.timeout)
                ).isoformat(),
                "interactive": False,
                "qr_image_data_url": "",
            }
            diagnostic = tempfile.TemporaryFile(mode="w+b")
            env = os.environ.copy()
            env.update(account_browser_env(account["id"]))
            env.update(scheduler_env())
            env.update(
                PYTHONUNBUFFERED="1",
                MEDIACRAWLER_DIR=str(settings.media_crawler_dir.expanduser().resolve()),
                CRAWLER_BROWSER_PROFILE_ROOT=env["MEDIACRAWLER_CLOAK_PROFILE_ROOT"],
            )
            env["PYTHONPATH"] = os.pathsep.join(
                filter(None, [str(settings.root_dir), env.get("PYTHONPATH", "")])
            )
            args = [
                str(self.python_path),
                str(self.helper_path),
                "--platform",
                "dy",
                "--account-id",
                account["id"],
                "--protocol-v2",
                "--media-fd",
                str(child.fileno()),
                "--operation-id",
                op["id"],
                "--intent",
                intent,
                "--entry",
                settings.crawler_login_v2_entry,
                "--timeout",
                str(self.timeout),
            ]
            if (
                browser_mode == "compatibility"
                or settings.crawler_login_v2_browser_mode == "headed"
            ):
                args.append("--headed")
            if account.get("platform_account_id"):
                args.extend(
                    ["--expected-platform-account-id", account["platform_account_id"]]
                )
            try:
                s.process = self.process_factory(
                    args,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=diagnostic,
                    cwd=str(settings.root_dir),
                    env=env,
                    pass_fds=tuple(h.fileno() for h in handles) + (child.fileno(),),
                    start_new_session=True,
                    bufsize=0,
                )
                s.diagnostic = diagnostic
                self.sessions[s.id] = s
                self.operations.mark_stage(op["id"], "checking")
            except Exception:
                parent.close()
                diagnostic.close()
                for h in handles:
                    h.close()
                self.operations.mark_stage(
                    op["id"], "failed", error_code="BROWSER_START_FAILED"
                )
                raise
            finally:
                child.close()
            for target in (
                self._writer,
                self._reader,
                self._media_reader,
                self._watchdog,
            ):
                threading.Thread(
                    target=target,
                    args=(s,),
                    daemon=True,
                    name=f"loginv2-{s.id[:8]}-{target.__name__}",
                ).start()
            return copy.deepcopy(s.snapshot), True

    def _committed_snapshot(self, op):
        account = op["result"]["account"]
        return {
            "protocol_version": 2,
            "engine": "v2",
            "id": "",
            "operation_id": op["id"],
            "account_id": op["account_id"],
            "phase": "done",
            "status": "success",
            "state_revision": 1,
            "controller_epoch": 0,
            "controller_state": "detached",
            "available_actions": [],
            "result": self._result(account),
            "error": None,
        }

    @staticmethod
    def _result(account):
        return {
            "auth_saved": True,
            "identity_verified": True,
            "reuse_verified": True,
            "platform_account_id": account.get("platform_account_id", ""),
            "account_eligibility": "disabled"
            if account.get("status") == "disabled"
            else "cooling_down"
            if account_is_cooling_down(account)
            else "ready",
            "cooldown_until": account.get("cooldown_until"),
        }

    def _notify(self, s):
        # Each subscriber has one scheduled wake regardless of media rate.
        for sub in list(s.subscribers):
            if not sub["pending"]:
                sub["pending"] = True
                try:
                    sub["loop"].call_soon_threadsafe(sub["event"].set)
                except RuntimeError:
                    s.subscribers.remove(sub)

    def subscribe(self, session_id):
        with self._lock:
            s = self.sessions[session_id]
            sub = {
                "loop": asyncio.get_running_loop(),
                "event": asyncio.Event(),
                "pending": False,
            }
            s.subscribers.append(sub)
            return sub

    def updates(self, session_id, sub, result_offset=0):
        with self._lock:
            s = self.sessions[session_id]
            sub["pending"] = False
            sub["event"].clear()
            return (
                copy.deepcopy(s.snapshot),
                s.frame,
                s.frame_revision,
                copy.deepcopy(s.results[max(0, result_offset - s.result_base) :]),
                s.result_base + len(s.results),
            )

    def unsubscribe(self, session_id, sub):
        with self._lock:
            s = self.sessions.get(session_id)
            if s and sub in s.subscribers:
                s.subscribers.remove(sub)

    def ticket(self, session_id, *, user_id, app_session_id, owner_token):
        with self._lock:
            s = self.sessions[session_id]
            self._owner(s, user_id, app_session_id, owner_token)
            if s.snapshot["phase"] in TERMINAL:
                raise LoginV2Error("SESSION_ENDED", status=410)
            token = secrets.token_urlsafe(32)
            # Tickets are hashes only, bounded in lifetime and retained count.
            self.tickets = {
                k: v for k, v in self.tickets.items() if v[1] > time.monotonic()
            }
            if len(self.tickets) >= 256:
                raise LoginV2Error("TICKET_LIMIT")
            self.tickets[digest(token)] = (
                s.id,
                time.monotonic() + 30,
                s.user_id,
                s.app_session_id,
                s.owner_hash,
            )
            return {"ticket": token, "expires_in": 30}

    def attach(self, session_id, token, *, user_id, app_session_id, controller):
        with self._lock:
            ticket = self.tickets.pop(digest(token), None)
            s = self.sessions.get(session_id)
            if (
                not s
                or not ticket
                or ticket[0] != session_id
                or ticket[1] < time.monotonic()
                or ticket[2:4] != (user_id, app_session_id)
                or ticket[4] != s.owner_hash
            ):
                raise LoginV2Error("TICKET_INVALID", status=403)
            if s.snapshot["phase"] in TERMINAL:
                raise LoginV2Error("SESSION_ENDED", status=410)
            old = s.controller
            s.epoch += 1
            s.sequence = 0
            s.controller = controller
            s.controller_ready.clear()
            s.snapshot.update(controller_epoch=s.epoch, controller_state="detached")
            self._enqueue(
                s,
                {
                    "type": "controller_authorized",
                    "controller_epoch": s.epoch,
                    "attached": True,
                },
                priority=True,
            )
            return s.epoch, old

    def wait_controller(self, session_id, epoch, timeout=3):
        s = self.sessions[session_id]
        if not s.controller_ready.wait(timeout):
            raise LoginV2Error("CONTROLLER_TIMEOUT")
        with self._lock:
            if s.epoch != epoch or s.snapshot["controller_state"] != "attached":
                raise LoginV2Error("CONTROLLER_REPLACED")

    def detach(self, session_id, epoch):
        with self._lock:
            s = self.sessions.get(session_id)
            if not s or epoch != s.epoch:
                return
            s.epoch += 1
            s.controller = None
            s.sequence = 0
            s.snapshot.update(controller_epoch=s.epoch, controller_state="detached")
            self._enqueue(
                s,
                {
                    "type": "controller_authorized",
                    "controller_epoch": s.epoch,
                    "attached": False,
                },
                priority=True,
            )
            s.frame = b""
            self._notify(s)

    def _enqueue(self, s, message, priority=False):
        if priority:
            # Only authorization/control messages bypass older unsent commands.
            self._drain(s)
        try:
            s.commands.put_nowait(message)
        except queue.Full:
            raise LoginV2Error("INPUT_BACKPRESSURE")

    def _drain(self, s):
        controls = []
        while True:
            try:
                message = s.commands.get_nowait()
            except queue.Empty:
                break
            command = message.get("command")
            if not command:
                controls.append(message)
            if command and self.operations.get_command(
                s.operation_id, command["command_id"]
            ):
                self.operations.finish_command(
                    s.operation_id,
                    command["command_id"],
                    execution_status="not_executed",
                    platform_status="not_applicable",
                    error_code="CONTROLLER_REPLACED",
                )
        for control in controls:
            if control.get("type") != "controller_authorized":
                s.commands.put_nowait(control)

    def command(
        self,
        session_id,
        command,
        *,
        user_id,
        app_session_id,
        owner_token=None,
        epoch=None,
    ):
        c = validate_command(command)
        with self._lock:
            s = self.sessions[session_id]
            if owner_token is not None:
                self._owner(s, user_id, app_session_id, owner_token)
            elif s.user_id != user_id or s.app_session_id != app_session_id:
                raise LoginV2Error("SESSION_OWNER_MISMATCH", status=403)
            if c["operation_id"] != s.operation_id:
                raise LoginV2Error("OPERATION_MISMATCH")
            kind = (
                c.get("action")
                if c["type"] == "action"
                else "manual_action"
                if c["type"] == "key"
                or c["type"] == "pointer_batch"
                and any(p["event"] != "move" for p in c["points"])
                else None
            )
            durable = kind in DURABLE_ACTIONS or kind == "manual_action"
            old = (
                self.operations.get_command(s.operation_id, c["command_id"])
                if durable
                else None
            )
            if old:
                return self.operations.record_command(
                    s.operation_id,
                    c["command_id"],
                    kind,
                    c,
                    application_session_id=s.app_session_id,
                    controller_epoch=c["controller_epoch"],
                    sequence=c["sequence"],
                )
            self._validate_runtime(s, c, epoch)
            if s.commands.full():
                raise LoginV2Error("INPUT_BACKPRESSURE")
            if durable:
                self.operations.record_command(
                    s.operation_id,
                    c["command_id"],
                    kind,
                    c,
                    application_session_id=s.app_session_id,
                    controller_epoch=c["controller_epoch"],
                    sequence=c["sequence"],
                )
            s.sequence = c["sequence"]
            self._enqueue(s, {"type": "command", "command": c})
            return {
                "command_id": c["command_id"],
                "execution_status": "accepted",
                "platform_status": "pending",
            }

    def _validate_runtime(self, s, c, epoch=None, dispatch=False):
        if s.snapshot["phase"] in TERMINAL:
            raise LoginV2Error("SESSION_ENDED")
        if (
            s.controller is None
            or s.snapshot["controller_state"] != "attached"
            or c["controller_epoch"] != s.epoch
            or epoch is not None
            and epoch != s.epoch
        ):
            raise LoginV2Error("CONTROLLER_REPLACED")
        if c["expected_state_revision"] != s.snapshot["state_revision"]:
            raise LoginV2Error("STALE_STATE")
        if not dispatch and c["sequence"] <= s.sequence:
            raise LoginV2Error("STALE_SEQUENCE")
        capability = c["action"] if c["type"] == "action" else c["type"]
        if capability not in s.snapshot["available_actions"]:
            raise LoginV2Error("ACTION_NOT_AVAILABLE")
        if "challenge_id" in c:
            area = s.snapshot.get("challenge") or {}
            if c["challenge_id"] != area.get("challenge_id") or c[
                "area_version"
            ] != area.get("area_version"):
                raise LoginV2Error("STALE_CHALLENGE")
        if s.authorize:
            s.authorize()

    def _writer(self, s):
        try:
            fd = s.process.stdin.fileno()
            os.set_blocking(fd, False)
            while not s.stopped.is_set():
                try:
                    message = s.commands.get(timeout=0.2)
                except queue.Empty:
                    continue
                with self._lock:
                    c = message.get("command")
                    if c:
                        try:
                            self._validate_runtime(s, c, dispatch=True)
                            if self.operations.get_command(
                                s.operation_id, c["command_id"]
                            ):
                                self.operations.dispatch_command(
                                    s.operation_id, c["command_id"]
                                )
                        except Exception as exc:
                            self._command_rejected(s, c, getattr(exc, "code", str(exc)))
                            continue
                    data = (json.dumps(message, separators=(",", ":")) + "\n").encode()
                deadline = time.monotonic() + 2
                while data and not s.stopped.is_set():
                    if time.monotonic() > deadline:
                        raise TimeoutError("control writer timed out")
                    _, ready, _ = select.select([], [fd], [], 0.1)
                    if ready:
                        try:
                            count = os.write(fd, data)
                        except BlockingIOError:
                            continue
                        if count <= 0:
                            raise BrokenPipeError()
                        data = data[count:]
        except Exception:
            self._finish(s, "failed", "CONTROL_CHANNEL_FAILED")

    def _append_result(self, s, result):
        s.results.append(result)
        if len(s.results) > 256:
            s.results.pop(0)
            s.result_base += 1
        self._notify(s)

    def _command_rejected(self, s, c, code):
        if self.operations.get_command(s.operation_id, c["command_id"]):
            self.operations.finish_command(
                s.operation_id,
                c["command_id"],
                execution_status="not_executed",
                platform_status="not_applicable",
                error_code=code,
            )
        self._append_result(
            s,
            {
                "type": "command_result",
                "command_id": c["command_id"],
                "execution_status": "rejected",
                "platform_status": "not_applicable",
                "error_code": code,
            },
        )

    def _reader(self, s):
        try:
            while True:
                raw = s.process.stdout.readline(65537)
                if not raw:
                    break
                if len(raw) > 65536:
                    raise ValueError("oversized control event")
                self.handle_event(s.id, json.loads(raw))
            s.process.wait(timeout=3)
            with self._lock:
                if s.snapshot["phase"] not in TERMINAL:
                    self._finish(s, "failed", "BROWSER_DISCONNECTED")
        except Exception:
            self._finish(s, "failed", "CONTROL_CHANNEL_FAILED")
        finally:
            self._cleanup(s)

    def _media_reader(self, s):
        try:
            while not s.stopped.is_set():
                p = read_packet(s.media, allow_auth=True)
                if p is None:
                    with self._lock:
                        if s.snapshot["phase"] not in TERMINAL and s.candidate is None:
                            self._finish(s, "failed", "MEDIA_CHANNEL_EOF")
                    break
                with self._lock:
                    if s.snapshot["phase"] in TERMINAL:
                        continue
                    if p["kind"] == 2:
                        candidate = json.loads(p["payload"])
                        if not isinstance(candidate, dict):
                            raise ValueError("invalid candidate")
                        if p["metadata"].get("operation_id") != s.operation_id:
                            raise ValueError("operation mismatch")
                        s.candidate = candidate
                    elif (
                        s.controller
                        and s.snapshot["controller_state"] == "attached"
                        and p["metadata"].get("controller_epoch") == s.epoch
                    ):
                        s.frame = pack_packet(
                            p["payload"],
                            p["metadata"],
                            sequence=p["sequence"],
                            area_version=p["area_version"],
                            capture_mono_ns=p["capture_mono_ns"],
                        )
                        s.frame_revision += 1
                        self._notify(s)
        except Exception:
            if not s.stopped.is_set():
                self._finish(s, "failed", "MEDIA_CHANNEL_FAILED")

    def handle_event(self, session_id, event):
        with self._lock:
            s = self.sessions[session_id]
            if s.snapshot["phase"] in TERMINAL:
                return
            kind = event.get("type")
            if kind == "controller_ready":
                if (
                    event.get("controller_epoch") == s.epoch
                    and s.controller is not None
                ):
                    s.snapshot.update(
                        controller_state="attached", controller_epoch=s.epoch
                    )
                    s.controller_ready.set()
                    self._notify(s)
            elif kind in {"snapshot", "phase"}:
                phase = event.get("phase", s.snapshot["phase"])
                if phase in TERMINAL:
                    raise ValueError("child cannot commit terminal state")
                s.snapshot.update(
                    phase=phase,
                    status=phase_to_status(phase),
                    state_revision=s.snapshot["state_revision"] + 1,
                    updated_at=utc(),
                )
                for key in ("qr", "sms", "challenge", "identity", "available_actions"):
                    if key in event:
                        s.snapshot[key] = event[key]
                s.snapshot["interactive"] = phase in {
                    "challenge_required",
                    "manual_required",
                    "sms_required",
                }
                s.snapshot["qr_image_data_url"] = (s.snapshot.get("qr") or {}).get(
                    "image_data_url", ""
                )
                self._notify(s)
            elif kind == "prepare_requested":
                self.operations.prepare_profile(s.operation_id, reset=False)
                self._enqueue(
                    s, {"type": "prepare_ready", "operation_id": s.operation_id}
                )
            elif kind == "guard_established":
                current = self.operations.get(s.operation_id)
                self.operations.mark_stage(
                    s.operation_id, current["stage"], profile_authority_established=1
                )
            elif kind == "guard_requested":
                self.operations.mark_stage(s.operation_id, "cleanup_authorized")
                self._enqueue(
                    s, {"type": "guard_ready", "operation_id": s.operation_id}
                )
            elif kind == "cleanup_requested":
                self.operations.prepare_profile(s.operation_id, reset=True)
                self._enqueue(
                    s, {"type": "cleanup_ready", "operation_id": s.operation_id}
                )
            elif kind == "cleanup_done":
                self.operations.mark_stage(s.operation_id, "reset_done")
            elif kind == "identity_rejected":
                self.operations.prepare_profile(s.operation_id, reset=False)
            elif kind == "reset_requested":
                self.operations.authorize_reset(s.operation_id)
                self._enqueue(
                    s, {"type": "reset_ready", "operation_id": s.operation_id}
                )
            elif kind == "reset_done":
                self.operations.mark_stage(s.operation_id, "reset_done")
            elif kind == "save_ready":
                # Media and stdout are separate channels; auth candidate may arrive shortly later.
                if not s.commit_requested:
                    s.commit_requested = True
                    threading.Thread(
                        target=self._commit_candidate, args=(s,), daemon=True
                    ).start()
            elif kind == "command_result":
                command_id = event.get("command_id")
                if self.operations.get_command(s.operation_id, command_id):
                    self.operations.finish_command(
                        s.operation_id,
                        command_id,
                        execution_status="not_executed"
                        if event.get("execution_status") == "rejected"
                        else event.get("execution_status", "executed"),
                        platform_status=event.get("platform_status", "unknown"),
                        error_code=event.get("error_code"),
                    )
                self._append_result(s, event)
            elif kind == "error":
                self._finish(s, "failed", event.get("code", "BROWSER_ERROR"))

    def _commit_candidate(self, s):
        deadline = time.monotonic() + 3
        while (
            s.candidate is None
            and not s.stopped.is_set()
            and time.monotonic() < deadline
        ):
            s.stopped.wait(0.01)
        with self._lock:
            if s.snapshot["phase"] in TERMINAL:
                return
            candidate = s.candidate
            s.candidate = None
            try:
                if (
                    not candidate
                    or not candidate.get("identity_verified")
                    or not candidate.get("reuse_verified")
                ):
                    raise LoginV2Error("IDENTITY_UNVERIFIED")
                if s.authorize:
                    s.authorize()
                current = self.operations.get(s.operation_id)
                if not current["profile_authority_established"]:
                    raise LoginV2Error("PROFILE_AUTHORITY_UNCONFIRMED")
                self.operations.mark_stage(s.operation_id, "profile_ready")
                ciphertext = self.cipher.encrypt(candidate["auth_state"])
                s.committing = True
                op = self.operations.commit_auth(
                    s.operation_id,
                    ciphertext,
                    candidate["platform_account_id"],
                    credentials_changed=candidate.get("credentials_changed", True),
                )
                s.snapshot["result"] = self._result(op["result"]["account"])
                self._enqueue(
                    s, {"type": "save_committed", "operation_id": s.operation_id}
                )
                self._set_terminal(s, "done", None)
            except Exception as exc:
                self.operations.mark_stage(
                    s.operation_id,
                    "recovery_required",
                    error_code=getattr(exc, "code", "SAVE_FAILED"),
                )
                self._finish(
                    s, "failed", getattr(exc, "code", "SAVE_FAILED"), recoverable=True
                )
            finally:
                s.committing = False

    def _set_terminal(self, s, phase, code):
        s.snapshot.update(
            phase=phase,
            status=phase_to_status(phase),
            error={"code": code, "message": code} if code else None,
            state_revision=s.snapshot["state_revision"] + 1,
            controller_state="detached",
            available_actions=[],
            qr=None,
            challenge=None,
            sms=None,
            qr_image_data_url="",
            updated_at=utc(),
        )
        s.epoch += 1
        s.frame = b""
        s.snapshot["controller_epoch"] = s.epoch
        self.tickets = {k: v for k, v in self.tickets.items() if v[0] != s.id}
        self._notify(s)

    def _finish(self, s, phase, code=None, recoverable=False):
        with self._lock:
            if s.snapshot["phase"] in TERMINAL:
                return
            self.operations.interrupt_commands(s.operation_id)
            if not recoverable:
                self.operations.mark_stage(s.operation_id, phase, error_code=code)
            self._set_terminal(s, phase, code)
            self._drain(s)
            self._enqueue(s, {"type": "shutdown", "operation_id": s.operation_id})
        threading.Thread(target=self._cleanup, args=(s,), daemon=True).start()

    def cancel(
        self,
        session_id,
        *,
        user_id=None,
        app_session_id=None,
        owner_token=None,
        internal=False,
    ):
        with self._lock:
            s = self.sessions[session_id]
            if not internal:
                self._owner(s, user_id, app_session_id, owner_token)
            self._finish(s, "cancelled")
            return copy.deepcopy(s.snapshot)

    def _watchdog(self, s):
        next_auth = 0.0
        while not s.stopped.wait(0.2):
            with self._lock:
                terminal = s.snapshot["phase"] in TERMINAL
                if terminal:
                    return
                if time.monotonic() >= s.deadline:
                    self._finish(s, "expired", "LOGIN_TIMEOUT")
                    return
                if s.authorize and time.monotonic() >= next_auth:
                    try:
                        s.authorize()
                    except Exception:
                        self._finish(s, "cancelled", "APPLICATION_AUTH_REVOKED")
                        return
                    next_auth = time.monotonic() + 2

    def _cleanup(self, s):
        # Serialize cleanup across EOF, cancel and timeout without holding the manager lock while waiting.
        with self._lock:
            already_cleaning = getattr(s, "cleaning", False)
            if not already_cleaning:
                s.cleaning = True
        if already_cleaning:
            s.cleanup_done.wait(8)
            return
        try:
            try:
                s.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(s.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    s.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(s.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    s.process.wait(timeout=2)
        finally:
            s.stopped.set()
            for stream in (s.media, s.process.stdin, s.process.stdout, s.diagnostic):
                if stream:
                    try:
                        stream.close()
                    except OSError:
                        pass
            for h in s.handles:
                h.close()
            s.cleanup_done.set()

    def shutdown(self):
        for s in list(self.sessions.values()):
            self.cancel(s.id, internal=True)
        for s in list(self.sessions.values()):
            self._cleanup(s)
