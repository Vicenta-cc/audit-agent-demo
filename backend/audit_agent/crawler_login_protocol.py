"""Strict, bounded login V2 wire protocol shared by manager and helper."""

from __future__ import annotations

import json
import math
import struct
from typing import BinaryIO

from .crawler_login_interaction import KEYS

MAGIC = b"DLV2"
PROTOCOL_VERSION = 2
KIND_FRAME = 1
KIND_AUTH_CANDIDATE = 2
HEADER = struct.Struct("!4sBBHIIIQ")
HEADER_SIZE = HEADER.size
MAX_METADATA_BYTES = 8 * 1024
MAX_FRAME_BYTES = 1024 * 1024
MAX_AUTH_BYTES = 8 * 1024 * 1024
MAX_COMMAND_BYTES = 16 * 1024
MAX_PACKET_BYTES = HEADER_SIZE + MAX_METADATA_BYTES + MAX_AUTH_BYTES
ACTIONS = frozenset(
    {
        "refresh_qr",
        "switch_to_sms",
        "request_sms",
        "submit_sms",
        "open_manual",
        "refresh_challenge",
        "close_manual",
        "abort_gesture",
    }
)
PHASE_STATUS = {
    "checking": "starting",
    "preparing": "starting",
    "qr_ready": "waiting_scan",
    "qr_expired": "waiting_scan",
    "phone_confirm": "scanned",
    "sms_required": "interactive",
    "challenge_required": "interactive",
    "manual_required": "interactive",
    "identity_check": "finalizing",
    "profile_flush": "finalizing",
    "reuse_check": "finalizing",
    "db_commit": "finalizing",
    "done": "success",
    "failed": "failed",
    "expired": "expired",
    "cancelled": "cancelled",
}


class ProtocolError(ValueError):
    pass


def phase_to_status(phase: str) -> str:
    try:
        return PHASE_STATUS[phase]
    except (KeyError, TypeError) as exc:
        raise ProtocolError("Invalid login phase") from exc


def _uint(value, bits=32):
    if type(value) is not int or not 0 <= value < 2**bits:
        raise ProtocolError("Invalid unsigned integer")
    return value


