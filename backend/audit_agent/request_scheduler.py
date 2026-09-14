from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable


class RequestScheduler:
    """Persistent, process-safe request gate shared by crawler operations."""

    def __init__(
        self,
        db_path: Path,
        *,
        platform: str,
        min_interval: float = 2.0,
        per_minute: int = 30,
        cooldown_seconds: float = 300.0,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval < 0 or per_minute < 1 or cooldown_seconds < 0:
            raise ValueError("invalid request scheduler limits")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.platform = str(platform)
        self.min_interval = float(min_interval)
        self.per_minute = int(per_minute)
        self.cooldown_seconds = float(cooldown_seconds)
        self._clock = clock
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=30)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS request_scheduler_state (
                    platform TEXT PRIMARY KEY,
                    next_allowed_at REAL NOT NULL DEFAULT 0,
                    cooldown_until REAL NOT NULL DEFAULT 0,
                    request_times_json TEXT NOT NULL DEFAULT '[]',
                    last_reason TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL DEFAULT 0
                )"""
            )

    def acquire(self, operation: str = "request") -> float:
        """Reserve one request slot, sleeping until the persisted gate permits it."""
        del operation  # retained for a future audit log without changing callers
        total_wait = 0.0
        while True:
            with self._lock, self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT next_allowed_at, cooldown_until, request_times_json "
                    "FROM request_scheduler_state WHERE platform = ?",
                    (self.platform,),
                ).fetchone()
                now = float(self._clock())
                if row is None:
                    next_allowed, cooldown_until, times = 0.0, 0.0, []
                else:
                    next_allowed = float(row[0] or 0)
                    cooldown_until = float(row[1] or 0)
                    try:
                        times = [float(value) for value in json.loads(row[2] or "[]")]
                    except (TypeError, ValueError, json.JSONDecodeError):
                        times = []
                times = [value for value in times if value > now - 60.0]
                wait_until = max(next_allowed, cooldown_until)
                if len(times) >= self.per_minute:
                    wait_until = max(wait_until, min(times) + 60.0)
                wait = max(0.0, wait_until - now)
                if wait == 0.0:
                    times.append(now)
                    conn.execute(
                        "INSERT INTO request_scheduler_state "
                        "(platform,next_allowed_at,cooldown_until,request_times_json,updated_at) "
                        "VALUES (?,?,?,?,?) ON CONFLICT(platform) DO UPDATE SET "
                        "next_allowed_at=excluded.next_allowed_at, request_times_json=excluded.request_times_json, updated_at=excluded.updated_at",
                        (self.platform, now + self.min_interval, cooldown_until, json.dumps(times), now),
                    )
                    conn.commit()
                    return total_wait
                conn.rollback()
            self._sleeper(wait)
            total_wait += wait

    def enter_cooldown(self, reason: str, seconds: float | None = None) -> float:
        duration = self.cooldown_seconds if seconds is None else max(0.0, float(seconds))
        now = float(self._clock())
        until = now + duration
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO request_scheduler_state "
                "(platform,next_allowed_at,cooldown_until,request_times_json,last_reason,updated_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(platform) DO UPDATE SET "
                "cooldown_until=MAX(cooldown_until, excluded.cooldown_until), last_reason=excluded.last_reason, updated_at=excluded.updated_at",
                (self.platform, now, until, "[]", str(reason), now),
            )
        return until

    def snapshot(self) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT next_allowed_at,cooldown_until,request_times_json,last_reason "
                "FROM request_scheduler_state WHERE platform=?",
                (self.platform,),
            ).fetchone()
        if row is None:
            return {"platform": self.platform, "request_count_last_minute": 0, "cooldown_until": 0.0, "last_reason": ""}
        try:
            times = json.loads(row[2] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            times = []
        now = float(self._clock())
        return {
            "platform": self.platform,
            "request_count_last_minute": sum(1 for value in times if float(value) > now - 60.0),
            "next_allowed_at": float(row[0] or 0),
            "cooldown_until": float(row[1] or 0),
            "last_reason": str(row[3] or ""),
        }
