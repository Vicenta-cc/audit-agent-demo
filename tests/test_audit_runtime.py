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
    Path(value["secrets_file"]).write_text("DASHSCOPE_API_KEY=test-audit\nRESOURCE_GENERATION_API_KEY=test-resource\nREMOTE_INFERENCE_API_KEY=test-dolphin\nCONVERSATION_API_KEY=test-conversation\n")
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


def test_migrated_account_key_is_never_recreated(config):
    import sqlite3
    root = Path(config['runtime_root'])
    (root / 'data').mkdir()
    with sqlite3.connect(root / 'data/audit_index.sqlite3') as db:
        db.execute('CREATE TABLE crawler_accounts(status TEXT, auth_state_ciphertext TEXT)')
        db.execute("INSERT INTO crawler_accounts VALUES ('active','encrypted-state')")
    key = root / 'data/crawler_auth.key'
    with pytest.raises(ValueError, match='original private runtime key'):
        runtime.verify_crawler_auth(config)
    assert not key.exists()


def test_active_work_refuses_restart_before_any_process_is_stopped(config, monkeypatch):
    import sqlite3
    root = Path(config['runtime_root'])
    (root / 'data').mkdir()
    with sqlite3.connect(root / 'data/audit_index.sqlite3') as db:
        db.execute('CREATE TABLE jobs(status TEXT)')
        db.execute("INSERT INTO jobs VALUES ('running')")
    stop = Mock()
    monkeypatch.setattr(runtime, 'stop_owned', stop)
    assert runtime.active_work(config) == {'jobs': 1}
    with pytest.raises(ValueError, match='Active work must finish'):
        runtime.lifecycle(config, 'restart')
    stop.assert_not_called()


def test_collection_context_table_is_not_mistaken_for_turn_lifecycle(config):
    import sqlite3
    root = Path(config['runtime_root'])
    (root / 'data').mkdir()
    with sqlite3.connect(root / 'data/report-collections.sqlite3') as db:
        db.execute('CREATE TABLE collection_turns(turn TEXT, session TEXT, sources TEXT)')
        db.execute("INSERT INTO collection_turns VALUES ('finished','session','[]')")
    assert runtime.active_work(config) == {}


