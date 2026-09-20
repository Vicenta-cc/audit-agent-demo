from __future__ import annotations

import base64
import hashlib
import secrets


_N = 1 << 14
_R = 8
_P = 1
_KEY_LENGTH = 32


def hash_password(password: str) -> str:
    value = _validated_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        value.encode("utf-8"),
        salt=salt,
        n=_N,
        r=_R,
        p=_P,
        dklen=_KEY_LENGTH,
    )
    return "$".join(
        (
            "scrypt",
            str(_N),
            str(_R),
            str(_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(raw_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(raw_digest.encode("ascii"))
        actual = hashlib.scrypt(
            str(password).encode("utf-8"),
            salt=salt,
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(actual, expected)


def _validated_password(password: str) -> str:
    value = str(password)
    if len(value) < 12:
        raise ValueError("password must contain at least 12 characters")
    if len(value) > 256:
        raise ValueError("password is too long")
    return value
