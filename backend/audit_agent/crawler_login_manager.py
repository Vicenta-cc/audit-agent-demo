from __future__ import annotations

import json
import base64
import os
import secrets
import signal
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TextIO
from uuid import uuid4

from .auth_state_cipher import AuthStateCipher, auth_state_cipher
from .config import settings
from .crawler_account_store import CrawlerAccountStore, crawler_account_store
from .crawler_browser import account_browser_env, scheduler_env
from .crawler_login_interaction import validate_login_input


ACTIVE_LOGIN_STATUSES = {"starting", "waiting_scan", "scanned", "interactive", "finalizing"}
TERMINAL_LOGIN_STATUSES = {"success", "failed", "expired", "cancelled"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


@dataclass
class LoginSession:
    id: str
    account_id: str
    platform: str
    status: str
    created_at: str
    updated_at: str
    expires_at: str
    qr_image_data_url: str = ""
    qr_expires_at: str = ""
    finalizing_started_at: str = ""
    finalizing_duration_seconds: int = 0
    platform_account_id: str = ""
    error: str = ""
    process: subprocess.Popen[str] | None = field(default=None, repr=False)
    diagnostic_file: TextIO | None = field(default=None, repr=False)
    interactive: bool = False
    owner_user_id: str = field(default="", repr=False)
    owner_token: str = field(default="", repr=False)
    frame: bytes = field(default=b"", repr=False)
    frame_sequence: int = 0
    resource_handle: object | None = field(default=None, repr=False)
    global_handle: object | None = field(default=None, repr=False)
    route_bundle: object | None = field(default=None, repr=False)
    route_pipe: object | None = field(default=None, repr=False)
    manual_recovery_claim: str = field(default="",repr=False)
    manual_finished: bool = field(default=False,repr=False)
    input_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def public(self) -> dict:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "platform": self.platform,
            "status": self.status,
            "qr_image_data_url": self.qr_image_data_url,
            "qr_expires_at": self.qr_expires_at,
            "finalizing_started_at": self.finalizing_started_at,
            "finalizing_duration_seconds": self.finalizing_duration_seconds,
            "platform_account_id": self.platform_account_id,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "interactive": self.interactive,
        }