def _json(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ProtocolError("Invalid JSON") from exc


def pack_packet(
    payload: bytes,
    metadata: dict,
    *,
    kind=KIND_FRAME,
    sequence=0,
    area_version=0,
    capture_mono_ns=0,
) -> bytes:
    if type(kind) is not int or kind not in (KIND_FRAME, KIND_AUTH_CANDIDATE):
        raise ProtocolError("Invalid packet kind")
    if not isinstance(payload, bytes) or not isinstance(metadata, dict):
        raise ProtocolError("Invalid packet data")
    encoded = _json(metadata)
    limit = MAX_FRAME_BYTES if kind == KIND_FRAME else MAX_AUTH_BYTES
    if len(encoded) > MAX_METADATA_BYTES or len(payload) > limit:
        raise ProtocolError("Packet exceeds limit")
    return (
        HEADER.pack(
            MAGIC,
            PROTOCOL_VERSION,
            kind,
            len(encoded),
            len(payload),
            _uint(sequence),
            _uint(area_version),
            _uint(capture_mono_ns, 64),
        )
        + encoded
        + payload
    )


def unpack_packet(packet: bytes, *, allow_auth=False) -> dict:
    if not isinstance(packet, bytes) or len(packet) < HEADER_SIZE:
        raise ProtocolError("Truncated packet")
    magic, version, kind, meta_len, data_len, seq, area, mono = HEADER.unpack_from(
        packet
    )
    if (
        magic != MAGIC
        or version != PROTOCOL_VERSION
        or kind not in (KIND_FRAME, KIND_AUTH_CANDIDATE)
    ):
        raise ProtocolError("Invalid packet header")
    if kind == KIND_AUTH_CANDIDATE and not allow_auth:
        raise ProtocolError("Auth candidate is IPC-only")
    limit = MAX_FRAME_BYTES if kind == KIND_FRAME else MAX_AUTH_BYTES
    if (
        meta_len > MAX_METADATA_BYTES
        or data_len > limit
        or len(packet) != HEADER_SIZE + meta_len + data_len
    ):
        raise ProtocolError("Invalid packet length")
    try:
        metadata = json.loads(
            packet[HEADER_SIZE : HEADER_SIZE + meta_len],
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
        )
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError("Invalid packet metadata") from exc
    if not isinstance(metadata, dict):
        raise ProtocolError("Metadata must be an object")
    return dict(
        kind=kind,
        metadata=metadata,
        payload=packet[HEADER_SIZE + meta_len :],
        sequence=seq,
        area_version=area,
        capture_mono_ns=mono,
    )


def read_exact(stream: BinaryIO, length: int, *, boundary_eof=False) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < length:
        part = (
            stream.recv(length - len(chunks))
            if hasattr(stream, "recv")
            else stream.read(length - len(chunks))
        )
        if not part:
            if boundary_eof and not chunks:
                return None
            raise ProtocolError("Truncated stream")
        chunks.extend(part)
    return bytes(chunks)


def read_packet(stream: BinaryIO, *, allow_auth=False) -> dict | None:
    prefix = read_exact(stream, 4, boundary_eof=True)
    if prefix is None:
        return None
    length = struct.unpack("!I", prefix)[0]
    if not HEADER_SIZE <= length <= MAX_PACKET_BYTES:
        raise ProtocolError("Invalid IPC packet length")
    # Validate header before allocating an auth-sized body on a frame-only stream.
    header = read_exact(stream, HEADER_SIZE)
    magic, version, kind, meta_len, data_len, *_ = HEADER.unpack(header)
    limit = MAX_FRAME_BYTES if kind == KIND_FRAME else MAX_AUTH_BYTES
    if (
        magic != MAGIC
        or version != 2
        or kind not in (1, 2)
        or (kind == 2 and not allow_auth)
        or meta_len > MAX_METADATA_BYTES
        or data_len > limit
        or length != HEADER_SIZE + meta_len + data_len
    ):
        raise ProtocolError("Invalid IPC header")
    return unpack_packet(
        header + read_exact(stream, length - HEADER_SIZE), allow_auth=allow_auth
    )


def write_all(stream: BinaryIO, data: bytes) -> None:
    """Blocking streams only; caller sets socket timeout and closes on any write error.

    EAGAIN/timeout propagate, never retry in a busy loop or replace a partial packet.
    """
    view = memoryview(data)
    while view:
        count = stream.send(view) if hasattr(stream, "send") else stream.write(view)
        if type(count) is not int or count <= 0 or count > len(view):
            raise ProtocolError("Stream write failed")
        view = view[count:]


def write_packet(stream: BinaryIO, packet: bytes) -> None:
    unpack_packet(packet, allow_auth=True)
    write_all(stream, struct.pack("!I", len(packet)) + packet)


def _identifier(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or any(ord(c) < 33 or ord(c) > 126 for c in value)
    ):
        raise ProtocolError("Invalid identifier")


def _coordinate(value):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ProtocolError("Invalid normalized coordinate")


def validate_command(command: dict) -> dict:
    """Validate syntax only; owner, epoch, phase and action capability are runtime checks."""
    if not isinstance(command, dict) or len(_json(command)) > MAX_COMMAND_BYTES:
        raise ProtocolError("Invalid command")
    common = {
        "type",
        "command_id",
        "operation_id",
        "controller_epoch",
        "sequence",
        "expected_state_revision",
    }
    kind = command.get("type")
    extras = {
        "action": {"action", "payload", "challenge_id", "area_version"},
        "pointer_batch": {
            "challenge_id",
            "area_version",
            "gesture_id",
            "mode",
            "points",
            "focus_field_id",
        },
        "scroll": {"challenge_id", "area_version", "x", "y", "delta_x", "delta_y"},
        "key": {"challenge_id", "area_version", "key", "field_id"},
        "text": {"challenge_id", "area_version", "text", "field_id"},
        "edit_input": {
            "challenge_id",
            "area_version",
            "field_id",
            "value",
            "client_edit_revision",
            "selection_start",
            "selection_end",
            "selection_direction",
        },
        "abort_gesture": {"challenge_id", "area_version", "gesture_id"},
    }
    if (
        not isinstance(kind, str)
        or kind not in extras
        or set(command) - common - extras[kind]
        or common - set(command)
    ):
        raise ProtocolError("Invalid command fields")
    for field in ("command_id", "operation_id"):
        _identifier(command[field])
    for field in ("controller_epoch", "sequence", "expected_state_revision"):
        _uint(command[field])
    if command["controller_epoch"] == 0 or command["sequence"] == 0:
        raise ProtocolError("Command requires active controller and sequence")
    graphical = kind != "action" or command.get("action") in {
        "refresh_challenge",
        "abort_gesture",
    }
    if graphical or "challenge_id" in command or "area_version" in command:
        _identifier(command.get("challenge_id"))
        _uint(command.get("area_version"))
    if kind == "action":
        action = command.get("action")
        if not isinstance(action, str) or action not in ACTIONS:
            raise ProtocolError("Invalid action")
        payload = command.get("payload", {})
        allowed = {"request_sms": {"phone"}, "submit_sms": {"code"}}.get(action, set())
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ProtocolError("Invalid action payload")
        for key, value in payload.items():
            if (
                not isinstance(value, str)
                or not value.isascii()
                or not value.isdigit()
                or not 1 <= len(value) <= (20 if key == "phone" else 12)
            ):
                raise ProtocolError("Invalid SMS field")
        if action == "submit_sms" and not payload.get("code"):
            raise ProtocolError("SMS code required")
    elif kind == "pointer_batch":
        if "focus_field_id" in command:
            _identifier(command["focus_field_id"])
            if command.get("mode") != "click":
                raise ProtocolError("Invalid focus intent")
        _identifier(command.get("gesture_id"))
        if command.get("mode") not in {"slider", "click", "draw", "unknown"}:
            raise ProtocolError("Invalid gesture mode")
        points = command.get("points")
        if not isinstance(points, list) or not 1 <= len(points) <= 64:
            raise ProtocolError("Invalid pointer batch")
        previous = -1
        for point in points:
            if not isinstance(point, dict) or set(point) != {
                "point_sequence",
                "x",
                "y",
                "dt_ms",
                "event",
            }:
                raise ProtocolError("Invalid pointer fields")
            seq = _uint(point["point_sequence"])
            if seq <= previous or point["event"] not in {"down", "move", "up"}:
                raise ProtocolError("Invalid pointer order")
            previous = seq
            _coordinate(point["x"])
            _coordinate(point["y"])
            if type(point["dt_ms"]) is not int or not 0 <= point["dt_ms"] <= 1000:
                raise ProtocolError("Invalid pointer time")
    elif kind == "scroll":
        _coordinate(command.get("x"))
        _coordinate(command.get("y"))
        for field in ("delta_x", "delta_y"):
            value = command.get(field)
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or abs(value) > 760
            ):
                raise ProtocolError("Invalid scroll delta")
    elif kind == "key":
        if "field_id" in command:
            _identifier(command["field_id"])
        if not isinstance(command.get("key"), str) or command["key"] not in KEYS:
            raise ProtocolError("Invalid key")
    elif kind == "text":
        if "field_id" in command:
            _identifier(command["field_id"])
        value = command.get("text")
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 256
            or any(ord(c) < 32 for c in value)
        ):
            raise ProtocolError("Invalid text")
    elif kind == "edit_input":
        _identifier(command.get("field_id"))
        revision = _uint(command.get("client_edit_revision"))
        value = command.get("value")
        if (
            not revision
            or not isinstance(value, str)
            or any(ord(c) < 32 and c != "\n" for c in value)
        ):
            raise ProtocolError("Invalid edit value")
        try:
            length = len(value.encode("utf-16-le")) // 2
        except UnicodeEncodeError as exc:
            raise ProtocolError("Invalid edit Unicode") from exc
        start, end = command.get("selection_start"), command.get("selection_end")
        if (
            length > 256
            or type(start) is not int
            or type(end) is not int
            or not 0 <= start <= end <= length
        ):
            raise ProtocolError("Invalid edit selection")
        if command.get("selection_direction", "none") not in {
            "none",
            "forward",
            "backward",
        }:
            raise ProtocolError("Invalid edit direction")
    elif kind == "abort_gesture":
        _identifier(command.get("gesture_id"))
    return json.loads(_json(command))
