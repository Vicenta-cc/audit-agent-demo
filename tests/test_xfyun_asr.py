from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

from backend.audit_agent.xfyun_asr import XfyunASRClient


def client() -> XfyunASRClient:
    value = XfyunASRClient.__new__(XfyunASRClient)
    value.app_id = "test-app"
    value.api_key = "test-key"
    value.api_secret = "test-secret"
    value.ws_url = "wss://iat.cn-huabei-1.xf-yun.com/v1"
    value.proxy_url = None
    value.request_retries = 0
    value.retry_backoff_seconds = 0
    return value


def test_first_request_uses_multilingual_auto_detection_without_ln() -> None:
    request = client()._request_payload(1, 0, b"audio")

    assert request["header"] == {"app_id": "test-app", "status": 0}
    iat = request["parameter"]["iat"]
    assert iat["domain"] == "slm"
    assert iat["language"] == "mul_cn"
    assert iat["accent"] == "mandarin"
    assert "ln" not in iat


def test_signed_url_uses_credentials_only_in_authorization_query() -> None:
    signed = urlsplit(client()._signed_url())
    query = parse_qs(signed.query)

    assert signed.scheme == "wss"
    assert signed.netloc == "iat.cn-huabei-1.xf-yun.com"
    assert signed.path == "/v1"
    assert set(query) == {"authorization", "date", "host"}
    assert "test-secret" not in signed.geturl()
    assert query["host"] == ["iat.cn-huabei-1.xf-yun.com"]


def test_merge_payloads_applies_replacement_and_keeps_language() -> None:
    payloads = [
        {"sn": 1, "ws": [{"bg": 10, "cw": [{"w": "旧", "lg": "zh"}]}]},
        {
            "sn": 2,
            "pgs": "rpl",
            "rg": [1, 1],
            "ws": [{"bg": 20, "cw": [{"w": "新文本", "lg": "zh"}]}],
        },
    ]

    text, words, language_items = client()._merge_payloads(payloads)

    assert text == "新文本"
    assert [item["text"] for item in words] == ["新文本"]
    assert [item["language"] for item in language_items] == ["zh"]


def test_result_reports_detected_language_and_provider_identity() -> None:
    result = client()._result(
        "维语文本",
        12.5,
        [],
        "sid-test",
        [],
        [
            {"language": "ug"},
            {"language": "ug"},
            {"language": "zh"},
        ],
    )

    assert result["provider"] == "xfyun_spark_mul_cn_iat"
    assert result["asr_engine"] == "xfyun"
    assert result["language"] == "ug"
    assert result["language_requested"] == "auto"
    assert result["segments"] == [
        {"start": 0.0, "end": 12.5, "text": "维语文本", "language": "ug"}
    ]


def test_video_processor_routes_xfyun_before_legacy_remote(monkeypatch) -> None:
    from backend.audit_agent import video_processor as module

    processor = module.DemoAudioProcessor.__new__(module.DemoAudioProcessor)
    processor.engine = "xfyun"
    processor.xfyun = Mock()
    processor.remote = Mock()
    processor.xfyun.transcribe.return_value = {
        "text": "ok",
        "segments": [],
        "asr_engine": "xfyun",
    }
    monkeypatch.setattr(module.settings, "use_remote_asr", True)

    result = processor.transcribe(Path("audio.wav"))

    assert result["asr_engine"] == "xfyun"
    processor.xfyun.transcribe.assert_called_once_with(Path("audio.wav"))
    processor.remote.transcribe.assert_not_called()


def test_client_enforces_service_limit_and_disables_overlap(monkeypatch) -> None:
    from backend.audit_agent import xfyun_asr as module

    value = client()
    captured = {}

    def transcribe_in_chunks(path, callback, **kwargs):
        captured.update(path=path, callback=callback, **kwargs)
        return {"text": "ok", "segments": []}

    monkeypatch.setattr(module, "transcribe_in_chunks", transcribe_in_chunks)
    monkeypatch.setattr(module.settings, "asr_chunk_seconds", 120)

    assert value.transcribe(Path("audio.wav"))["text"] == "ok"
    assert captured["max_seconds"] == 60
    assert captured["overlap_seconds"] == 0


def test_authoritative_validation_requires_xfyun_credentials(monkeypatch) -> None:
    from backend.audit_agent.config import settings
    from backend.audit_agent.pipeline import (
        AuditPipeline,
        AuditProviderUnavailableError,
    )

    monkeypatch.setattr(settings, "asr_engine", "xfyun")
    monkeypatch.setattr(settings, "use_remote_asr", True)
    monkeypatch.setattr(settings, "xfyun_app_id", "")
    monkeypatch.setattr(settings, "xfyun_api_key", "")
    monkeypatch.setattr(settings, "xfyun_api_secret", "")

    try:
        AuditPipeline._validate_authoritative_asr_configuration()
    except AuditProviderUnavailableError as exc:
        message = str(exc)
    else:
        raise AssertionError("missing XFYUN credentials must fail closed")

    assert "XFYUN_APP_ID" in message
    assert "XFYUN_API_KEY" in message
    assert "XFYUN_API_SECRET" in message
