"""Bounded binary IPC sender; no credentials travel through stdout or WebSocket."""
from __future__ import annotations

import asyncio
import json
import socket
import struct
import time

from backend.audit_agent.crawler_login_protocol import KIND_AUTH_CANDIDATE, pack_packet


class LoginMediaWriter:
    def __init__(self, fd: int):
        self.socket = socket.socket(fileno=fd)
        self.socket.setblocking(False)
        self.lock = asyncio.Lock()
        self.sequence = 0
        self.closed = False

    async def send(self, payload: bytes, metadata: dict, *, kind=1, area_version=0):
        if self.closed:
            raise ConnectionError("Media channel closed")
        async with self.lock:
            self.sequence += 1
            packet = pack_packet(payload, metadata, kind=kind, sequence=self.sequence,
                                 area_version=area_version, capture_mono_ns=time.monotonic_ns())
            try:
                await asyncio.wait_for(asyncio.get_running_loop().sock_sendall(
                    self.socket, struct.pack("!I", len(packet)) + packet), 2)
            except BaseException:
                # Never replace an already partly written packet.
                self.close()
                raise

    async def auth(self, candidate: dict, operation_id: str):
        payload = json.dumps(candidate, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        await self.send(payload, {"operation_id": operation_id}, kind=KIND_AUTH_CANDIDATE)

    def close(self):
        self.closed = True
        self.socket.close()
