import pytest

from backend.audit_agent.config import environment_flag, settings
from backend.main import get_config


def test_investigation_streaming_feature_flags_default_to_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UNSET_STREAMING_FEATURE", raising=False)

    assert environment_flag("UNSET_STREAMING_FEATURE") is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " true "])
def test_investigation_streaming_feature_flags_accept_explicit_true(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_STREAMING_FEATURE", value)

    assert environment_flag("TEST_STREAMING_FEATURE") is True


def test_runtime_config_exposes_independent_streaming_feature_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "activity_stream_enabled", True)
    monkeypatch.setattr(settings, "answer_stream_enabled", False)
    monkeypatch.setattr(settings, "creation_answer_stream_enabled", True)
    runtime_config = get_config()

    assert runtime_config["activity_stream_enabled"] is True
    assert runtime_config["answer_stream_enabled"] is False
    assert runtime_config["creation_answer_stream_enabled"] is True
