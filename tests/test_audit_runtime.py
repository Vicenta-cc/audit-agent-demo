import copy
import importlib.util
import json
import os
from pathlib import Path
import plistlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location("portable_runtime", Path(__file__).parents[1] / "scripts/audit_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


@pytest.fixture
def config(tmp_path):
    root = tmp_path / "runtime"
    runtime.initialize(root)
    path = root / "runtime.json"
    value = json.loads(path.read_text())
    value.update(repository=str(tmp_path / "repo"), python="/fixed/app/.venv/bin/python", node="/fixed/node/bin/node",
                 crawler_repository="/fixed/crawler", crawler_python="/fixed/crawler/.venv/bin/python",
                 ffmpeg="/fixed/bin/ffmpeg", probe_video="/fixed/probe.mp4")
    path.write_text(json.dumps(value))
    Path(value["secrets_file"]).write_text("DASHSCOPE_API_KEY=test-audit\nRESOURCE_GENERATION_API_KEY=test-resource\nREMOTE_INFERENCE_API_KEY=test-dolphin\n")
    return value


def test_init_never_overwrites_and_creates_private_template(tmp_path):
    target = tmp_path / "new"
    result = runtime.initialize(target)
    assert result["ready"] is False
    assert (target / "secrets.env").stat().st_mode & 0o077 == 0
    original = (target / "runtime.json").read_bytes()
    with pytest.raises(ValueError, match="never overwritten"):
        runtime.initialize(target)
    assert original == (target / "runtime.json").read_bytes()


@pytest.mark.parametrize("field,value", [("schema_version", 99), ("extra", 1), ("api_port", 80),
    ("frontend_port", 8398), ("commit", "main"), ("python", "python"),
    ("environment_id", "../foreign"), ("settings", {"PYTHONPATH": "/wrong"}),
    ("settings", {"INVESTIGATION_MAX_POSTS": 30}), ("dolphin_url", "http://remote:9001"),
    ("dolphin_url", "https://user:password@remote"), ("repository", "/bad\npath")])
def test_invalid_manifests_fail(config, field, value):
    config[field] = value
    path = Path(config["runtime_root"]) / "runtime.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        runtime.load_config(path)


def test_clean_environment_and_paths_are_single_source(config, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "wrong-shell")
    monkeypatch.setenv("PYTHONHOME", "/wrong")
    env = runtime.environment(config)
    assert env["DASHSCOPE_API_KEY"] == "test-audit"
    assert env["RESOURCE_GENERATION_API_KEY"] == "test-resource"
    assert env["REMOTE_INFERENCE_API_KEY"] == "test-dolphin"
    assert "PYTHONHOME" not in env
    assert env["PYTHON_DOTENV_DISABLED"] == "1"
    assert env["PYTHONPATH"] == config["repository"] + ":" + config["repository"] + "/hermes_m0"
    assert env["APP_AUTH_MODE"] == "required"
    assert env["FFMPEG_PATH"] == config["ffmpeg"]
    assert env["ASR_ENGINE"] == "dolphin"
    assert env["USE_REMOTE_ASR"] == "true"
    assert env["PATH"].startswith("/fixed/app/.venv/bin:")
    for key in runtime.SECRET_KEYS:
        assert key not in runtime.environment(config, frontend=True)


@pytest.mark.parametrize("body", ["", "DASHSCOPE_API_KEY=your_key\n", "PATH=/wrong\n",
    "DASHSCOPE_API_KEY=same\nRESOURCE_GENERATION_API_KEY=same\nREMOTE_INFERENCE_API_KEY=asr\n"])
def test_bad_secrets_fail(config, body):
    Path(config["secrets_file"]).write_text(body)
    with pytest.raises(ValueError):
        runtime.environment(config)


def test_secret_permissions(config):
    Path(config["secrets_file"]).chmod(0o644)
    with pytest.raises(ValueError, match="mode 600"):
        runtime.environment(config)


def test_checkout_dotenv_cannot_override(config, tmp_path):
    from dotenv import load_dotenv
    import subprocess
    fake = tmp_path / ".env"
    fake.write_text("LEAKED_CHECKOUT_VALUE=bad\n")
    env = runtime.environment(config)
    result = subprocess.run([os.sys.executable, "-c", "from dotenv import load_dotenv; import os,sys; load_dotenv(sys.argv[1]); assert 'LEAKED_CHECKOUT_VALUE' not in os.environ", str(fake)], env=env)
    assert result.returncode == 0


@pytest.mark.parametrize("service", runtime.SERVICES)
def test_failed_preflight_never_launches_service(config, monkeypatch, service):
    monkeypatch.setattr(runtime, "check", Mock(side_effect=ValueError("failed closed")))
    execute = Mock()
    monkeypatch.setattr(runtime.os, "execve", execute)
    with pytest.raises(ValueError):
        runtime.start(config, service)
    execute.assert_not_called()


def test_probe_failure_reports_stage_not_provider_body(config, monkeypatch):
    monkeypatch.setattr(runtime, "verify_source", lambda _: None)
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1,
        stdout=json.dumps({"stage": "dolphin_real_transcription", "error_type": "HTTPError"}), stderr="test-secret-body"))
    with pytest.raises(ValueError, match="dolphin_real_transcription: HTTPError") as error:
        runtime.check(config)
    assert "test-secret-body" not in str(error.value)


