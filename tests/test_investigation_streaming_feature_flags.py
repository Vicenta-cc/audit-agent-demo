import pytest

from backend.audit_agent.config import (
    environment_flag,
    parse_cors_allow_origins,
    settings,
)
from backend.investigation_creation.principal import Principal
from backend import main
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
    monkeypatch.setattr(settings, "activity_recovery_turn_limit", 17)
    monkeypatch.setattr(settings, "stream_replay_batch_size", 31)
    runtime_config = get_config()

    assert runtime_config["activity_stream_enabled"] is True
    assert runtime_config["answer_stream_enabled"] is False
    assert runtime_config["creation_answer_stream_enabled"] is True
    assert runtime_config["activity_recovery_turn_limit"] == 17
    assert runtime_config["stream_replay_batch_size"] == 31


def test_runtime_paths_and_boundary_errors_are_admin_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "_authz_enabled", lambda: True)
    monkeypatch.setattr(
        main,
        "runtime_data_directory_error",
        lambda **_kwargs: "mismatch: /private/runtime/path",
    )

    ordinary = get_config(Principal("ordinary-user"))
    admin = get_config(Principal("administrator", role="admin"))

    assert ordinary["media_crawler_dir"] == ""
    assert ordinary["outputs_dir"] == ""
    assert ordinary["runtime_data_dir"] == ""
    assert ordinary["runtime_boundary_error"] == ""
    assert "/private/runtime/path" in admin["runtime_boundary_error"]


def test_authenticated_cors_rejects_wildcard_origins() -> None:
    with pytest.raises(ValueError, match="cannot contain"):
        parse_cors_allow_origins("*", auth_mode="required")
    assert parse_cors_allow_origins(
        "https://audit.example.com, https://admin.example.com",
        auth_mode="required",
    ) == ["https://audit.example.com", "https://admin.example.com"]
