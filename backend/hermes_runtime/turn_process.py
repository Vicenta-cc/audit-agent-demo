"""Bounded, fresh-process execution of durable product Turns.

Hermes catalogs, environment flags and discovery patches are process-global.
Never reuse a process across Turns: history and tool ledgers are durable, while
an interrupted Agent or a changed catalog must not leak into another request.
"""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


class TurnProcessRunner:
    def __init__(self, scope: str, *, timeout_seconds: float = 600):
        if scope not in {"report", "creation"}:
            raise ValueError("invalid Turn process scope")
        if timeout_seconds <= 0:
            raise ValueError("Turn process timeout must be positive")
        self.scope = scope
        self.timeout_seconds = timeout_seconds

    def _command(self, turn_id: str, *, resume: bool) -> list[str]:
        return [
            sys.executable, "-m", "backend.hermes_runtime.turn_worker",
            self.scope, turn_id, "resume" if resume else "execute", str(os.getpid()),
        ]

    def run(self, turn_id: str, *, resume: bool = False) -> None:
        # exec, not fork: no inherited locks, cached Agents or plugin registry.
        # The child uses the same interpreter, configured environment and stores.
        with subprocess.Popen(
            self._command(turn_id, resume=resume),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            start_new_session=True,
        ) as process:
            try:
                code = process.wait(timeout=self.timeout_seconds)
            finally:
                # Reap the whole group on timeout, crash AND normal exit. Tool
                # subprocesses must not outlive the request that owns them.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        if code:
            raise RuntimeError(f"isolated Turn process exited with code {code}")
