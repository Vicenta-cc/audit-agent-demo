from __future__ import annotations

import json
import os
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


ACTIVE_LOGIN_STATUSES = {"starting", "waiting_scan", "finalizing"}
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

    def start(self, account: dict) -> dict:
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
                    return session.public()
                raise ValueError("已有账号正在登录，请先完成或关闭当前登录窗口")

        now = _utc_now()
        session = LoginSession(
            id=uuid4().hex,
            account_id=account["id"],
            platform=account["platform"],
            status="starting",
            created_at=_iso(now),
            updated_at=_iso(now),
            expires_at=_iso(now + timedelta(seconds=self.timeout_seconds)),
        )
        command = [
            str(self.python_path),
            str(self.helper_path),
            "--platform",
            session.platform,
            "--timeout",
            str(self.timeout_seconds),
        ]
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        root_path = str(settings.root_dir)
        current_python_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (root_path, current_python_path) if part
        )
        diagnostic_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        try:
            process = subprocess.Popen(
                command,
                cwd=str(settings.root_dir),
                env=env,
                stdout=subprocess.PIPE,
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
            diagnostic_file.close()
            raise RuntimeError(f"无法启动登录浏览器：{exc}") from exc

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
        return session.public()

    def get(self, session_id: str) -> dict | None:
        self._expire_if_needed(session_id)
        with self._lock:
            session = self._sessions.get(session_id)
            return session.public() if session else None

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
            session.qr_expires_at = ""
            session.finalizing_started_at = ""
            session.finalizing_duration_seconds = 0
            session.updated_at = _iso(_utc_now())
            process = session.process
            account_id = session.account_id
        self.store.mark_expired(account_id, session.error)
        self._stop_process(process)

    def cancel(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return False
            if session.status in TERMINAL_LOGIN_STATUSES:
                return True
            session.status = "cancelled"
            session.qr_image_data_url = ""
            session.qr_expires_at = ""
            session.finalizing_started_at = ""
            session.finalizing_duration_seconds = 0
            session.updated_at = _iso(_utc_now())
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
            self.cancel(session_id)

    def shutdown(self) -> None:
        with self._lock:
            ids = [
                session.id
                for session in self._sessions.values()
                if session.status in ACTIVE_LOGIN_STATUSES
            ]
        for session_id in ids:
            self.cancel(session_id)

    def _consume_process(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            process = session.process if session else None
        if not session or not process or not process.stdout:
            return

        for raw_line in process.stdout:
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
        diagnostic = self._read_process_diagnostic(session_id)
        with self._lock:
            current = self._sessions.get(session_id)
            needs_failure = bool(current and current.status in ACTIVE_LOGIN_STATUSES)
        if needs_failure:
            self._fail(
                session_id,
                self._unexpected_exit_message(return_code, diagnostic),
            )

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
                    account = self.store.save_auth_state(
                        session.account_id,
                        ciphertext,
                        platform_account_id=platform_account_id or None,
                    )
                    if not account:
                        raise RuntimeError("采集账号已不存在")
                    session.status = "success"
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
            session.error = message[:500]
            session.qr_image_data_url = ""
            session.qr_expires_at = ""
            session.finalizing_started_at = ""
            session.finalizing_duration_seconds = 0
            session.updated_at = _iso(_utc_now())
            account_id = session.account_id
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
