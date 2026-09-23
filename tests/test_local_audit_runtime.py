import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location("local_audit_runtime", Path(__file__).parents[1] / "scripts/local_audit_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def config(tmp_path):
    secrets = tmp_path / "secrets.env"
    secrets.write_text("DASHSCOPE_API_KEY=fixture-audit\nRESOURCE_GENERATION_API_KEY=fixture-resource\nPYTHONHOME=/wrong\nPYTHONPATH=/wrong\n")
    local = tmp_path / "local.env"
    local.write_text("FFMPEG_PATH=/fixed/ffmpeg\nASR_ENGINE=dolphin\n")
    return {"python": "/fixed/.venv/bin/python", "node": "/fixed/node/bin/node", "repository": "/fixed/repo",
            "env_files": [str(secrets), str(local)], "api_port": 8398}


def test_clean_environment_keeps_venv_and_drops_calling_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "wrong-shell-key")
    monkeypatch.setenv("PYTHONPATH", "/wrong/shell")
    monkeypatch.setenv("PYTHONHOME", "/wrong/home")
    env = runtime.environment(config(tmp_path))
    assert env["DASHSCOPE_API_KEY"] == "fixture-audit"
    assert env["RESOURCE_GENERATION_API_KEY"] == "fixture-resource"
    assert env["PYTHONPATH"] == "/fixed/repo:/fixed/repo/hermes_m0"
    assert "PYTHONHOME" not in env
    assert env["PATH"].startswith("/fixed/.venv/bin:")
    assert env["FFMPEG_PATH"] == "/fixed/ffmpeg"


def test_frontend_never_receives_backend_keys(tmp_path):
    env = runtime.environment(config(tmp_path), frontend=True)
    assert env["VITE_API_PROXY_TARGET"] == "http://127.0.0.1:8398"
    assert "DASHSCOPE_API_KEY" not in env
    assert "RESOURCE_GENERATION_API_KEY" not in env


def test_missing_env_file_fails_before_launch(tmp_path):
    value = config(tmp_path)
    value["env_files"].append(str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="Required env file missing"):
        runtime.environment(value)


def test_json_credentials_are_explicit_and_not_copied_to_frontend(tmp_path):
    value = config(tmp_path)
    source = tmp_path / "old-environment.json"
    source.write_text(json.dumps({"REMOTE_INFERENCE_API_KEY": "fixture-asr", "FFMPEG_PATH": "/obsolete"}))
    value["json_env_sources"] = [{"path": str(source), "keys": ["REMOTE_INFERENCE_API_KEY"]}]
    env = runtime.environment(value)
    assert env["REMOTE_INFERENCE_API_KEY"] == "fixture-asr"
    assert env["FFMPEG_PATH"] == "/fixed/ffmpeg"
    assert "REMOTE_INFERENCE_API_KEY" not in runtime.environment(value, frontend=True)
    source.write_text("{}")
    with pytest.raises(RuntimeError, match="Missing required credential"):
        runtime.environment(value)


@pytest.mark.parametrize("actual,dirty", [("other-commit", ""), ("pinned", " M backend/main.py")])
def test_unreviewed_source_cannot_start(tmp_path, monkeypatch, actual, dirty):
    value = config(tmp_path)
    value["commit"] = "pinned"
    monkeypatch.setattr(runtime.subprocess, "check_output", Mock(side_effect=[actual, dirty]))
    with pytest.raises(RuntimeError, match="pinned clean commit"):
        runtime.verify_source(value)
