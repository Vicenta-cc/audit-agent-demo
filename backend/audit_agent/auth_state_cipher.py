from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


class AuthStateCipher:
    def __init__(
        self,
        encryption_key: str | bytes | None = None,
        key_file: Path | None = None,
    ):
        configured_key = encryption_key or settings.crawler_auth_encryption_key
        key = self._normalize_key(configured_key) if configured_key else self._load_or_create_key(
            key_file or settings.crawler_auth_key_file
        )
        self._fernet = Fernet(key)

    def encrypt(self, auth_state: dict) -> str:
        if not isinstance(auth_state, dict):
            raise ValueError("auth_state 必须是对象")
        payload = json.dumps(
            auth_state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self._fernet.encrypt(payload).decode("ascii")

    def decrypt(self, ciphertext: str) -> dict:
        try:
            payload = self._fernet.decrypt(str(ciphertext or "").encode("ascii"))
        except (InvalidToken, UnicodeEncodeError) as exc:
            raise ValueError("auth_state 无法解密，请检查加密密钥") from exc
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("auth_state 内容无效")
        return value

    @staticmethod
    def _normalize_key(value: str | bytes) -> bytes:
        raw = value.encode("utf-8") if isinstance(value, str) else value
        raw = raw.strip()
        try:
            Fernet(raw)
            return raw
        except (TypeError, ValueError):
            return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())

    @staticmethod
    def _load_or_create_key(path: Path) -> bytes:
        key_path = Path(path).expanduser()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            key = key_path.read_bytes().strip()
        except FileNotFoundError:
            key = Fernet.generate_key()
            try:
                with key_path.open("xb") as handle:
                    handle.write(key + b"\n")
            except FileExistsError:
                key = key_path.read_bytes().strip()
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        return key


auth_state_cipher = AuthStateCipher()
