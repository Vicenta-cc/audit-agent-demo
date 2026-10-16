"""Pinned local runtime: configuration, preflight and owned process lifecycle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import time

SECRET_KEYS = {"DASHSCOPE_API_KEY", "RESOURCE_GENERATION_API_KEY", "CONVERSATION_API_KEY", "REMOTE_INFERENCE_API_KEY", "CRAWLER_AUTH_ENCRYPTION_KEY"}
SETTING_KEYS = {
    "QWEN_TEXT_MODEL", "RESOURCE_GENERATION_MODEL", "QWEN_VL_MODEL", "QWEN_IMAGE_AUDIT_MODEL",
    "QWEN_CONTACT_SHEET_MODEL", "ASR_TRANSLATE_MODEL", "ASR_LANGUAGE", "DOLPHIN_LANG_SYM",
    "DOLPHIN_REGION_SYM", "INVESTIGATION_MAX_POSTS", "APP_AUTH_COOKIE_SECURE",
    "TASK_WORKER_PROCESSES", "TASK_EXECUTION_CAPACITY", "TASK_ANALYSIS_CAPACITY",
    "CRAWLER_LOGIN_HEADED", "CORS_ALLOW_ORIGINS",
    "CONVERSATION_MODEL", "CONVERSATION_BASE_URL", "RESOURCE_GENERATION_BASE_URL", "DASHSCOPE_BASE_URL",
    "APP_AUTH_COOKIE_NAME", "HERMES_CREATION_FAKE_RUNTIME", "INVESTIGATION_ACTIVITY_STREAM_ENABLED",
    "INVESTIGATION_ANSWER_STREAM_ENABLED", "INVESTIGATION_CREATION_ANSWER_STREAM_ENABLED",
    "INVESTIGATION_MULTI_ROUND_ENABLED", "CONTINUOUS_RESOURCE_SESSION_ENABLED", "REPORT_COLLECTION_ENABLED",
    "REPORT_COLLECTION_AGENT_DRIVEN", "REPORT_COLLECTION_DIRECT_DELIVERY", "TRIAGE_MODE", "TRIAGE_MODEL",
    "SEARCH_TERMS_MAX", "TRIAGE_CANDIDATES_PER_KEYWORD", "TRIAGE_CANDIDATE_COMMENTS", "TRIAGE_ACCOUNTS_PER_TASK",
    "M3_COMMENTS_PER_POST", "M3_POSTS_PER_KEYWORD", "M3_ANALYZE_LIMIT", "CRAWLER_MAX_CONCURRENCY",
    "REQUEST_CONCURRENCY", "HERMES_INVESTIGATION_MAX_WORKERS", "HERMES_TURN_TIMEOUT_SECONDS",
    "REPORT_REQUEST_TIMEOUT", "QWEN_MAX_TOKENS", "QWEN_REPORT_MODEL", "DY_MEDIA_TIMEOUT_SECONDS",
}
FIELDS = {"schema_version", "environment_id", "repository", "commit", "runtime_root", "python", "node",
          "crawler_repository", "crawler_commit", "crawler_python", "ffmpeg", "probe_video",
          "api_port", "frontend_port", "secrets_file", "dolphin_url", "settings"}
SERVICES = ("api", "worker", "frontend")
OPTIONAL_FIELDS = {"model_proxy_url", "crawler_proxy_url", "managed_proxy", "geographic_runtime"}
PROBE_STAGE = "configuration"


def write_new(path, text):
    # Never replace an existing manifest, credential or service definition.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(text)


def initialize(directory):
    root = directory.absolute()
    if root.exists():
        raise ValueError("init requires a new directory; existing runtime is never overwritten")
    root.mkdir(parents=True, mode=0o700)
    repo = Path(__file__).resolve().parents[1]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    config = dict(schema_version=1, environment_id="audit-development", repository=str(repo), commit=head,
                  runtime_root=str(root), python=sys.executable, node=shutil.which("node") or "/EDIT/node",
                  crawler_repository="/EDIT/media-crawler", crawler_commit="efafe3186400b1020955c6acfdc201235b84a2d8",
                  crawler_python="/EDIT/media-crawler/.venv/bin/python", ffmpeg=shutil.which("ffmpeg") or "/EDIT/ffmpeg",
                  probe_video="/EDIT/non-sensitive-short-video.mp4", api_port=8398, frontend_port=3398,
                  secrets_file=str(root / "secrets.env"), dolphin_url="http://127.0.0.1:19001",
                  settings={"QWEN_TEXT_MODEL": "qwen3.7-plus", "RESOURCE_GENERATION_MODEL": "deepseek-v4-pro-ga-260813",
                            "RESOURCE_GENERATION_BASE_URL": "https://api.qhose.net/v1",
                            "CONVERSATION_MODEL": "deepseek-v4-pro-ga-260813", "CONVERSATION_BASE_URL": "https://api.qhose.net/v1",
                            "QWEN_IMAGE_AUDIT_MODEL": "qwen3.6-flash", "QWEN_CONTACT_SHEET_MODEL": "qwen3.6-flash",
                            "APP_AUTH_COOKIE_SECURE": "false", "INVESTIGATION_MAX_POSTS": "30",
                            "ASR_LANGUAGE": "auto", "DOLPHIN_LANG_SYM": "auto", "TASK_WORKER_PROCESSES": "1"})
    write_new(root / "runtime.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    write_new(root / "secrets.env", "# Private values only; never commit this file.\n" +
              "\n".join(key + "=" for key in sorted(SECRET_KEYS)) + "\n")
    return {"status": "template_created", "config": str(root / "runtime.json"), "ready": False}


def load_config(path):
    config = json.loads(path.read_text())
    if FIELDS - set(config) or set(config) - FIELDS - OPTIONAL_FIELDS or config["schema_version"] != 1:
        raise ValueError("Unsupported runtime manifest fields/schema")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,48}", config["environment_id"]):
        raise ValueError("Invalid environment_id")
    for key in ("repository", "runtime_root", "python", "node", "crawler_repository", "crawler_python",
                "ffmpeg", "probe_video", "secrets_file"):
        if not isinstance(config[key], str) or not Path(config[key]).is_absolute() or "/EDIT/" in config[key] or any(c in config[key] for c in "\r\n\x00"):
            raise ValueError("Fill in absolute path: " + key)
    root = Path(config["runtime_root"]).resolve()
    if path.resolve().parent != root or Path(config["secrets_file"]).resolve().parent != root:
        raise ValueError("Manifest and secrets must be directly inside runtime_root")
    repo = Path(config["repository"]).resolve()
    if root == repo or root.is_relative_to(repo) or repo.is_relative_to(root):
        raise ValueError("Runtime data and source checkout must be separate")
    if any(type(config[key]) is not int or not 1024 <= config[key] <= 65535 for key in ("api_port", "frontend_port")):
        raise ValueError("Ports must be integers in 1024..65535")
    if config["api_port"] == config["frontend_port"]:
        raise ValueError("API and frontend ports must differ")
    for key in ("commit", "crawler_commit"):
        if not re.fullmatch(r"[a-f0-9]{40}", config[key]):
            raise ValueError("Use full source SHA: " + key)
    if not isinstance(config["settings"], dict) or set(config["settings"]) - SETTING_KEYS:
        raise ValueError("Unknown/protected setting; runtime paths belong to the manifest")
    if any(not isinstance(v, str) for v in config["settings"].values()):
        raise ValueError("Settings values must be strings")
    from urllib.parse import urlsplit
    url = urlsplit(config["dolphin_url"])
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Invalid Dolphin URL; credentials belong in secrets.env")
    if url.scheme == "http" and url.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Use HTTPS for remote Dolphin or a loopback SSH tunnel")
    for key in ("CONVERSATION_BASE_URL", "RESOURCE_GENERATION_BASE_URL", "DASHSCOPE_BASE_URL"):
        if key in config["settings"]:
            provider = urlsplit(config["settings"][key])
            if provider.scheme != "https" or not provider.hostname or provider.username or provider.password or provider.query or provider.fragment:
                raise ValueError("Provider URL must use HTTPS without credentials: " + key)
    for key in ("model_proxy_url", "crawler_proxy_url"):
        if key in config and not isinstance(config[key], str):
            raise ValueError("Proxy URL must be a string: " + key)
        if config.get(key):
            proxy = urlsplit(config[key])
            if proxy.scheme not in ("http", "https", "socks5", "socks5h") or proxy.hostname not in ("127.0.0.1", "localhost", "::1") or not proxy.port or proxy.username or proxy.password or proxy.query or proxy.fragment or proxy.path not in ("", "/"):
                raise ValueError("Proxy must be an explicit loopback URL without credentials: " + key)
    if "geographic_runtime" in config and type(config["geographic_runtime"]) is not bool:
        raise ValueError("geographic_runtime must be boolean")
    if "managed_proxy" in config:
        proxy = config["managed_proxy"]
        if not isinstance(proxy, dict) or set(proxy) != {"binary", "config", "port"}:
            raise ValueError("managed_proxy requires binary, config and port")
        if any(not isinstance(proxy[k], str) or not Path(proxy[k]).is_absolute() for k in ("binary", "config")):
            raise ValueError("Managed proxy paths must be absolute")
        if not Path(proxy["config"]).resolve().is_relative_to(root):
            raise ValueError("Managed proxy config must be inside runtime_root")
        if type(proxy["port"]) is not int or not 1024 <= proxy["port"] <= 65535 or proxy["port"] in (config["api_port"], config["frontend_port"]):
            raise ValueError("Invalid managed proxy port")
    return config


def verify_source(config):
    for repository, commit in (("repository", "commit"), ("crawler_repository", "crawler_commit")):
        repo = Path(config[repository])
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True).strip()
        if sha != config[commit] or dirty:
            raise ValueError("Pinned source must be clean and match manifest: " + repository)
    if Path(__file__).resolve() != Path(config["repository"]).resolve() / "scripts/audit_runtime.py":
        raise ValueError("Launch the controller from the pinned checkout")
    for key in ("python", "node", "crawler_python", "ffmpeg"):
        if not os.access(config[key], os.X_OK):
            raise ValueError("Missing executable: " + key)
    if not Path(config["probe_video"]).is_file():
        raise ValueError("Missing probe video")


def environment(config, *, frontend=False):
    root = Path(config["runtime_root"])
    repo = config["repository"]
    env = {"HOME": str(Path.home()), "LANG": "en_US.UTF-8", "PYTHONUNBUFFERED": "1",
           "PATH": ":".join([str(Path(config["python"]).parent), str(Path(config["node"]).parent),
                             "/usr/bin", "/bin", "/usr/sbin", "/sbin"])}
    if frontend:
        env["VITE_API_PROXY_TARGET"] = f"http://127.0.0.1:{config['api_port']}"
        return env
    from dotenv import dotenv_values
    secret_file = Path(config["secrets_file"])
    if secret_file.stat().st_mode & 0o077:
        raise ValueError("secrets.env must have mode 600 (no group/world permissions)")
    secrets = dotenv_values(secret_file, interpolate=False)
    if set(secrets) - SECRET_KEYS:
        raise ValueError("secrets.env contains unknown keys; settings belong in runtime.json")
    required = SECRET_KEYS - {"CRAWLER_AUTH_ENCRYPTION_KEY", "CONVERSATION_API_KEY"}
    if any(k.startswith("CONVERSATION_") for k in config["settings"]):
        required.add("CONVERSATION_API_KEY")
    for key in required:
        if not secrets.get(key) or str(secrets[key]).startswith(("REPLACE", "your_", "<")):
            raise ValueError("Missing private credential: " + key)
    if secrets["DASHSCOPE_API_KEY"] == secrets["RESOURCE_GENERATION_API_KEY"]:
        raise ValueError("Audit and resource-generation credentials must be separate")
    env.update({k: v for k, v in secrets.items() if v})
    # Disable implicit checkout .env loading in python-dotenv >=1.2; no shell inheritance.
    env.update(PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=f"{repo}:{repo}/hermes_m0",
               AUDIT_RUNTIME_MANIFEST=str(root / "runtime.json"),
               XHS_AUDIT_DATA_DIR=str(root / "data"), XHS_AUDIT_OUTPUTS_DIR=str(root / "outputs"),
               APP_AUTH_DB=str(root / "data/investigation_creation.sqlite3"), HERMES_HOME=str(root / "hermes"),
               CRAWLER_BROWSER_PROFILE_ROOT=str(root / "browser-profiles"), CRAWLER_AUTH_KEY_FILE=str(root / "data/crawler_auth.key"),
               REQUEST_SCHEDULER_DB=str(root / "scheduler.sqlite3"), MEDIACRAWLER_REQUEST_SCHEDULER_DB=str(root / "scheduler.sqlite3"),
               TASK_RESOURCE_LOCK_DIR=str(root / "resource-locks"),
               MEDIACRAWLER_DIR=config["crawler_repository"], CRAWLER_LOGIN_PYTHON=config["crawler_python"],
               APP_AUTH_MODE="required", DASHSCOPE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
               RESOURCE_GENERATION_BASE_URL="https://www.dmxapi.cn/v1", FFMPEG_PATH=config["ffmpeg"],
               ASR_ENGINE="dolphin", USE_REMOTE_ASR="true", REMOTE_ASR_BASE_URL=config["dolphin_url"],
               ASR_TRANSLATE_ENABLE_THINKING="false", USE_REMOTE_VLM="false", USE_REMOTE_LLM="false")
    env.update(config["settings"])
    env["CRAWLER_PROXY_URL"] = config.get("crawler_proxy_url", "")
    if config.get("model_proxy_url"):
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env[key] = config["model_proxy_url"]
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost,::1"
    return env


def fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def check_dependencies():
    # Runtime wheels installed by uv need not contain pip itself.
    from importlib import metadata
    from packaging.requirements import Requirement
    for distribution in metadata.distributions():
        for raw in distribution.requires or []:
            requirement = Requirement(raw)
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue
            version = metadata.version(requirement.name)
            if requirement.specifier and not requirement.specifier.contains(version, prereleases=True):
                raise ValueError("Installed dependency version conflict")


def verify_crawler_auth(config):
    """Refuse to replace a missing key for already encrypted account states."""
    import sqlite3
    root = Path(config["runtime_root"])
    with sqlite3.connect((root / "data/audit_index.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='crawler_accounts'").fetchone()
        rows = db.execute("SELECT auth_state_ciphertext FROM crawler_accounts WHERE status='active' AND auth_state_ciphertext IS NOT NULL").fetchall() if exists else []
    if rows:
        key = root / "data/crawler_auth.key"
        if not key.is_file() or key.stat().st_mode & 0o077:
            raise ValueError("Migrated crawler accounts require the original private runtime key")
        from backend.audit_agent.auth_state_cipher import AuthStateCipher
        cipher = AuthStateCipher(key_file=key)
        if not all(cipher.decrypt(row[0]) for row in rows):
            raise ValueError("Crawler account authentication cannot be decrypted")
    return len(rows)


def probe(config):
    """Executed only in the fixed Python with the same clean environment as services."""
    global PROBE_STAGE
    PROBE_STAGE = "crawler_authentication"
    verify_crawler_auth(config)
    PROBE_STAGE = "python_imports"
    import sqlite3
    import cv2
    import pydantic_core
    import hermes_cli
    from backend.hermes_runtime import turn_worker  # noqa: F401
    import real_report_repository  # noqa: F401
    from backend.audit_agent.pipeline import AuditPipeline
    from backend.audit_agent.qwen_client import QwenClient
    from backend.audit_agent.video_processor import DemoAudioProcessor
    import requests
    if hermes_cli.__version__ != "0.20.4":
        raise ValueError("Unexpected Hermes version")
    PROBE_STAGE = "python_dependency_consistency"
    check_dependencies()
    PROBE_STAGE = "crawler_imports"
    subprocess.run([config["crawler_python"], "-c", "from media_platform.douyin import DouYinCrawler; import playwright"],
                   cwd=config["crawler_repository"], check=True, capture_output=True)
    PROBE_STAGE = "frontend_dependencies"
    frontend = Path(config["repository"]) / "Audit_assistant"
    if not (frontend / "node_modules/vite/bin/vite.js").is_file():
        raise ValueError("Install frontend dependencies with npm ci")
    node_version = subprocess.check_output([config["node"], "--version"], text=True).strip()
    root = Path(config["runtime_root"])
    audio = root / "receipts/probe.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    PROBE_STAGE = "ffmpeg_real_extraction"
    subprocess.run([config["ffmpeg"], "-hide_banner", "-loglevel", "error", "-i", config["probe_video"],
                    "-t", "3", "-vn", "-ar", "16000", "-ac", "1", "-y", str(audio)], check=True, capture_output=True)
    PROBE_STAGE = "dolphin_real_transcription"
    processor = DemoAudioProcessor()
    transcript = AuditPipeline._validated_authoritative_transcript(processor.transcribe(audio))
    if (transcript.get("asr_engine") or transcript.get("provider")) != "dolphin":
        raise ValueError("Transcription did not use Dolphin")
    PROBE_STAGE = "audit_provider"
    if QwenClient().audit_text('Return only JSON: {"ok":true}', max_tokens=128, enable_thinking=False).get("ok") is not True:
        raise ValueError("Audit provider JSON probe failed")
    # Unlike the old local probe, verify the resource provider's own credential too.
    PROBE_STAGE = "resource_provider"
    resource_model = os.environ.get("RESOURCE_GENERATION_MODEL", "qwen3.7-plus")
    response = requests.post(os.environ["RESOURCE_GENERATION_BASE_URL"].rstrip("/") + "/chat/completions",
                             headers={"Authorization": "Bearer " + os.environ["RESOURCE_GENERATION_API_KEY"]},
                             json={"model": resource_model,
                                   "messages": [{"role": "user", "content": "Reply only OK"}], "max_tokens": 64,
                                   **({"thinking": {"type": "disabled"}} if resource_model.lower().startswith("deepseek") else {"enable_thinking": False})}, timeout=60)
    response.raise_for_status()
    if not response.json().get("choices", [{}])[0].get("message", {}).get("content"):
        raise ValueError("Resource provider returned no content")
    conversation_checked = False
    if os.environ.get("CONVERSATION_API_KEY"):
        PROBE_STAGE = "conversation_provider"
        response = requests.post(os.environ.get("CONVERSATION_BASE_URL", "https://api.qhose.net/v1").rstrip("/") + "/chat/completions",
                                 headers={"Authorization": "Bearer " + os.environ["CONVERSATION_API_KEY"]},
                                 json={"model": os.environ.get("CONVERSATION_MODEL", "deepseek-v4-pro-ga-260813"),
                                       "messages": [{"role": "user", "content": "Reply only OK"}], "max_tokens": 64,
                                       **({"thinking": {"type": "disabled"}} if os.environ.get("CONVERSATION_MODEL", "deepseek").lower().startswith("deepseek") else {})}, timeout=60)
        response.raise_for_status()
        if not response.json().get("choices", [{}])[0].get("message", {}).get("content"):
            raise ValueError("Conversation provider returned no content")
        conversation_checked = True
    PROBE_STAGE = "database_integrity"
    for filename in (root / "data/audit_index.sqlite3", root / "data/investigation_creation.sqlite3"):
        with sqlite3.connect(filename.as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Database integrity check failed")
    return {"status": "passed", "manifest_sha256": fingerprint(config), "commit": config["commit"],
            "crawler_commit": config["crawler_commit"], "node": node_version,
            "python": sys.version.split()[0], "hermes": hermes_cli.__version__, "cv2": cv2.__version__,
            "pydantic_core": pydantic_core.__version__, "dolphin_real_audio": "passed",
            "audit_provider": "passed", "resource_provider": "passed",
            "conversation_provider": "passed" if conversation_checked else "not_configured", "checked_at": time.time()}


def child(config, action, extra=()):
    return [config["python"], str(Path(config["repository"]) / "scripts/audit_runtime.py"),
            "--config", str(Path(config["runtime_root"]) / "runtime.json"), action, *extra]


def check(config):
    # API/worker/frontend supervisors can start together. Serialize the real
    # audio probe and atomic receipt so they never overwrite an in-use WAV.
    import fcntl
    verify_source(config)
    ensure_proxy(config)
    receipts = Path(config["runtime_root"]) / "receipts"
    receipts.mkdir(mode=0o700, exist_ok=True)
    with (receipts / "preflight.lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        return check_locked(config)


def check_locked(config):
    verify_source(config)
    result = subprocess.run(child(config, "_probe"), cwd=config["repository"], env=environment(config),
                            capture_output=True, text=True, timeout=300)
    if result.returncode:
        # Never forward provider bodies/tracebacks (may include secrets) to receipts.
        try:
            failure = json.loads(result.stdout.strip().splitlines()[-1])
            reason = str(failure.get("stage", "unknown")) + ": " + str(failure.get("error_type", "ProbeError"))
        except (ValueError, IndexError):
            reason = "ProbeProcessError"
        raise ValueError("Preflight failed closed: " + reason)
    receipt = json.loads(result.stdout.strip().splitlines()[-1])
    if receipt.get("status") != "passed" or receipt.get("manifest_sha256") != fingerprint(config):
        raise RuntimeError("Preflight receipt does not match manifest")
    path = Path(config["runtime_root"]) / "receipts/check-latest.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)
    return receipt


def start(config, service):
    check(config)  # Every supervisor restart also performs real preflight.
    os.chdir(config["repository"])
    command = child(config, "_serve", [service])
    # The isolated service entry keeps runtime identity and starts the selected role.
    os.execve(command[0], command, environment(config, frontend=service == "frontend"))


def serve(config, service):
    verify_source(config)
    frontend = service == "frontend"
    env = environment(config, frontend=frontend)
    cwd = Path(config["repository"])
    if frontend:
        cwd /= "Audit_assistant"
        command = [config["node"], str(cwd / "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1",
                   "--port", str(config["frontend_port"]), "--strictPort"]
    os.chdir(cwd)
    os.environ.clear()
    os.environ.update(env)
    sys.path[:0] = [config["repository"], config["repository"] + "/hermes_m0"]
    if frontend:
        os.execve(command[0], command, env)
        return
    identity = {"manifest_sha256": fingerprint(config), "commit": config["commit"],
                "environment_id": config["environment_id"], "pid": os.getpid()}
    receipt = Path(config["runtime_root"]) / "receipts" / (service + "-running.json")
    receipt.write_text(json.dumps(identity))
    receipt.chmod(0o600)
    if service == "worker":
        from backend.investigation_creation.worker import main as worker_main
        sys.argv = ["worker", "--workers", config["settings"].get("TASK_WORKER_PROCESSES", "1")]
        worker_main()
    else:
        import uvicorn
        from backend.main import app, principal_provider
        @app.get("/api/runtime-identity", include_in_schema=False)
        def runtime_identity():
            principal_provider()  # Same authenticated principal as product APIs.
            return identity
        uvicorn.run(app, host="127.0.0.1", port=config["api_port"], access_log=False)


def port_ready(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def process_snapshot(pid):
    result = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "stat=", "-o", "pgid=", "-o", "lstart=", "-o", "args="],
                            capture_output=True, text=True)
    if result.returncode or not result.stdout.strip():
        return None
    parts = result.stdout.strip().split(None, 7)
    if len(parts) != 8:
        raise ValueError("Cannot establish process identity")
    if parts[0].startswith("Z"):
        return None
    return {"pgid": int(parts[1]), "started": " ".join(parts[2:7]), "command": parts[7]}


def process_file(config, service):
    return Path(config["runtime_root"]) / "pids" / (service + ".json")


def owned_process(config, service):
    path = process_file(config, service)
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    if type(record.get("pid")) is not int or record["pid"] <= 1 or record.get("service") != service or record.get("runtime_root") != config["runtime_root"]:
        raise ValueError("Invalid process ownership record: " + service)
    current = process_snapshot(record["pid"])
    if current is None:
        return None
    if current != record.get("identity") or current["pgid"] != record["pid"]:
        raise ValueError("Refusing unrelated/reused process: " + service)
    command = current["command"]
    if service == "frontend":
        expected = str(Path(config["repository"]) / "Audit_assistant/node_modules/vite/bin/vite.js")
    elif service == "proxy":
        expected = config.get("managed_proxy", {}).get("config", "")
    else:
        expected = str(Path(config["runtime_root"]) / "runtime.json")
        if "_serve " + service not in command:
            raise ValueError("Refusing unexpected service command: " + service)
    if not expected or expected not in command:
        raise ValueError("Refusing unexpected service command: " + service)
    return record


def launch_owned(config, service, command, cwd, env, port=None):
    existing = owned_process(config, service)
    if existing:
        if existing.get("manifest_sha256") != fingerprint(config):
            raise ValueError("Running service uses another manifest; restart required: " + service)
        if port and not port_ready(port):
            raise ValueError("Owned process is running but its port is unavailable: " + service)
        return False
    if port and port_ready(port):
        raise ValueError("Port already belongs to another process: " + service)
    root = Path(config["runtime_root"])
    for directory in ("pids", "logs", "receipts"):
        (root / directory).mkdir(mode=0o700, exist_ok=True)
    log_path = root / "logs" / (service + ".log")
    with log_path.open("ab") as log:
        log_path.chmod(0o600)
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        deadline = time.monotonic() + (30 if port else 2)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ValueError("Service exited during startup; inspect private log: " + service)
            if port and port_ready(port):
                break
            time.sleep(0.1)
        if port and not port_ready(port):
            raise ValueError("Service port did not become ready: " + service)
        identity = process_snapshot(process.pid)
        if not identity or identity["pgid"] != process.pid:
            raise ValueError("Service process identity unavailable: " + service)
        record = {"pid": process.pid, "service": service, "runtime_root": config["runtime_root"],
                  "manifest_sha256": fingerprint(config), "identity": identity}
        path = process_file(config, service)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record))
        temporary.chmod(0o600)
        temporary.replace(path)
        return True
    except BaseException:
        # This process was just spawned by this call; never use a discovered PID.
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        raise


def stop_owned(config, service):
    record = owned_process(config, service)
    path = process_file(config, service)
    if record:
        pid = record["pid"]
        os.killpg(pid, signal.SIGTERM)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and process_snapshot(pid) is not None:
            time.sleep(0.1)
        # Revalidate birth time, command and process group before escalating.
        if process_snapshot(pid) is not None and owned_process(config, service):
            os.killpg(pid, signal.SIGKILL)
    path.unlink(missing_ok=True)


def ensure_proxy(config):
    # Individual service supervisors and the stack controller share this lock.
    import fcntl
    with (Path(config["runtime_root"]) / "proxy-controller.lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _ensure_proxy(config)


def _ensure_proxy(config):
    proxy = config.get("managed_proxy")
    if proxy:
        if not os.access(proxy["binary"], os.X_OK):
            raise ValueError("Missing managed proxy executable")
        path = Path(proxy["config"])
        if not path.is_file() or path.stat().st_mode & 0o077:
            raise ValueError("Managed proxy config must be private (mode 600)")
        launched = launch_owned(config, "proxy", [proxy["binary"], "-d", str(path.parent), "-f", str(path)],
                                config["runtime_root"], environment(config, frontend=True), proxy["port"])
    else:
        launched = False
    from urllib.parse import urlsplit
    try:
        for key in ("model_proxy_url", "crawler_proxy_url"):
            if config.get(key) and not port_ready(urlsplit(config[key]).port):
                raise ValueError("Configured proxy is unavailable: " + key)
    except BaseException:
        if launched:
            stop_owned(config, "proxy")
        raise


def inspect(config):
    processes = {}
    for service in ("proxy", *SERVICES):
        record = owned_process(config, service)
        processes[service] = {"running": bool(record), "pid": record["pid"] if record else None,
                              "manifest_matches": record.get("manifest_sha256") == fingerprint(config) if record else None}
    return {"status": "inspected", "environment_id": config["environment_id"], "runtime_root": config["runtime_root"],
            "commit": config["commit"], "crawler_commit": config["crawler_commit"], "processes": processes,
            "ports": {"api": port_ready(config["api_port"]), "frontend": port_ready(config["frontend_port"])},
            "model_proxy_url": config.get("model_proxy_url", ""), "crawler_proxy_url": config.get("crawler_proxy_url", ""),
            "models": {key: value for key, value in config["settings"].items() if key.endswith("_MODEL") or key.endswith("_BASE_URL")}}


def active_work(config):
    """Keep detached crawler sessions alive only with their owning worker."""
    import sqlite3
    counts = {}
    checks = {
        'investigation_creation.sqlite3': {'investigation_runs': ('QUEUED', 'RUNNING', 'REPORT_GENERATING')},
        'audit_index.sqlite3': {'jobs': ('queued', 'running', 'crawl_pausing', 'analysis_running', 'analysis_stopping')},
        'investigation.sqlite3': {'investigation_turns': ('running', 'pending', 'queued')},
    }
    for filename, tables in checks.items():
        path = Path(config['runtime_root']) / 'data' / filename
        if not path.is_file():
            continue
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, statuses in tables.items():
                if table not in existing:
                    continue
                count = db.execute('SELECT count(*) FROM ' + table + ' WHERE status IN (' + ','.join('?' for _ in statuses) + ')', statuses).fetchone()[0]
                if count:
                    counts[table] = count
    return counts


def lifecycle(config, action):
    import fcntl
    root = Path(config["runtime_root"])
    with (root / "controller.lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == "inspect":
            return inspect(config)
        if action in ("down", "restart"):
            if active_work(config):
                raise ValueError("Active work must finish or be paused in the application before stopping this runtime")
            # Validate all records before stopping anything.
            for service in (*SERVICES, "proxy"):
                owned_process(config, service)
            for service in (*reversed(SERVICES), "proxy"):
                stop_owned(config, service)
        if action == "down":
            return {"status": "stopped", "environment_id": config["environment_id"]}
        verify_source(config)
        started = []
        try:
            proxy_was_running = bool(owned_process(config, "proxy"))
            ensure_proxy(config)
            if config.get("managed_proxy") and not proxy_was_running:
                started.append("proxy")
            check(config)  # One real preflight for the complete stack.
            for service in SERVICES:
                port = config["api_port"] if service == "api" else config["frontend_port"] if service == "frontend" else None
                if launch_owned(config, service, child(config, "_serve", [service]), config["repository"],
                                environment(config, frontend=service == "frontend"), port):
                    started.append(service)
            return inspect(config)
        except BaseException:
            for service in reversed(started):
                stop_owned(config, service)
            raise


def status(config, cookie):
    import http.cookiejar
    import urllib.request
    cookies = http.cookiejar.MozillaCookieJar(cookie)
    cookies.load(ignore_discard=True)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    identities = []
    for port in (config["api_port"], config["frontend_port"]):
        with opener.open(f"http://127.0.0.1:{port}/api/runtime-identity", timeout=10) as response:
            identities.append(json.load(response))
    if identities[0] != identities[1] or identities[0].get("manifest_sha256") != fingerprint(config):
        raise ValueError("API/frontend running identity differs from manifest")
    worker = json.loads((Path(config["runtime_root"]) / "receipts/worker-running.json").read_text())
    command = subprocess.check_output(["ps", "-p", str(int(worker["pid"])), "-o", "args="], text=True)
    if worker.get("manifest_sha256") != fingerprint(config) or "_serve worker" not in command or config["runtime_root"] not in command:
        raise ValueError("Worker running identity differs from manifest")
    return {"status": "ready", "api": identities[0], "worker_pid": worker["pid"]}


def render_services(config, output):
    output.mkdir(parents=True, exist_ok=False)
    # Generate only. Never install, start, stop or overwrite another environment.
    for service in SERVICES:
        command = child(config, "start", [service])
        label = "local.xhs.audit-" + config["environment_id"] + "." + service
        logs = str(Path(config["runtime_root"]) / "logs" / (service + ".log"))
        plist = {"Label": label, "ProgramArguments": command, "WorkingDirectory": config["repository"],
                 "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 120,
                 "StandardOutPath": logs, "StandardErrorPath": logs}
        write_new(output / (label + ".plist"), plistlib.dumps(plist).decode())
        # systemd user units; escape specifiers and command-line special characters.
        def quote(value, *, command_value=True):
            value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
            return '"' + (value.replace("$", "$$") if command_value else value) + '"'
        unit = ("[Unit]\nDescription=Audit " + service + "\nAfter=network-online.target\n"
                "StartLimitIntervalSec=600\nStartLimitBurst=3\n\n[Service]\nType=simple\n"
                "WorkingDirectory=" + quote(config["repository"], command_value=False) + "\nExecStart=" + " ".join(map(quote, command)) +
                "\nRestart=on-failure\nRestartSec=120\nUMask=0077\n\n[Install]\nWantedBy=default.target\n")
        write_new(output / (label + ".service"), unit)
    return {"status": "rendered_not_installed", "directory": str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init").add_argument("--directory", required=True, type=Path)
    for name in ("check", "init-db", "_probe", "_init-db", "up", "down", "restart", "inspect"):
        sub.add_parser(name)
    sub.add_parser("start").add_argument("service", choices=SERVICES)
    sub.add_parser("_serve").add_argument("service", choices=SERVICES)
    sub.add_parser("status").add_argument("--cookie", required=True, type=Path)
    sub.add_parser("render-services").add_argument("--output", required=True, type=Path)
    sub.add_parser("run").add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "init":
        result = initialize(args.directory)
    else:
        if not args.config or args.config.name != "runtime.json":
            parser.error("--config must point to runtime.json")
        config = load_config(args.config)
        if args.action in ("up", "down", "restart", "inspect"):
            result = lifecycle(config, args.action)
        elif args.action == "_probe":
            result = probe(config)
        elif args.action == "_init-db":
            from backend.audit_agent.job_store import JobStore
            from backend.application_auth.store import AuthStore
            from backend.audit_agent.config import settings
            JobStore(settings.data_dir / "audit_index.sqlite3")
            AuthStore(settings.app_auth_db)
            result = {"status": "databases_initialized_no_user_created"}
        elif args.action == "check":
            result = check(config)
        elif args.action == "start":
            start(config, args.service)
            return
        elif args.action == "_serve":
            serve(config, args.service)
            return
        elif args.action == "status":
            result = status(config, args.cookie)
        elif args.action == "render-services":
            verify_source(config)
            result = render_services(config, args.output)
        elif args.action == "init-db":
            verify_source(config)
            root = Path(config["runtime_root"])
            if any((root / "data").glob("*.sqlite3")):
                raise ValueError("Existing databases found; init-db is only for empty runtimes")
            for folder in ("data", "outputs", "hermes", "logs", "receipts"):
                (root / folder).mkdir(mode=0o700, exist_ok=True)
            result = subprocess.run(child(config, "_init-db"), cwd=config["repository"], env=environment(config),
                                    capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError("Database initialization failed; check fixed Python dependencies")
            result = {"status": "databases_initialized_no_user_created"}
        else:
            if not args.args:
                parser.error("run requires Python arguments")
            check(config)
            os.chdir(config["repository"])
            os.execve(config["python"], [config["python"], *args.args], environment(config))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Validation messages describe keys/paths, never their secret values.
        error = {"status": "failed", "stage": PROBE_STAGE, "error_type": type(exc).__name__}
        if PROBE_STAGE == "configuration" and isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError):
            error["reason"] = str(exc)
        print(json.dumps(error, ensure_ascii=False))
        sys.exit(1)
