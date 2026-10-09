import io
import struct

import pytest

from backend.audit_agent.crawler_login_protocol import (
    HEADER_SIZE, KIND_AUTH_CANDIDATE, MAX_FRAME_BYTES, ProtocolError,
    pack_packet, phase_to_status, read_packet, unpack_packet, validate_command, write_packet,
)


def command(**changes):
    value = dict(type="action", action="refresh_qr", command_id="cmd-1", operation_id="op-1",
                 controller_epoch=1, sequence=1, expected_state_revision=0)
    value.update(changes)
    return value


def test_frame_round_trip_and_atomic_metadata():
    packet = pack_packet(b"image", {"codec": "jpeg", "controller_epoch": 2}, sequence=3, area_version=4, capture_mono_ns=5)
    assert HEADER_SIZE == 28
    decoded = unpack_packet(packet)
    assert decoded == dict(kind=1, payload=b"image", metadata={"codec": "jpeg", "controller_epoch": 2}, sequence=3, area_version=4, capture_mono_ns=5)
    stream = io.BytesIO()
    write_packet(stream, packet)
    stream.seek(0)
    assert read_packet(stream) == decoded
    assert read_packet(stream) is None


def test_auth_candidate_cannot_be_network_frame():
    packet = pack_packet(b"{}", {}, kind=KIND_AUTH_CANDIDATE)
    with pytest.raises(ProtocolError, match="IPC-only"):
        unpack_packet(packet)
    assert unpack_packet(packet, allow_auth=True)["payload"] == b"{}"


@pytest.mark.parametrize("packet", [b"", b"DLV1" + b"\0" * 24, pack_packet(b"a", {})[:-1], pack_packet(b"a", {}) + b"b"])
def test_invalid_packets_fail(packet):
    with pytest.raises(ProtocolError):
        unpack_packet(packet)


def test_packet_limits_and_nonfinite_metadata():
    for payload, meta in [(b"x" * (MAX_FRAME_BYTES + 1), {}), (b"", {"x": "y" * 8192}), (b"", {"x": float("nan")})]:
        with pytest.raises(ProtocolError):
            pack_packet(payload, meta)


class ShortStream(io.BytesIO):
    def read(self, count=-1):
        return super().read(min(count, 3))

    def write(self, data):
        return super().write(data[:2])


def test_short_writes_and_half_packets():
    packet = pack_packet(b"image", {})
    stream = ShortStream()
    write_packet(stream, packet)
    stream.seek(0)
    assert read_packet(stream)["payload"] == b"image"
    for data in [b"\0", stream.getvalue()[:-1], struct.pack("!I", 99999999)]:
        with pytest.raises(ProtocolError):
            read_packet(ShortStream(data))


def test_zero_write_is_rejected():
    class Broken:
        def write(self, data):
            return 0
    with pytest.raises(ProtocolError):
        write_packet(Broken(), pack_packet(b"", {}))


@pytest.mark.parametrize("change", [{"controller_epoch": True}, {"sequence": 0}, {"operation_id": None}, {"url": "file:///etc/passwd"}, {"type": []}, {"payload": {"script": "alert(1)"}}, {"expected_state_revision": float("nan")}])
def test_commands_reject_old_unbounded_or_invalid_structure(change):
    with pytest.raises(ProtocolError):
        validate_command(command(**change))


def test_pointer_requires_finite_coordinates_and_order():
    value = command(type="pointer_batch", challenge_id="challenge", area_version=1, gesture_id="gesture", mode="draw",
                    points=[dict(point_sequence=1, x=.2, y=.3, dt_ms=0, event="down")])
    del value["action"]
    assert validate_command(value) == value
    for coord in [float("nan"), float("inf"), True, 1.1]:
        value["points"][0]["x"] = coord
        with pytest.raises(ProtocolError):
            validate_command(value)


def test_phase_is_single_status_source():
    assert phase_to_status("qr_ready") == "waiting_scan"
    assert phase_to_status("done") == "success"
    with pytest.raises(ProtocolError):
        phase_to_status("success")


def test_v2_origins_require_explicit_origins():
    from backend.audit_agent.config import parse_login_v2_origins
    assert parse_login_v2_origins("https://app.example,http://localhost:3398", auth_mode="required", enabled=True) == ["https://app.example", "http://localhost:3398"]
    assert parse_login_v2_origins("", auth_mode="required", enabled=False) == []
    for origin in ["", "*", "https://user:secret@app.example", "https://app.example/path", "https://app.example:99999", "null"]:
        with pytest.raises(ValueError):
            parse_login_v2_origins(origin, auth_mode="required", enabled=True)


def test_pointer_batch_limit_and_eagain_are_bounded():
    value = command(type="pointer_batch", challenge_id="challenge", area_version=1, gesture_id="gesture", mode="draw",
                    points=[dict(point_sequence=i, x=.2, y=.3, dt_ms=0, event="move") for i in range(65)])
    del value["action"]
    with pytest.raises(ProtocolError):
        validate_command(value)
    class WouldBlock:
        def write(self, data):
            raise BlockingIOError("EAGAIN")
    with pytest.raises(BlockingIOError):
        write_packet(WouldBlock(), pack_packet(b"", {}))