def test_receipt_must_match_config(config, monkeypatch):
    monkeypatch.setattr(runtime, "verify_source", lambda _: None)
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0,
        stdout=json.dumps({"status": "passed", "manifest_sha256": "wrong"})))
    with pytest.raises(RuntimeError, match="does not match"):
        runtime.check(config)


def test_templates_contain_only_unified_entry_and_no_keys(config, tmp_path):
    folder = tmp_path / "services"
    runtime.render_services(config, folder)
    assert len(list(folder.iterdir())) == 6
    for file in folder.iterdir():
        text = file.read_text()
        assert "test-audit" not in text and "test-dolphin" not in text
        assert "audit_runtime.py" in text
        if file.suffix == ".plist":
            data = plistlib.loads(file.read_bytes())
            assert data["ProgramArguments"][0] == config["python"]
            assert "start" in data["ProgramArguments"]
        else:
            assert "RestartSec=120" in text
    with pytest.raises(FileExistsError):
        runtime.render_services(config, folder)


@pytest.mark.parametrize("sha,dirty", [("0" * 40, ""), (None, " M file")])
def test_source_mismatch_fails(config, monkeypatch, sha, dirty):
    monkeypatch.setattr(runtime.subprocess, "check_output", Mock(side_effect=[sha or config["commit"], dirty]))
    with pytest.raises(ValueError, match="Pinned source"):
        runtime.verify_source(config)


def test_empty_database_initialization_in_real_clean_subprocess(config):
    import subprocess
    import sqlite3
    repo = Path(__file__).parents[1].resolve()
    config["repository"] = str(repo)
    config["python"] = os.sys.executable
    path = Path(config["runtime_root"]) / "runtime.json"
    path.write_text(json.dumps(config))
    result = subprocess.run(runtime.child(config, "_init-db"), env=runtime.environment(config),
                            cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
    for name in ("audit_index.sqlite3", "investigation_creation.sqlite3"):
        with sqlite3.connect(Path(config["runtime_root"]) / "data" / name) as db:
            assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("service", runtime.SERVICES)
def test_successful_start_uses_fixed_python_and_private_environment(config, monkeypatch, service):
    monkeypatch.setattr(runtime, "check", lambda _: {"status": "passed"})
    monkeypatch.setattr(runtime.os, "chdir", Mock())
    execute = Mock()
    monkeypatch.setattr(runtime.os, "execve", execute)
    runtime.start(config, service)
    executable, args, env = execute.call_args.args
    assert executable == config["python"]
    assert args[-2:] == ["_serve", service]
    assert ("DASHSCOPE_API_KEY" in env) == (service != "frontend")


def test_receipt_saved_without_secrets(config, monkeypatch):
    monkeypatch.setattr(runtime, "verify_source", lambda _: None)
    receipt = {"status": "passed", "manifest_sha256": runtime.fingerprint(config)}
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(receipt)))
    assert runtime.check(config) == receipt
    path = Path(config["runtime_root"]) / "receipts/check-latest.json"
    assert json.loads(path.read_text()) == receipt
    assert path.stat().st_mode & 0o077 == 0


def test_installed_dependency_check_works_without_pip():
    runtime.check_dependencies()
