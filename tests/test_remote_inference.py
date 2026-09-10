from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from backend.audit_agent.remote_inference import RemoteInferenceClient


def client() -> RemoteInferenceClient:
    remote = RemoteInferenceClient.__new__(RemoteInferenceClient)
    remote.asr_base_url = "http://127.0.0.1:19001"
    remote.api_key = ""
    remote.timeout = 10
    remote.asr_request_retries = 2
    remote.asr_retry_backoff_seconds = 0.25
    return remote


def response(status_code: int, payload: dict | None = None) -> Mock:
    result = Mock(spec=requests.Response)
    result.status_code = status_code
    result.text = "error"
    result.json.return_value = payload or {}
    if status_code >= 400:
        result.raise_for_status.side_effect = requests.HTTPError("request failed")
    return result


def test_transcribe_retries_connection_error_and_reopens_audio(tmp_path: Path) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio-data")
    successful = response(200, {"text": "ok", "segments": []})
    observed_audio = []

    def post(*_args, **kwargs):
        observed_audio.append(kwargs["files"]["audio"][1].read())
        if len(observed_audio) == 1:
            raise requests.ConnectionError("tunnel reconnecting")
        return successful

    with patch("backend.audit_agent.remote_inference.requests.post", side_effect=post), patch(
        "backend.audit_agent.remote_inference.time.sleep"
    ) as sleep:
        result = client().transcribe(audio_path)

    assert result == {"text": "ok", "segments": []}
    assert observed_audio == [b"audio-data", b"audio-data"]
    sleep.assert_called_once_with(0.25)


def test_transcribe_retries_retryable_http_status(tmp_path: Path) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio-data")

    with patch(
        "backend.audit_agent.remote_inference.requests.post",
        side_effect=[response(503), response(200, {"text": "ok"})],
    ), patch("backend.audit_agent.remote_inference.time.sleep") as sleep:
        result = client().transcribe(audio_path)

    assert result == {"text": "ok"}
    sleep.assert_called_once_with(0.25)


def test_transcribe_does_not_retry_non_retryable_http_status(tmp_path: Path) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio-data")

    with patch(
        "backend.audit_agent.remote_inference.requests.post",
        return_value=response(401),
    ) as post, patch("backend.audit_agent.remote_inference.time.sleep") as sleep:
        with pytest.raises(RuntimeError, match="remote ASR failed: HTTP 401"):
            client().transcribe(audio_path)

    post.assert_called_once()
    sleep.assert_not_called()


def test_transcribe_raises_after_bounded_connection_retries(tmp_path: Path) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio-data")

    with patch(
        "backend.audit_agent.remote_inference.requests.post",
        side_effect=requests.ConnectionError("offline"),
    ) as post, patch("backend.audit_agent.remote_inference.time.sleep") as sleep:
        with pytest.raises(requests.ConnectionError, match="offline"):
            client().transcribe(audio_path)

    assert post.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [0.25, 0.5]