class CrawlerAccountLoginManager:
    def __init__(
        self,
        store: CrawlerAccountStore | None = None,
        cipher: AuthStateCipher | None = None,
        python_path: Path | None = None,
        helper_path: Path | None = None,
        timeout_seconds: int | None = None,
    ):
        self.store = store or crawler_account_store
        self.cipher = cipher or auth_state_cipher
        self.python_path = Path(python_path or settings.crawler_login_python)
        self.helper_path = Path(
            helper_path or (settings.root_dir / "scripts" / "crawler_account_login.py")
        )
        self.timeout_seconds = max(
            60,
            int(timeout_seconds or settings.crawler_login_timeout_seconds),
        )
        self._sessions: dict[str, LoginSession] = {}
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()

    def start(
        self,
        account: dict,
        owner_token: str = "",
        *,
        owner_user_id: str = "",
        owner_is_admin: bool = False,
    ) -> dict:
        # Serialize check + spawn; concurrent POSTs must not launch two browsers.
        with self._start_lock:
            return self._start(
                account,
                owner_token,
                owner_user_id=owner_user_id,
                owner_is_admin=owner_is_admin,
            )

    def _start(
        self,
        account: dict,
        owner_token: str,
        *,
        owner_user_id: str,
        owner_is_admin: bool,
    ) -> dict:
        if account["platform"] == "dy":
            raise ValueError("抖音账号请使用 Login V2 登录入口")
        interactive = account["platform"] == "dy" and settings.crawler_login_interactive
        if interactive and not (32 <= len(owner_token) <= 128):
            raise PermissionError("请刷新页面后重新打开登录窗口")
        if account.get("status") == "disabled":
            raise ValueError("账号已停用，请先启用后再登录")
        if not self.python_path.is_file():
            raise RuntimeError(
                f"未找到 Playwright Python：{self.python_path}，请配置 CRAWLER_LOGIN_PYTHON"
            )
        if not self.helper_path.is_file():
            raise RuntimeError("登录脚本不存在")

        with self._lock:
            for session in self._sessions.values():
                if session.status not in ACTIVE_LOGIN_STATUSES:
                    continue
                if session.account_id == account["id"]:
                    self._check_owner(
                        session,
                        owner_token,
                        owner_user_id=owner_user_id,
                        owner_is_admin=owner_is_admin,
                    )
                    return session.public()
                raise ValueError("已有账号正在登录，请先完成或关闭当前登录窗口")

        now = _utc_now()
        timeout = settings.crawler_login_interactive_timeout_seconds if interactive else self.timeout_seconds
        session = LoginSession(
            id=uuid4().hex,
            account_id=account["id"],
            platform=account["platform"],
            status="starting",
            created_at=_iso(now),
            updated_at=_iso(now),
            expires_at=_iso(now + timedelta(seconds=timeout)),
            interactive=interactive,
            owner_user_id=str(owner_user_id or "").strip(),
            owner_token=owner_token if interactive else "",
        )
        command = [
            str(self.python_path),
            str(self.helper_path),
            "--platform",
            session.platform,
            "--timeout",
            str(timeout),
        ]
        env = os.environ.copy()
        for name in ("MEDIACRAWLER_PROXY_CONFIG_FD", "MEDIACRAWLER_PROXY_RECEIPT_FD", "MEDIACRAWLER_PROXY_REQUIRED", "MEDIACRAWLER_PROXY_PROBE_BUDGET_ID"):
            env.pop(name, None)
        if session.platform == "dy":
            browser_env = account_browser_env(session.account_id)
            env.update(browser_env)
            env.update(scheduler_env())
            command.extend(["--account-id", session.account_id])
            if interactive:
                command.append("--interactive")
            elif settings.crawler_login_headed:
                command.append("--headed")
            if account.get("platform_account_id"):
                command.extend(["--expected-platform-account-id", account["platform_account_id"]])
            # The helper and crawler load the same adapter implementation.
            env["MEDIACRAWLER_DIR"] = str(settings.media_crawler_dir.expanduser().resolve())
            env["CRAWLER_BROWSER_PROFILE_ROOT"] = str(settings.crawler_browser_profile_root)
        env["PYTHONUNBUFFERED"] = "1"
        root_path = str(settings.root_dir)
        current_python_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (root_path, current_python_path) if part
        )
        from backend.task_admission.resources import acquire_account_handle
        session.resource_handle = acquire_account_handle(session.platform, session.account_id)
        if session.resource_handle is None:
            raise ValueError("该采集账号正在采集或登录，请等待结束后再打开登录。")
        import fcntl

        global_path = (
            Path(settings.crawler_browser_profile_root).expanduser().resolve()
            / ".resource-locks" / "login-global.lock"
        )
        global_path.parent.mkdir(parents=True, exist_ok=True)
        session.global_handle = global_path.open("a+b")
        try:
            fcntl.flock(session.global_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            session.global_handle.close()
            session.resource_handle.close()
            raise ValueError("已有账号正在登录，请等待结束。")
        from .crawler_route import acquire_route_execution, RouteConfigPipe
        try:
            if session.platform == "dy":
                if settings.crawler_safety_enabled and account.get("route_state") in {"manual_required","challenge_paused","recovery_check"}:
                    from .crawler_route import reconcile_stopped_route_admissions
                    reconcile_stopped_route_admissions(self.store,session.account_id,session.resource_handle)
                    session.manual_recovery_claim = self.store.begin_manual_recovery(session.account_id,session.id,owner_id=session.owner_user_id) or ""
                    if not session.manual_recovery_claim:
                        raise ValueError('ACCOUNT_BUSY')
                session.route_bundle = acquire_route_execution(session.account_id, session.id, session.owner_user_id, account_handle=session.resource_handle, account_store=self.store, allow_probe=True, recovery_claim=session.manual_recovery_claim or None,human_login_operation_id=session.id)
                session.manual_recovery_claim=getattr(session.route_bundle,"manual_recovery_claim",None) or session.manual_recovery_claim
            session.route_pipe = RouteConfigPipe(session.route_bundle.snapshot if session.route_bundle else None)
            env.update(session.route_pipe.env)
            from .crawler_route import RouteReceiptPipe
            session.route_receipt_pipe = RouteReceiptPipe(session.route_bundle) if session.route_bundle and session.route_bundle.snapshot else None
            if session.route_receipt_pipe:
                env.update(session.route_receipt_pipe.env)
            if session.route_bundle:
                from .crawler_route import probe_env
                env.update(probe_env(session.route_bundle))
        except Exception:
            if session.manual_recovery_claim and not session.manual_finished:
                self.store.finish_manual_recovery(session.account_id,session.id,session.manual_recovery_claim,"uncertain",owner_id=session.owner_user_id)
                session.manual_finished=True
            session.resource_handle.close()
            session.global_handle.close()
            raise
        diagnostic_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        try:
            process = subprocess.Popen(
                command,
                pass_fds=tuple(set((session.resource_handle.fileno(), session.global_handle.fileno(), *(session.route_bundle.pass_fds if session.route_bundle else ()), *session.route_pipe.pass_fds, *(session.route_receipt_pipe.pass_fds if session.route_receipt_pipe else ())))),
                cwd=str(settings.root_dir),
                env=env,
                stdout=subprocess.PIPE,
                stdin=subprocess.PIPE if session.platform == "dy" else None,
                # stdout is an NDJSON protocol channel. Browser diagnostics on stderr
                # must not be merged into it or they can corrupt the final event.
                stderr=diagnostic_file,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            if session.manual_recovery_claim and not session.manual_finished:
                self.store.finish_manual_recovery(session.account_id,session.id,session.manual_recovery_claim,"uncertain",owner_id=session.owner_user_id)
                session.manual_finished=True
            session.route_pipe.close()
            if session.route_bundle:
                session.route_bundle.close()
            diagnostic_file.close()
            session.resource_handle.close()
            session.global_handle.close()
            raise RuntimeError(f"无法启动登录浏览器：{exc}") from exc

        session.route_pipe.start(process)
        if session.route_receipt_pipe:
            session.route_receipt_pipe.start(process)
        session.process = process
        session.diagnostic_file = diagnostic_file
        with self._lock:
            self._sessions[session.id] = session
        threading.Thread(
            target=self._consume_process,
            args=(session.id,),
            name=f"crawler-login-{session.id[:8]}",
            daemon=True,
        ).start()
        timer = threading.Timer(timeout + 1, self._expire_if_needed, args=(session.id,))
        timer.daemon = True
        timer.start()
        return session.public()

    @staticmethod
    def _check_owner(
        session: LoginSession,
        owner_token: str,
        *,
        owner_user_id: str = "",
        owner_is_admin: bool = False,
    ) -> None:
        caller = str(owner_user_id or "").strip()
        if (
            (session.owner_user_id or caller)
            and caller != session.owner_user_id
        ):
            raise PermissionError("该登录窗口属于另一个应用用户")
        if session.interactive and (not owner_token or not secrets.compare_digest(session.owner_token, owner_token)):
            raise PermissionError("该登录窗口属于另一个浏览器会话")

    def get(
        self,
        session_id: str,
        owner_token: str = "",
        *,
        owner_user_id: str = "",
        owner_is_admin: bool = False,
    ) -> dict | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                self._check_owner(
                    session,
                    owner_token,
                    owner_user_id=owner_user_id,
                    owner_is_admin=owner_is_admin,
                )
        self._expire_if_needed(session_id)
        with self._lock:
            session = self._sessions.get(session_id)
            return session.public() if session else None

    def get_frame(
        self,
        session_id: str,
        owner_token: str,
        after: int = 0,
        *,
        owner_user_id: str = "",
        owner_is_admin: bool = False,
    ) -> tuple[bytes, int]:
        self.get(
            session_id,
            owner_token,
            owner_user_id=owner_user_id,
            owner_is_admin=owner_is_admin,
        )
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or not session.interactive or session.status not in ACTIVE_LOGIN_STATUSES:
                raise ValueError("登录窗口已结束")
            return (session.frame if session.frame_sequence > after else b"", session.frame_sequence)

    def send_input(
        self,
        session_id: str,
        owner_token: str,
        event: dict,
        *,
        owner_user_id: str = "",
        owner_is_admin: bool = False,
    ) -> None:
        self.get(
            session_id,
            owner_token,
            owner_user_id=owner_user_id,
            owner_is_admin=owner_is_admin,
        )
        command = validate_login_input(event)
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or not session.interactive or session.status not in {"interactive", "waiting_scan", "scanned"}:
                raise ValueError("登录窗口当前不可操作")
            process = session.process
        if not process or not process.stdin or process.poll() is not None:
            raise ValueError("登录窗口已断开")
        # Do not let a stalled child block HTTP threads on a full pipe.
        payload = (json.dumps(command, ensure_ascii=True) + "\n").encode()
        with session.input_lock:
            try:
                os.set_blocking(process.stdin.fileno(), False)
                os.write(process.stdin.fileno(), payload)
            except (OSError, ValueError) as exc:
                raise ValueError("登录窗口暂时忙，请稍后重试") from exc

    def _expire_if_needed(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.status not in ACTIVE_LOGIN_STATUSES:
                return
            try:
                expired = _utc_now() >= datetime.fromisoformat(session.expires_at)
            except ValueError:
                expired = True
            if not expired:
                return
            session.status = "expired"
            session.error = "二维码登录已超时，请重新获取二维码"
            session.qr_image_data_url = ""
            session.frame = b""
            session.qr_expires_at = ""
            session.finalizing_started_at = ""
            session.finalizing_duration_seconds = 0
            session.updated_at = _iso(_utc_now())
            process = session.process
            account_id = session.account_id
        # Timeout is a login-session result, not evidence that account auth expired.
        if session.platform != "dy":
            self.store.mark_expired(account_id, session.error)
        self._stop_process(process)

    def cancel(
        self,
        session_id: str,
        owner_token: str = "",
        *,
        internal: bool = False,
        owner_user_id: str = "",
        owner_is_admin: bool = False,
    ) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return False
            if not internal:
                self._check_owner(
                    session,
                    owner_token,
                    owner_user_id=owner_user_id,
                    owner_is_admin=owner_is_admin,
                )
            if session.status in TERMINAL_LOGIN_STATUSES:
                return True
            session.status = "cancelled"
            session.qr_image_data_url = ""
            session.qr_expires_at = ""
            session.finalizing_started_at = ""
            session.finalizing_duration_seconds = 0
            session.updated_at = _iso(_utc_now())
            session.frame = b""
            process = session.process
        self._stop_process(process)
        return True

    def cancel_for_account(self, account_id: str) -> None:
        with self._lock:
            ids = [
                session.id
                for session in self._sessions.values()
                if session.account_id == account_id and session.status in ACTIVE_LOGIN_STATUSES
            ]
        for session_id in ids:
            self.cancel(session_id, internal=True)

    def shutdown(self) -> None:
        with self._lock:
            ids = [
                session.id
                for session in self._sessions.values()
                if session.status in ACTIVE_LOGIN_STATUSES
            ]
        for session_id in ids:
            self.cancel(session_id, internal=True)

    def _consume_process(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            process = session.process if session else None
        if not session or not process or not process.stdout:
            return

        try:
            from .crawler_route import CollectionRouteError, consume_route_event
            for raw_line in process.stdout:
                try:
                    if consume_route_event(raw_line, session.route_bundle):
                        continue
                except CollectionRouteError as exc:
                    self._fail(session_id, exc.code)
                    self._stop_process(process)
                    break
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                self._handle_event(session_id, event)
                with self._lock:
                    current = self._sessions.get(session_id)
                    if not current or current.status in TERMINAL_LOGIN_STATUSES:
                        break

            process.stdout.close()
            return_code = process.wait()
            if process.stdin:
                process.stdin.close()
            diagnostic = self._read_process_diagnostic(session_id)
            with self._lock:
                current = self._sessions.get(session_id)
                needs_failure = bool(current and current.status in ACTIVE_LOGIN_STATUSES)
            if needs_failure:
                self._fail(
                    session_id,
                    self._unexpected_exit_message(return_code, diagnostic),
                )
        finally:
            if session.manual_recovery_claim and not session.manual_finished:
                self.store.finish_manual_recovery(session.account_id,session.id,session.manual_recovery_claim,"cancelled" if session.status=="cancelled" else "uncertain",owner_id=session.owner_user_id)
                session.manual_finished=True
            if session.route_pipe is not None:
                session.route_pipe.close()
            if session.route_bundle is not None:
                session.route_bundle.close()
            if session.resource_handle is not None:
                session.resource_handle.close()
            if session.global_handle is not None:
                session.global_handle.close()

    def _read_process_diagnostic(self, session_id: str) -> str:
        with self._lock:
            session = self._sessions.get(session_id)
            diagnostic_file = session.diagnostic_file if session else None
            if session:
                session.diagnostic_file = None
        if not diagnostic_file:
            return ""
        try:
            diagnostic_file.seek(0)
            return diagnostic_file.read()[-4000:]
        except (OSError, ValueError):
            return ""
        finally:
            diagnostic_file.close()

    @staticmethod
    def _unexpected_exit_message(return_code: int, diagnostic: str) -> str:
        lowered = diagnostic.lower()
        if "no module named 'backend'" in lowered:
            return "登录服务模块加载失败，请重启后端后重试"
        if "no module named 'playwright'" in lowered:
            return "登录运行环境缺少 Playwright，请检查 CRAWLER_LOGIN_PYTHON"
        if "executable doesn't exist" in lowered and "playwright" in lowered:
            return "登录浏览器未安装，请在登录运行环境中安装 Playwright Chromium"
        return f"登录进程异常退出（代码 {return_code}），请重新获取二维码"

    def _handle_event(self, session_id: str, event: dict) -> None:
        event_type = str(event.get("type") or "")
        if event_type=="security_challenge":
            with self._lock:
                session=self._sessions.get(session_id)
                if session and session.platform=="dy" and session.status in ACTIVE_LOGIN_STATUSES:
                    from .crawler_route import record_login_challenge
                    record_login_challenge(session.route_bundle)
            return
        if event_type in {"interactive", "frame"}:
            with self._lock:
                session = self._sessions.get(session_id)
                if not session or not session.interactive or session.status not in ACTIVE_LOGIN_STATUSES:
                    return
                if event_type == "interactive":
                    session.status = "interactive"
                    session.updated_at = _iso(_utc_now())
                else:
                    encoded = event.get("jpeg", "")
                    if not isinstance(encoded, str) or len(encoded) > 2_000_000:
                        return
                    try:
                        frame = base64.b64decode(encoded, validate=True)
                    except ValueError:
                        return
                    if frame.startswith(b"\xff\xd8"):
                        session.frame = frame
                        session.frame_sequence += 1
            return
        if event_type == "reauth_started":
            with self._lock:
                session = self._sessions.get(session_id)
                if session and session.platform == "dy" and session.status in ACTIVE_LOGIN_STATUSES:
                    # The helper holds the same profile lock as the crawler.
                    # Cancellation must leave this account awaiting login.
                    self.store.update(session.account_id, status="login_required")
                    if session.process and session.process.stdin:
                        try:
                            session.process.stdin.write("reauth_ready\n")
                            session.process.stdin.flush()
                        except (BrokenPipeError, OSError):
                            pass  # Cancellation leaves the account awaiting login.
            return
        if event_type == "qr":
            image_data_url = str(event.get("image_data_url") or "")
            if not image_data_url.startswith("data:image/"):
                self._fail(session_id, "登录二维码数据无效")
                return
            with self._lock:
                session = self._sessions.get(session_id)
                if not session or session.status not in ACTIVE_LOGIN_STATUSES:
                    return
                session.status = "waiting_scan"
                session.qr_image_data_url = image_data_url
                session.qr_expires_at = str(event.get("expires_at") or "")
                session.finalizing_started_at = ""
                session.finalizing_duration_seconds = 0
                session.updated_at = _iso(_utc_now())
            return

        if event_type == "finalizing":
            with self._lock:
                session = self._sessions.get(session_id)
                if not session or session.status not in ACTIVE_LOGIN_STATUSES:
                    return
                now = _utc_now()
                session.status = "finalizing"
                session.finalizing_started_at = _iso(now)
                session.finalizing_duration_seconds = max(
                    1,
                    min(15, int(event.get("duration_seconds") or 3)),
                )
                session.updated_at = _iso(now)
            return

        if event_type == "scanned":
            with self._lock:
                session = self._sessions.get(session_id)
                if not session or session.status not in ACTIVE_LOGIN_STATUSES:
                    return
                session.status = "scanned"
                session.qr_image_data_url = ""
                session.qr_expires_at = ""
                session.finalizing_started_at = ""
                session.finalizing_duration_seconds = 0
                session.updated_at = _iso(_utc_now())
            return

        if event_type == "waiting_scan":
            with self._lock:
                session = self._sessions.get(session_id)
                if not session or session.status not in ACTIVE_LOGIN_STATUSES:
                    return
                session.status = "waiting_scan"
                session.finalizing_started_at = ""
                session.finalizing_duration_seconds = 0
                session.updated_at = _iso(_utc_now())
            return

        if event_type == "success":
            auth_state = event.get("auth_state")
            platform_account_id = str(event.get("platform_account_id") or "").strip()
            if len(platform_account_id) > 256:
                platform_account_id = ""
            try:
                ciphertext = self.cipher.encrypt(auth_state)
                with self._lock:
                    session = self._sessions.get(session_id)
                    if not session or session.status not in ACTIVE_LOGIN_STATUSES:
                        return
                    if session.platform == "dy":
                        expected = self.store.get(session.account_id)
                        if not platform_account_id:
                            self.store.mark_douyin_auth_invalid(session.account_id, status="login_required")
                            raise ValueError("IDENTITY_UNVERIFIED")
                        if expected and expected.get("platform_account_id") and expected["platform_account_id"] != platform_account_id:
                            self.store.mark_douyin_auth_invalid(session.account_id, status="login_required")
                            raise ValueError("IDENTITY_MISMATCH")
                        with self.store._connect() as conn:
                            conn.execute('BEGIN IMMEDIATE')
                            account = self.store.save_douyin_auth_state(session.account_id,ciphertext,platform_account_id or '',connection=conn)
                            if session.route_bundle and session.route_bundle.snapshot:
                                route = session.route_bundle.snapshot
                                if route.route_mode == 'direct_degraded':
                                    self.store.mark_direct_validated(session.account_id,route.exit_generation,connection=conn)
                                else:
                                    session.route_bundle.proxy_store.mark_route_login_validated(session.account_id,
                                        expected_binding_revision=route.binding_revision,expected_config_revision=route.config_revision,connection=conn)
                            if session.manual_recovery_claim:
                                if not self.store.finish_manual_recovery(session.account_id,session.id,session.manual_recovery_claim,'success',
                                    owner_id=session.owner_user_id,verified_identity=platform_account_id,connection=conn):
                                    raise ValueError('MANUAL_RECOVERY_REJECTED')
                            account=self.store.get(session.account_id,connection=conn)
                        if session.manual_recovery_claim:
                            session.manual_finished=True
                    else:
                        account = self.store.save_auth_state(
                            session.account_id, ciphertext, platform_account_id=platform_account_id or None
                        )
                    if not account:
                        raise RuntimeError("采集账号已不存在")
                    session.status = "success"
                    session.frame = b""
                    session.platform_account_id = account.get("platform_account_id", "")
                    session.qr_image_data_url = ""
                    session.qr_expires_at = ""
                    session.finalizing_started_at = ""
                    session.finalizing_duration_seconds = 0
                    session.updated_at = _iso(_utc_now())
            except Exception as exc:
                self._fail(session_id, f"保存登录状态失败：{exc}")
                return
            return

        if event_type == "expired":
            self._fail(
                session_id,
                str(event.get("message") or "二维码登录已超时，请重新获取二维码"),
                status="expired",
            )
            return
        if event_type == "error":
            self._fail(session_id, str(event.get("message") or "登录进程异常退出"))

    def _fail(self, session_id: str, message: str, status: str = "failed") -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.status not in ACTIVE_LOGIN_STATUSES:
                return
            session.status = status
            session.frame = b""
            session.error = message[:500]
            session.qr_image_data_url = ""
            session.qr_expires_at = ""
            session.finalizing_started_at = ""
            session.finalizing_duration_seconds = 0
            session.updated_at = _iso(_utc_now())
            account_id = session.account_id
        if session.platform != "dy":
            self.store.record_login_error(account_id, message)

    @staticmethod
    def _stop_process(process: subprocess.Popen[str] | None) -> None:
        if not process or process.poll() is not None:
            return
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                if os.name != "nt":
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    process.kill()
            except OSError:
                pass


crawler_account_login_manager = CrawlerAccountLoginManager()
