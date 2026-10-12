"""Account-bound login V2 lifecycle, with one serialized control writer per child."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import math
import os
import queue
import secrets
import selectors
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
    ProtocolError,
)

TERMINAL = {"done", "failed", "expired", "cancelled"}
logger = logging.getLogger(__name__)
DURABLE_ACTIONS = {
    "request_sms",
    "submit_sms",
    "refresh_qr",
    "refresh_challenge",
    "switch_to_sms",
}


ERROR_MESSAGES = {
    "EDIT_TARGET_CHANGED": "输入框已变化，请点击当前输入框重新输入。",
    "EDIT_REVISION_STALE": "本次输入已过期，请继续在当前输入框操作。",
    "EDIT_VALUE_INVALID": "该输入框不支持此输入内容。",
    "EDIT_VALUE_TOO_LONG": "输入内容超过当前输入框允许的长度。",
    "QR_PLATFORM_REJECTED": "平台暂未接受本次登录，请重新打开登录窗口并按页面提示操作。",
    "PHONE_CONFIRM_REFUSED": "手机端已取消本次登录，请重新扫码。",
    "LOGIN_FAILED": "登录未完成，请查看当前登录步骤后重试。",
    "INVALID_COMMAND": "操作内容无效，请刷新登录窗口后重试。",
    "ACCOUNT_BUSY": "已有账号正在登录或采集，请等待结束后重试。",
    "ACCOUNT_DISABLED": "账号已停用，请先启用账号。",
    "SESSION_OWNER_MISMATCH": "该登录窗口属于另一应用或浏览器会话，请重新打开。",
    "OWNER_TOKEN_REQUIRED": "登录窗口信息已失效，请重新打开。",
    "SESSION_NOT_FOUND": "登录窗口已失效，请检查登录记录或重新打开。",
    "SESSION_ENDED": "本次登录已结束，请查看结果或重新登录。",
    "SESSION_REPLACED": "应用登录状态已变化，请重新登录应用。",
    "APPLICATION_AUTH_REVOKED": "应用登录或账号管理权限已失效，本次登录已停止。",
    "AUTH_REVISION_CONFLICT": "账号登录信息已被更新，请重新检查账号状态。",
    "IDENTITY_MISMATCH": "登录的抖音账号与绑定账号不一致，请使用正确账号重新扫码。",
    "IDENTITY_UNCONFIRMED": "尚未确认抖音账号身份，请查看当前登录步骤后重试。",
    "BROWSER_LOGIN_FAILED": "登录页面未能完成加载，请重新尝试。",
    "LOGIN_PREPARATION_FAILED": "登录页面暂时无法加载，请重新尝试。",
    "LOGIN_DISPLAY_UNAVAILABLE": "当前无法打开登录页面，请稍后重试或联系管理员。",
    "QR_REFRESH_UNAVAILABLE": "当前二维码暂不支持刷新，请等待平台更新或重新打开登录窗口。",
    "STALE_POINT": "本次操作轨迹已过期，请在最新验证画面重新操作。",
    "INPUT_OUTSIDE_AREA": "请在当前验证区域内操作。",
    "IDENTITY_UNVERIFIED": "尚未确认抖音账号身份，请查看当前登录步骤后重试。",
    "PROFILE_AUTHORITY_UNCONFIRMED": "浏览器登录信息尚未准备完成，请重新检查。",
    "SAVE_FAILED": "登录信息保存失败，请从登录记录恢复，暂勿重新扫码。",
    "CAPTURE_FAILED": "验证画面暂时无法获取，请重新打开登录窗口。",
    "MEDIA_CHANNEL_FAILED": "验证画面连接中断，请重新连接登录窗口。",
    "MEDIA_CHANNEL_EOF": "验证画面连接已关闭，请查看登录结果后重试。",
    "CONTROL_CHANNEL_FAILED": "登录浏览器连接中断，请查看登录记录后重试。",
    "BROWSER_DISCONNECTED": "登录浏览器已断开，请查看登录记录后重试。",
    "BROWSER_ERROR": "登录浏览器暂时不可用，请重新尝试。",
    "BROWSER_START_FAILED": "登录浏览器未能启动，请检查运行环境后重试。",
    "CONTROLLER_REPLACED": "登录窗口已重新连接，本次旧操作未执行。",
    "CONTROLLER_TIMEOUT": "登录窗口连接超时，请重新连接。",
    "TICKET_INVALID": "连接凭据已失效，请重新连接登录窗口。",
    "TICKET_LIMIT": "连接请求过于频繁，请稍后重试。",
    "CLEANUP_TIMEOUT": "登录浏览器仍在关闭，请稍后检查关闭结果，暂勿启动新登录。",
    "GESTURE_EXPIRED": "验证轨迹等待过久已停止，请在当前画面重新操作。",
    "QUEUE_FULL": "验证操作较多，已停止当前手势，请重新操作。",
    "CAPTURE_RETRY": "验证页面正在更新，请稍候。",
    "INPUT_BACKPRESSURE": "操作较多，请稍等当前操作完成后再试。",
    "STALE_STATE": "登录步骤已变化，请按当前页面重新操作。",
    "STALE_CHALLENGE": "验证码已刷新，请在最新验证画面操作。",
    "STALE_AREA": "验证区域已变化，请在最新画面操作。",
    "STALE_SEQUENCE": "本次操作已过期，请按当前页面重新操作。",
    "ACTION_NOT_AVAILABLE": "当前登录步骤不支持该操作，请查看当前页面。",
    "COMMAND_ID_CONFLICT": "操作记录不一致，请重新确认当前步骤后再试。",
    "COMMAND_FINGERPRINT_UNAVAILABLE": "原操作记录暂时无法核验，请勿重复提交。",
    "COMMAND_NOT_DISPATCHABLE": "该操作已经处理，请查询原操作结果。",
    "RECOVERY_REQUIRED": "上次登录已中断，请从登录记录恢复。",
    "RECOVERY_SOURCE_REQUIRED": "请选择需要恢复的登录记录。",
    "RECOVERY_SOURCE_FORBIDDEN": "无权恢复该登录记录。",
    "RECOVERY_SOURCE_NOT_RECOVERABLE": "该登录记录当前不能恢复，请查看其状态。",
    "RECOVERY_SOURCE_SUPERSEDED": "账号登录信息已更新，该旧记录不能再恢复。",
    "OPERATION_REQUEST_CONFLICT": "本次请求与原登录操作不一致，请重新打开登录窗口。",
    "OPERATION_NOT_ACTIVE": "本次登录操作已结束，请查看原操作结果。",
    "OPERATION_EXPIRED": "登录记录已失效，请重新检查账号状态。",
    "OPERATION_MISMATCH": "操作不属于当前登录窗口，请重新打开。",
    "LOGIN_TIMEOUT": "本次登录已超时，请重新获取二维码。",
    "REUSE_CHECK_FAILED": "登录状态未能通过重新打开检查，请重新登录。",
    "SMS_FORM_UNAVAILABLE": "短信输入步骤暂时不可用，请查看当前登录页面。",
    "PLATFORM_REJECTED": "平台未接受本次操作，请按页面提示处理。",
    "SERVER_RESTARTED": "登录服务已重启，请从登录记录恢复。",
    "INVALID_LOGIN_OPTIONS": "登录参数无效，请重新打开登录窗口。",
    "INVALID_PROTOCOLS": "当前登录方式不受支持，请刷新页面。",
    "IDEMPOTENCY_KEY_REQUIRED": "登录请求信息不完整，请重新打开登录窗口。",
    "RECOVERY_REQUIRES_V2": "当前登录方式不支持恢复，请启用新版登录。",
    "PROTOCOL_NOT_AVAILABLE": "当前服务未启用此登录方式，请使用可用方式。",
    "PROFILE_MIGRATION_REQUIRED": "旧登录信息需要迁移，请明确选择重新扫码后再继续。",
    "CURRENT_PHASE_UNKNOWN": "暂未识别当前登录步骤，请打开验证面板人工处理。",
}


ERROR_MESSAGES.update({
    "PROXY_BUSY": "代理正在使用，请稍后重试。",
    "SUPPLEMENT_DEFERRED": "代理资源暂不可用。",
    "PROXY_REVISION_MISMATCH": "账号代理配置已变化，请重新开始登录。",
    "PROXY_EXIT_CHANGED": "代理出口已变化，登录已停止。",
    "PROXY_UNAVAILABLE": "代理不可用，登录已停止。",
    "PROXY_AUTH_FAILED": "代理鉴权失败，登录已停止。",
    "PROXY_CONFIG_INVALID": "代理配置无效，登录已停止。",
    "PROBE_BUDGET_EXHAUSTED": "出口检测额度已耗尽，登录已停止。",
    "SCHEDULER_POLICY_CONFLICT": "请求保护配置冲突，登录已停止。",
})


def safe_error_code(value, fallback="LOGIN_FAILED"):
    raw = getattr(value, "code", value)
    if isinstance(value, ValueError) and not hasattr(value, "code"):
        raw = str(value)
    return raw if isinstance(raw, str) and raw in ERROR_MESSAGES else fallback


def error_message(code):
    return ERROR_MESSAGES.get(code, ERROR_MESSAGES["LOGIN_FAILED"])


def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LoginV2Error(ValueError):
    def __init__(self, code, message=None, status=409):
        self.code = safe_error_code(code)
        self.message = error_message(self.code)
        self.status = status
        super().__init__(self.message)


class CommandRejected(LoginV2Error):
    """The manager has established that this new command was not accepted."""


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
    graph_generation: int = 0
    pending_abort: bool = False


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
        if (
            not isinstance(intent, str)
            or not isinstance(browser_mode, str)
            or intent
            not in {
                "check_or_login",
                "reauth",
                "recover",
            }
            or browser_mode not in {"default", "compatibility"}
        ):
            raise LoginV2Error("INVALID_LOGIN_OPTIONS", status=400)
        if source_operation_id is not None and (
            not isinstance(source_operation_id, str)
            or not 1 <= len(source_operation_id) <= 128
            or any(ord(char) < 33 or ord(char) > 126 for char in source_operation_id)
        ):
            raise LoginV2Error("INVALID_LOGIN_OPTIONS", status=400)
        if (intent == "recover") != bool(source_operation_id):
            raise LoginV2Error("RECOVERY_SOURCE_REQUIRED", status=400)
        with self._start_lock, self._lock:
            self._prune_terminal_sessions()
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
            if op["stage"] == "committed":
                return self._committed_snapshot(op), False
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
            if op["stage"] in {"failed", "cancelled", "expired", "superseded"}:
                phase = op["stage"] if op["stage"] in TERMINAL else "failed"
                return {
                    "protocol_version": 2,
                    "engine": "v2",
                    "id": "",
                    "operation_id": op["id"],
                    "account_id": op["account_id"],
                    "phase": phase,
                    "status": phase_to_status(phase),
                    "state_revision": 1,
                    "controller_epoch": 0,
                    "controller_state": "detached",
                    "available_actions": [],
                    "cleanup_state": "complete",
                    "result": None,
                    "error": {
                        "code": safe_error_code(op["error_code"]),
                        "message": error_message(op["error_code"]),
                    },
                }, False
            if op["stage"] != "created":
                raise LoginV2Error("RECOVERY_REQUIRED")
            try:
                handles = self._leases(account["id"])
            except Exception:
                self.operations.mark_stage(
                    op["id"], "failed", error_code="ACCOUNT_BUSY"
                )
                raise
            from .crawler_route import acquire_route_execution, RouteConfigPipe
            try:
                from .crawler_route import resolve_account_route
                snapshot = resolve_account_route(account["id"], account_store=self.store, expected_binding_revision=op.get("expected_binding_revision"), expected_config_revision=op.get("expected_config_revision"), allow_probe=True)
                if (snapshot.proxy_id if snapshot else None) != op.get("proxy_id"):
                    from .crawler_route import CollectionRouteError
                    raise CollectionRouteError("PROXY_REVISION_MISMATCH")
                route_bundle = acquire_route_execution(account["id"], op["id"], user_id, account_handle=handles[-1], account_store=self.store, snapshot=snapshot, allow_probe=True)
            except Exception as exc:
                for handle in handles:
                    handle.close()
                self.operations.mark_stage(op["id"], "failed", error_code=safe_error_code(exc))
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
            s.route_bundle = route_bundle
            s.route_pipe = RouteConfigPipe(route_bundle.snapshot)
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
                "cleanup_state": "active",
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
            for name in ("MEDIACRAWLER_PROXY_CONFIG_FD", "MEDIACRAWLER_PROXY_RECEIPT_FD", "MEDIACRAWLER_ROUTE_ACK_FD", "MEDIACRAWLER_MANAGED_ROUTE", "MEDIACRAWLER_PROXY_REQUIRED", "MEDIACRAWLER_PROXY_PROBE_BUDGET_ID"):
                env.pop(name, None)
            env.update(account_browser_env(account["id"]))
            env.update(scheduler_env())
            env.update(s.route_pipe.env)
            from .crawler_route import RouteReceiptPipe
            s.route_receipt_pipe = RouteReceiptPipe(s.route_bundle) if s.route_bundle and s.route_bundle.snapshot else None
            if s.route_receipt_pipe:
                env.update(s.route_receipt_pipe.env)
            from .crawler_route import probe_env
            env.update(probe_env(route_bundle))
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
                or settings.crawler_login_v2_browser_mode == "compatibility"
            ):
                args.append("--headed")
            if settings.crawler_login_v2_resource_policy == "precise":
                args.append("--block-media")
            if self.store.get_auth_state_ciphertext(account["id"]):
                args.append("--has-auth-backup")
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
                    pass_fds=tuple(set((*tuple(h.fileno() for h in handles), child.fileno(), *route_bundle.pass_fds, *s.route_pipe.pass_fds, *(s.route_receipt_pipe.pass_fds if s.route_receipt_pipe else ())))),
                    start_new_session=True,
                    bufsize=0,
                )
                s.route_pipe.start(s.process)
                if s.route_receipt_pipe:
                    s.route_receipt_pipe.start(s.process)
                s.diagnostic = diagnostic
                self.sessions[s.id] = s
                self.operations.mark_stage(op["id"], "checking")
            except Exception:
                s.route_pipe.close()
                route_bundle.close()
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
            "cleanup_state": "complete",
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
            s.frame = b""
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
        try:
            c = validate_command(command)
        except ProtocolError as exc:
            raise CommandRejected("INVALID_COMMAND") from exc
        with self._lock:
            s = self.sessions[session_id]
            if owner_token is not None:
                self._owner(s, user_id, app_session_id, owner_token)
            elif s.user_id != user_id or s.app_session_id != app_session_id:
                raise LoginV2Error("SESSION_OWNER_MISMATCH", status=403)
            if c["operation_id"] != s.operation_id:
                raise CommandRejected("OPERATION_MISMATCH")
            kind = (
                c.get("action")
                if c["type"] == "action"
                else "manual_action"
                if c["type"] in {"key", "edit_input"}
                or c["type"] == "pointer_batch"
                and any(p["event"] != "move" for p in c["points"])
                else None
            )
            durable = kind in DURABLE_ACTIONS or kind == "manual_action"
            old = self.operations.get_command(s.operation_id, c["command_id"])
            if old and not durable:
                raise LoginV2Error("COMMAND_ID_CONFLICT")
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
            try:
                self._validate_runtime(s, c, epoch)
            except CommandRejected as exc:
                self._abort_current_stale_graphic(s, c, exc.code, epoch)
                raise
            if s.commands.full():
                if c["type"] != "action":
                    self._abort_pending_graphics(s, "INPUT_BACKPRESSURE")
                raise CommandRejected("INPUT_BACKPRESSURE")
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
            self._enqueue(
                s,
                {
                    "type": "command",
                    "command": c,
                    "queued_mono": time.monotonic(),
                    "graph_generation": s.graph_generation,
                },
            )
            return {
                "command_id": c["command_id"],
                "execution_status": "accepted",
                "platform_status": "pending",
            }

    def command_failure(self, session_id, command, exc):
        """Report recorded outcomes before classifying a new pre-dispatch rejection."""
        with self._lock:
            session = self.sessions[session_id]
            command_id = (
                command.get("command_id") if isinstance(command, dict) else None
            )
            if isinstance(command_id, str) and len(command_id) <= 128:
                try:
                    recorded = self.operations.get_command(
                        session.operation_id, command_id
                    )
                except Exception:
                    recorded = None
                    return {"execution_status": "unknown", "platform_status": "unknown"}
                if recorded:
                    return {
                        "execution_status": recorded["execution_status"],
                        "platform_status": recorded["platform_status"],
                    }
            if isinstance(exc, CommandRejected):
                return {
                    "execution_status": "rejected",
                    "platform_status": "not_applicable",
                }
            return {"execution_status": "unknown", "platform_status": "unknown"}

    def cancel_if_controller(self, session_id, epoch):
        with self._lock:
            session = self.sessions.get(session_id)
            if session and session.epoch == epoch:
                self._finish(session, "cancelled")

    def revoke_application(self, session_id, user_id, app_session_id):
        with self._lock:
            session = self.sessions.get(session_id)
            if (
                session
                and session.user_id == user_id
                and session.app_session_id == app_session_id
            ):
                self._finish(session, "cancelled", "APPLICATION_AUTH_REVOKED")

    def _validate_runtime(self, s, c, epoch=None, dispatch=False):
        if s.snapshot["phase"] in TERMINAL:
            raise CommandRejected("SESSION_ENDED")
        if (
            s.controller is None
            or s.snapshot["controller_state"] != "attached"
            or c["controller_epoch"] != s.epoch
            or epoch is not None
            and epoch != s.epoch
        ):
            raise CommandRejected("CONTROLLER_REPLACED")
        if c["expected_state_revision"] != s.snapshot["state_revision"]:
            raise CommandRejected("STALE_STATE")
        if not dispatch and c["sequence"] <= s.sequence:
            raise CommandRejected("STALE_SEQUENCE")
        capability = c["action"] if c["type"] == "action" else c["type"]
        if capability not in s.snapshot["available_actions"]:
            raise CommandRejected("ACTION_NOT_AVAILABLE")
        if "challenge_id" in c:
            area = s.snapshot.get("challenge") or {}
            if c["challenge_id"] != area.get("challenge_id") or c[
                "area_version"
            ] != area.get("area_version"):
                raise CommandRejected("STALE_CHALLENGE")
        if s.authorize:
            s.authorize()

    def _abort_current_stale_graphic(self, s, command, code, epoch=None):
        # Both admission and dispatch can invalidate a release after a prior down.
        # A replaced epoch must never release the successor's current gesture.
        if (
            command["type"] != "action"
            and code in {"STALE_STATE", "STALE_CHALLENGE", "GESTURE_EXPIRED"}
            and s.controller is not None
            and s.snapshot["controller_state"] == "attached"
            and command["controller_epoch"] == s.epoch
            and (epoch is None or epoch == s.epoch)
        ):
            self._abort_pending_graphics(s, code)

    def _abort_pending_graphics(self, s, code):
        s.graph_generation += 1
        s.pending_abort = (
            True  # One coalesced reliable abort, separate from the full input queue.
        )
        retained = []
        while True:
            try:
                message = s.commands.get_nowait()
            except queue.Empty:
                break
            command = message.get("command")
            if command and command["type"] != "action":
                self._command_rejected(s, command, code)
            else:
                retained.append(message)
        for message in retained:
            s.commands.put_nowait(message)
        self._append_result(
            s, {"type": "notice", "code": code, "message": error_message(code)}
        )

    def _writer(self, s):
        from .crawler_route import CollectionRouteError
        deferred = None
        readiness = selectors.DefaultSelector()
        try:
            fd = s.process.stdin.fileno()
            os.set_blocking(fd, False)
            readiness.register(fd, selectors.EVENT_WRITE)
            while not s.stopped.is_set():
                if deferred is None:
                    try:
                        deferred = s.commands.get(timeout=0.02)
                    except queue.Empty:
                        pass
                with self._lock:
                    if s.pending_abort:
                        message = {"type": "abort_gesture"}
                        s.pending_abort = False
                    else:
                        message = deferred
                        deferred = None
                    if message is None:
                        continue
                    c = message.get("command")
                    if c:
                        try:
                            graphical = c["type"] != "action"
                            if graphical and (
                                message.get("graph_generation", s.graph_generation)
                                != s.graph_generation
                                or c["type"] != "edit_input"
                                and time.monotonic()
                                - message.get("queued_mono", time.monotonic())
                                > 0.25
                            ):
                                raise CommandRejected("GESTURE_EXPIRED")
                            self._validate_runtime(s, c, dispatch=True)
                            if self.operations.get_command(
                                s.operation_id, c["command_id"]
                            ):
                                self.operations.dispatch_command(
                                    s.operation_id, c["command_id"]
                                )
                        except Exception as exc:
                            code = safe_error_code(exc, "INVALID_COMMAND")
                            self._abort_current_stale_graphic(s, c, code)
                            self._command_rejected(s, c, code)
                            continue
                    # Queue timestamps/generation are parent-only, never part of the wire envelope.
                    wire = {
                        key: value
                        for key, value in message.items()
                        if key not in {"queued_mono", "graph_generation"}
                    }
                    data = (json.dumps(wire, separators=(",", ":")) + "\n").encode()
                deadline = time.monotonic() + 2
                while data and not s.stopped.is_set():
                    if time.monotonic() > deadline:
                        raise TimeoutError("control writer timed out")
                    if readiness.select(timeout=0.1):
                        try:
                            count = os.write(fd, data)
                        except BlockingIOError:
                            continue
                        if count <= 0:
                            raise BrokenPipeError()
                        data = data[count:]
        except CollectionRouteError as exc:
            self._finish(s, "failed", exc.code)
        except Exception:
            self._finish(s, "failed", "CONTROL_CHANNEL_FAILED")
        finally:
            readiness.close()

    def _append_result(self, s, result):
        s.results.append(result)
        if len(s.results) > 256:
            s.results.pop(0)
            s.result_base += 1
        self._notify(s)

    def _command_rejected(self, s, c, code):
        code = safe_error_code(code, "INVALID_COMMAND")
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
                "error_message": error_message(code),
            },
        )

    def _reader(self, s):
        from .crawler_route import CollectionRouteError, consume_route_event
        try:
            while True:
                raw = s.process.stdout.readline(65537)
                if not raw:
                    break
                if len(raw) > 65536:
                    raise ValueError("oversized control event")
                if consume_route_event(raw, getattr(s, "route_bundle", None)):
                    continue
                self.handle_event(s.id, json.loads(raw))
            s.process.wait(timeout=3)
            with self._lock:
                if s.snapshot["phase"] not in TERMINAL:
                    self._finish(s, "failed", "BROWSER_DISCONNECTED")
        except CollectionRouteError as exc:
            self._finish(s, "failed", exc.code)
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
            if kind == "diagnostic_network":
                endpoint = event.get("endpoint")
                http_status = event.get("http_status")
                error_code = event.get("error_code")
                status = event.get("status")
                blocked = event.get("blocked_documents")
                import re

                if (
                    endpoint in {"get_qrcode", "check_qrconnect"}
                    and type(http_status) is int
                    and 100 <= http_status <= 599
                    and (
                        error_code is None
                        or type(error_code) is int
                        and -999999 <= error_code <= 999999
                    )
                    and (
                        status is None
                        or isinstance(status, str)
                        and re.fullmatch(r"[A-Za-z_ -]{1,32}", status)
                    )
                    and type(blocked) is int
                    and 0 <= blocked <= 100000
                ):
                    logger.warning(
                        "Douyin login network operation=%s endpoint=%s http_status=%s error_code=%s status=%s blocked_documents=%s verification=%s secondary=%s verification_header=%s token_current=%s source_current=%s",
                        s.operation_id,
                        endpoint,
                        http_status,
                        error_code,
                        status,
                        blocked,
                        event.get("has_verification_decision") is True,
                        event.get("has_secondary_decision") is True,
                        event.get("has_verification_header") is True,
                        event.get("token_matches_current") is True,
                        event.get("source_matches_current") is True,
                    )
            elif kind == "diagnostic_failure":
                stage = event.get("stage")
                exception_class = event.get("exception_class")
                pattern = event.get("error_pattern")
                if (
                    stage
                    in {
                        "browser_start",
                        "page_prepare",
                        "entry_navigation",
                        "login_entry_open",
                        "initial_observation",
                        "observation",
                        "identity_check",
                        "profile_flush",
                        "reuse_check",
                        "export",
                        "save_wait",
                    }
                    and exception_class
                    in {
                        "Error",
                        "TimeoutError",
                        "ValueError",
                        "RuntimeError",
                        "ConnectionError",
                        "CollectionRouteError",
                        "OSError",
                        "Exception",
                    }
                    and pattern
                    in {
                        "context_destroyed",
                        "target_closed",
                        "navigation_aborted",
                        "network_timeout",
                        "observation_timeout",
                        "unknown",
                    }
                ):
                    method = event.get("method")
                    if method not in {
                        "inner_text",
                        "get_attribute",
                        "evaluate",
                        "bounding_box",
                        "is_enabled",
                        "is_visible",
                        "wait_for",
                        "goto",
                        "click",
                    }:
                        method = None
                    timeout_ms = event.get("timeout_ms")
                    if type(timeout_ms) is not int or not 0 <= timeout_ms <= 120000:
                        timeout_ms = None
                    frame = event.get("source_frame")
                    import re

                    safe_frame = None
                    if (
                        isinstance(frame, dict)
                        and frame.get("file")
                        in {
                            "crawler_douyin_login_adapter.py",
                            "crawler_douyin_login_v2.py",
                        }
                        and isinstance(frame.get("function"), str)
                        and re.fullmatch(
                            r"[A-Za-z_][A-Za-z0-9_]{0,63}", frame["function"]
                        )
                        and type(frame.get("line")) is int
                        and 1 <= frame["line"] <= 10000
                    ):
                        safe_frame = (
                            f"{frame['file']}:{frame['function']}:{frame['line']}"
                        )
                    logger.warning(
                        "Douyin login failure operation=%s stage=%s exception_class=%s pattern=%s method=%s timeout_ms=%s source=%s",
                        s.operation_id,
                        stage,
                        exception_class,
                        pattern,
                        method,
                        timeout_ms,
                        safe_frame,
                    )
            elif kind == "diagnostic":
                phase = event.get("phase")
                reason = event.get("qr_reason")
                elapsed = event.get("elapsed_seconds")
                if (
                    phase
                    in {
                        "checking",
                        "preparing",
                        "qr_ready",
                        "qr_expired",
                        "phone_confirm",
                        "sms_required",
                        "challenge_required",
                        "manual_required",
                        "identity_check",
                    }
                    and reason
                    in {
                        "unknown",
                        "not_observed",
                        "image_not_visible",
                        "decoded",
                        "qr_detection_failed",
                        "capture_timeout",
                        "capture_failed",
                    }
                    and type(elapsed) in {float, int}
                    and math.isfinite(elapsed)
                    and 0 <= elapsed <= 86400
                    and type(event.get("has_auth_signal")) is bool
                    and type(event.get("has_trusted_identity")) is bool
                ):
                    logger.warning(
                        "Douyin login observation operation=%s phase=%s qr_reason=%s auth_signal=%s trusted_identity=%s elapsed=%.1f blocked_documents=%s",
                        s.operation_id,
                        phase,
                        reason,
                        event["has_auth_signal"],
                        event["has_trusted_identity"],
                        elapsed,
                        event.get("blocked_documents")
                        if type(event.get("blocked_documents")) is int
                        and 0 <= event["blocked_documents"] <= 100000
                        else None,
                    )
            elif kind == "controller_ready":
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
                if event.get("error_code"):
                    event = {
                        **event,
                        "error_code": safe_error_code(event["error_code"]),
                        "error_message": error_message(event["error_code"]),
                    }
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
            elif kind == "notice":
                code = safe_error_code(event.get("code"))
                self._append_result(
                    s, {"type": "notice", "code": code, "message": error_message(code)}
                )
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
                    or candidate.get("identity_verified") is not True
                    or candidate.get("reuse_verified") is not True
                ):
                    raise LoginV2Error("IDENTITY_UNVERIFIED")
                if s.authorize:
                    s.authorize()
                current = self.operations.get(s.operation_id)
                if not current["profile_authority_established"]:
                    raise LoginV2Error("PROFILE_AUTHORITY_UNCONFIRMED")
                changed = current.get("credentials_changed")
                if candidate.get("credentials_changed", True) is not False:
                    changed = 1
                elif current["intent"] != "recover":
                    changed = 0
                # Recovery observes a previously changed Profile; it cannot replace
                # the source operation's provenance with its own "no new login".
                self.operations.mark_stage(
                    s.operation_id, "profile_ready", credentials_changed=changed
                )
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
                    error_code=safe_error_code(exc, "SAVE_FAILED"),
                )
                self._finish(
                    s, "failed", safe_error_code(exc, "SAVE_FAILED"), recoverable=True
                )
            finally:
                s.committing = False

    def _set_terminal(self, s, phase, code):
        s.candidate = None
        code = safe_error_code(code) if code else None
        s.snapshot.update(
            phase=phase,
            status=phase_to_status(phase),
            error={"code": code, "message": error_message(code)} if code else None,
            state_revision=s.snapshot["state_revision"] + 1,
            controller_state="detached",
            cleanup_state="closing",
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
        code = safe_error_code(code) if code else None
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
        if not internal and not s.cleanup_done.wait(8):
            raise LoginV2Error("CLEANUP_TIMEOUT", status=503)
        with self._lock:
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
            if getattr(s, "route_pipe", None):
                s.route_pipe.close()
            if getattr(s, "route_bundle", None):
                s.route_bundle.close()
            for h in s.handles:
                h.close()
            with self._lock:
                s.candidate = None
                s.snapshot["cleanup_state"] = "complete"
                s.snapshot["state_revision"] += 1
                self._notify(s)
                s.cleanup_done.set()

    def _prune_terminal_sessions(self):
        removable = [
            key
            for key, session in self.sessions.items()
            if session.snapshot["phase"] in TERMINAL
            and session.cleanup_done.is_set()
            and not session.subscribers
        ]
        for key in removable[: max(0, len(removable) - 128)]:
            del self.sessions[key]

    def shutdown(self):
        for s in list(self.sessions.values()):
            self.cancel(s.id, internal=True)
        for s in list(self.sessions.values()):
            self._cleanup(s)