def test_provider_settings_and_separate_proxy_environments(config, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://wrong-shell:9999")
    monkeypatch.setenv("CRAWLER_PROXY_URL", "http://wrong-shell:9999")
    config.update(model_proxy_url="http://127.0.0.1:17890", crawler_proxy_url="http://127.0.0.1:17891")
    config["settings"].update(CONVERSATION_MODEL="deepseek-pro", CONVERSATION_BASE_URL="https://api.example/v1",
                              RESOURCE_GENERATION_MODEL="deepseek-pro", RESOURCE_GENERATION_BASE_URL="https://resource.example/v1")
    env = runtime.environment(config)
    assert env["CONVERSATION_API_KEY"] == "test-conversation"
    assert env["HTTP_PROXY"] == config["model_proxy_url"]
    assert env["CRAWLER_PROXY_URL"] == config["crawler_proxy_url"]
    assert env["CONVERSATION_BASE_URL"] == "https://api.example/v1"
    assert env["RESOURCE_GENERATION_BASE_URL"] == "https://resource.example/v1"
    assert env["AUDIT_RUNTIME_MANIFEST"] == config["runtime_root"] + "/runtime.json"
    assert env["CRAWLER_AUTH_KEY_FILE"] == config["runtime_root"] + "/data/crawler_auth.key"
    assert env["REQUEST_SCHEDULER_DB"] == env["MEDIACRAWLER_REQUEST_SCHEDULER_DB"]
    frontend = runtime.environment(config, frontend=True)
    assert "HTTP_PROXY" not in frontend and "CONVERSATION_API_KEY" not in frontend
    config.pop("model_proxy_url")
    config.pop("crawler_proxy_url")
    direct = runtime.environment(config)
    assert "HTTP_PROXY" not in direct and direct["CRAWLER_PROXY_URL"] == ""


def test_legacy_manifest_without_conversation_remains_supported(config):
    for key in ("CONVERSATION_MODEL", "CONVERSATION_BASE_URL"):
        config["settings"].pop(key)
    path = Path(config["secrets_file"])
    path.write_text(path.read_text().replace("CONVERSATION_API_KEY=test-conversation\n", ""))
    assert "CONVERSATION_API_KEY" not in runtime.environment(config)
    config["settings"]["CONVERSATION_MODEL"] = "deepseek-pro"
    with pytest.raises(ValueError, match="CONVERSATION_API_KEY"):
        runtime.environment(config)


@pytest.mark.parametrize("field,value", [
    ("model_proxy_url", "http://outside:7890"), ("crawler_proxy_url", "http://user:secret@127.0.0.1:7890"),
    ("geographic_runtime", "true"), ("managed_proxy", {"binary": "/bin/proxy", "config": "/tmp/foreign", "port": 17890}),
    ("settings", {"CONVERSATION_BASE_URL": "http://insecure/v1"}),
])
def test_new_manifest_fields_are_validated(config, field, value):
    config[field] = value
    path = Path(config["runtime_root"]) / "runtime.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        runtime.load_config(path)


def test_stop_rejects_reused_or_unrelated_pid(config, monkeypatch):
    path = runtime.process_file(config, "worker")
    path.parent.mkdir()
    identity = {"pgid": 12345, "started": "old-start", "command": config["runtime_root"] + "/runtime.json _serve worker"}
    path.write_text(json.dumps({"pid": 12345, "service": "worker", "runtime_root": config["runtime_root"], "identity": identity}))
    monkeypatch.setattr(runtime, "process_snapshot", lambda _: {**identity, "started": "new-start"})
    kill = Mock()
    monkeypatch.setattr(runtime.os, "killpg", kill)
    with pytest.raises(ValueError, match="unrelated/reused"):
        runtime.stop_owned(config, "worker")
    kill.assert_not_called()
    assert path.exists()


def test_up_rolls_back_only_services_started_this_time(config, monkeypatch):
    monkeypatch.setattr(runtime, "verify_source", lambda _: None)
    monkeypatch.setattr(runtime, "ensure_proxy", lambda _: None)
    monkeypatch.setattr(runtime, "check", lambda _: {"status": "passed"})
    monkeypatch.setattr(runtime, "owned_process", lambda *_: None)
    launch = Mock(side_effect=[False, True, ValueError("frontend failed")])
    stop = Mock()
    monkeypatch.setattr(runtime, "launch_owned", launch)
    monkeypatch.setattr(runtime, "stop_owned", stop)
    with pytest.raises(ValueError, match="frontend failed"):
        runtime.lifecycle(config, "up")
    stop.assert_called_once_with(config, "worker")


def test_failed_check_rolls_back_new_managed_proxy(config, monkeypatch):
    config["managed_proxy"] = {"binary": "/bin/proxy", "config": config["runtime_root"] + "/proxy.yaml", "port": 17890}
    monkeypatch.setattr(runtime, "verify_source", lambda _: None)
    monkeypatch.setattr(runtime, "ensure_proxy", lambda _: None)
    monkeypatch.setattr(runtime, "owned_process", lambda *_: None)
    monkeypatch.setattr(runtime, "check", Mock(side_effect=ValueError("provider failed")))
    launch, stop = Mock(), Mock()
    monkeypatch.setattr(runtime, "launch_owned", launch)
    monkeypatch.setattr(runtime, "stop_owned", stop)
    with pytest.raises(ValueError, match="provider failed"):
        runtime.lifecycle(config, "up")
    launch.assert_not_called()
    stop.assert_called_once_with(config, "proxy")


def test_real_owned_child_is_stopped_without_touching_other_processes(config):
    import subprocess
    config["python"] = os.sys.executable
    command = [os.sys.executable, "-c", "import time; time.sleep(60)",
               str(Path(config["runtime_root"]) / "runtime.json"), "_serve", "worker"]
    assert runtime.launch_owned(config, "worker", command, config["runtime_root"], runtime.environment(config))
    record = runtime.owned_process(config, "worker")
    assert record["pid"] != os.getpid()
    try:
        assert not runtime.launch_owned(config, "worker", command, config["runtime_root"], runtime.environment(config))
        runtime.stop_owned(config, "worker")
        assert runtime.owned_process(config, "worker") is None
        assert not runtime.process_file(config, "worker").exists()
    finally:
        try:
            os.waitpid(record["pid"], os.WNOHANG)
        except ChildProcessError:
            pass


def test_login_v2_whitelist_and_exact_rollback(config):
    config["settings"].update(CRAWLER_LOGIN_V2_ENABLED="false", CRAWLER_LOGIN_V2_ORIGINS="https://app.example")
    old_keys = runtime.SETTING_KEYS - runtime.LOGIN_V2_SETTING_KEYS
    original = copy.deepcopy(config)
    result = runtime.login_v1_rollback_config(config, target_setting_keys=old_keys,
                                             repository="/release/protected-v1", commit="a" * 40)
    assert config == original
    assert not set(result["settings"]) & runtime.LOGIN_V2_SETTING_KEYS
    for field in ("runtime_root", "secrets_file", "crawler_repository", "crawler_python"):
        assert result[field] == original[field]
    assert runtime.environment(result)["CRAWLER_BROWSER_PROFILE_ROOT"] == runtime.environment(original)["CRAWLER_BROWSER_PROFILE_ROOT"]
    config["settings"]["FUTURE_UNKNOWN_SETTING"] = "value"
    with pytest.raises(ValueError, match="non-V2"):
        runtime.login_v1_rollback_config(config, target_setting_keys=old_keys, repository="/release/v1", commit="a" * 40)


def test_login_v2_keys_accepted_by_manifest(config):
    config["settings"].update({key: "false" for key in runtime.LOGIN_V2_SETTING_KEYS})
    path = Path(config["runtime_root"]) / "runtime.json"
    path.write_text(json.dumps(config))
    assert runtime.load_config(path) == config
