"""Synthetic legacy download transport; no account or external network access."""
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from backend.audit_agent import asset_utils
from backend.audit_agent.config import settings


@pytest.fixture
def legacy(monkeypatch):
    monkeypatch.setattr(settings, "crawler_proxy_enabled", False)
    monkeypatch.setattr(settings, "crawler_proxy_trial_id", "")
    monkeypatch.delenv("MEDIACRAWLER_PROXY_TRIAL_ID", raising=False)


class Response:
    status_code = 200
    headers = {}

    def __init__(self, *, fail=False):
        self.fail = fail
        self.closed = False

    @property
    def content(self):
        pytest.fail("media must not be aggregated into response.content")

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        assert chunk_size == 65536
        yield b"a" * 65536
        if self.fail:
            raise requests.exceptions.ChunkedEncodingError("synthetic truncation")
        yield b"tail"

    def close(self):
        self.closed = True


def install_session(monkeypatch, responses):
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.get.side_effect = responses
    monkeypatch.setattr(asset_utils.requests, "Session", lambda: session)
    return session


def test_streams_and_publishes_complete_file(legacy, monkeypatch, tmp_path):
    response = Response()
    session = install_session(monkeypatch, [response])
    destination = tmp_path / "media.mp4"
    result = asset_utils.download_url_with_error("https://fixture.invalid/media", destination)
    assert result.path == destination
    assert destination.stat().st_size == 65540
    with destination.open("rb") as stream:
        assert stream.read(65536) == b"a" * 65536
        assert stream.read() == b"tail"
    assert session.get.call_args.kwargs["stream"] is True
    assert response.closed
    assert list(tmp_path.iterdir()) == [destination]


def test_truncated_stream_preserves_existing_file_and_cleans_partial(legacy, monkeypatch, tmp_path):
    response = Response(fail=True)
    install_session(monkeypatch, [response])
    destination = tmp_path / "media.mp4"
    destination.write_bytes(b"prior-complete-file")
    result = asset_utils.download_url_with_error("https://fixture.invalid/media", destination)
    assert result.path is None
    assert result.error == "media request failed"
    assert destination.read_bytes() == b"prior-complete-file"
    assert response.closed
    assert list(tmp_path.iterdir()) == [destination]


def test_redirect_response_closed_without_body_read(legacy, monkeypatch, tmp_path):
    redirect, final = Response(), Response()
    redirect.status_code = 302
    redirect.headers = {"Location": "https://cdn.invalid/media"}
    session = install_session(monkeypatch, [redirect, final])
    result = asset_utils.download_url_with_error("https://fixture.invalid/media", tmp_path / "media")
    assert result.path is not None
    assert redirect.closed and final.closed
    assert [call.args[0] for call in session.get.call_args_list] == [
        "https://fixture.invalid/media", "https://cdn.invalid/media"
    ]
