"""Pinned macOS local audit environment: one configuration for API, worker and tools."""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import plistlib
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

from dotenv import dotenv_values


def environment(config, *, frontend=False):
    env = {"HOME": str(Path.home()), "LANG": "en_US.UTF-8", "PYTHONUNBUFFERED": "1"}
    env["PATH"] = ":".join([str(Path(config["python"]).parent), str(Path(config["node"]).parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    if frontend:
        env["VITE_API_PROXY_TARGET"] = f"http://127.0.0.1:{config['api_port']}"
        return env
    for filename in config["env_files"]:
        if not Path(filename).is_file():
            raise RuntimeError(f"Required env file missing: {filename}")
        env.update({key: value for key, value in dotenv_values(filename, interpolate=False).items() if value is not None})
    for source in config.get("json_env_sources", []):
        values = json.loads(Path(source["path"]).read_text())
        for key in source["keys"]:
            if not isinstance(values.get(key), str) or not values[key]:
                raise RuntimeError(f"Missing required credential: {key}")
            env[key] = values[key]
    # Never inherit shell Python or change the virtualenv launcher to its symlink target.
    env["PATH"] = ":".join([str(Path(config["python"]).parent), str(Path(config["node"]).parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = f"{config['repository']}:{config['repository']}/hermes_m0"
    return env


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)


def identity(config):
    return {key: config[key] for key in ("environment_id", "repository", "commit", "python", "api_port", "frontend_port")}


def verify_source(config):
    repo = Path(config["repository"])
    head = subprocess.check_output(["/usr/bin/git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    dirty = subprocess.check_output(["/usr/bin/git", "status", "--porcelain"], cwd=repo, text=True).strip()
    if head != config["commit"] or dirty:
        raise RuntimeError("Source differs from pinned clean commit; commit/review changes and update runtime.json before start")
    for key in ("python", "node"):
        if not Path(config[key]).is_absolute() or not os.access(config[key], os.X_OK):
            raise RuntimeError(f"Invalid fixed executable: {key}")


def probe(config):
    import cv2
    import hermes_cli
    import pydantic_core
    from backend.audit_agent.config import settings
    from backend.audit_agent.pipeline import AuditPipeline
    from backend.audit_agent.qwen_client import QwenClient
    from backend.audit_agent.video_processor import DemoAudioProcessor
    from backend.hermes_runtime import turn_worker
    import real_report_repository

    root = Path(config["runtime_root"])
    for name in ("data_dir", "outputs_dir", "crawler_browser_profile_root", "hermes_home"):
        if hasattr(settings, name):
            path = Path(getattr(settings, name)).resolve()
            if root != path and root not in path.parents:
                raise RuntimeError(f"{name} escaped local runtime")
    if Path(os.environ["HERMES_HOME"]).resolve() != root / "hermes":
        raise RuntimeError("Wrong Hermes session directory")
    if settings.asr_engine != "dolphin" or not settings.use_remote_asr:
        raise RuntimeError("Expected remote Dolphin ASR")
    if settings.asr_translate_enable_thinking:
        raise RuntimeError("ASR translation thinking must be false")
    from urllib.parse import urlsplit
    if urlsplit(settings.dashscope_base_url).hostname != config["audit_provider_host"]:
        raise RuntimeError("Wrong audit provider host")
    if urlsplit(settings.resource_generation_base_url).hostname != config["resource_provider_host"]:
        raise RuntimeError("Wrong resource-generation provider host")
    if not settings.dashscope_api_key or not settings.resource_generation_api_key:
        raise RuntimeError("Missing audit/resource-generation key")
    if settings.dashscope_api_key == settings.resource_generation_api_key:
        raise RuntimeError("Audit and resource keys unexpectedly identical")
    if str(settings.crawler_login_python) != config["crawler_python"]:
        raise RuntimeError("Crawler Python differs from pinned launcher")
    subprocess.run([config["crawler_python"], "-c", "import playwright, pydantic; from media_platform.douyin import DouYinCrawler"],
                   cwd=settings.media_crawler_dir, check=True, stdout=subprocess.DEVNULL)
    processor = DemoAudioProcessor()
    ffmpeg = processor._resolve_ffmpeg()
    if ffmpeg != config["ffmpeg"]:
        raise RuntimeError("ffmpeg resolution differs from pinned executable")
    import requests
    health = requests.get(processor.remote.asr_base_url + "/api/inference/health", headers=processor.remote._headers(), timeout=10)
    health.raise_for_status()
    if health.json().get("asr_engine") != "dolphin":
        raise RuntimeError("Remote ASR is not Dolphin")
    receipts = root / "receipts"
    receipts.mkdir(exist_ok=True)
    sample = receipts / "asr-preflight.wav"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", config["probe_video"], "-t", "3", "-vn", "-ar", "16000", "-ac", "1", "-y", str(sample)], check=True)
    transcript = AuditPipeline._validated_authoritative_transcript(processor.transcribe(sample))
    if (transcript.get("asr_engine") or transcript.get("provider")) != "dolphin":
        raise RuntimeError("Live audio probe did not use Dolphin")
    text_probe = QwenClient().audit_text('Return only JSON: {"ok":true}', max_tokens=128, enable_thinking=False)
    if text_probe.get("ok") is not True:
        raise RuntimeError("Audit text-provider preflight did not return valid JSON")
    for database in (settings.data_dir / "audit_index.sqlite3", settings.app_auth_db):
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Database quick_check failed")
    return {**identity(config), "status": "passed", "hermes_version": hermes_cli.__version__,
            "cv2": cv2.__version__, "pydantic_core": pydantic_core.__version__, "ffmpeg": ffmpeg,
            "dolphin_health": "passed", "dolphin_real_audio": "passed", "audit_text": "passed",
            "audit_provider_host": config["audit_provider_host"], "resource_provider_host": config["resource_provider_host"],
            "database": "ok", "checked_at": time.time()}


def check(config, config_path):
    verify_source(config)
    command = [config["python"], __file__, "--config", str(config_path), "_probe"]
    result = subprocess.run(command, cwd=config["repository"], env=environment(config), capture_output=True, text=True)
    if result.returncode:
        # Probe diagnostics have no request headers or key values.
        raise RuntimeError("Preflight failed: " + result.stderr[-2500:])
    receipt = json.loads(result.stdout)
    write_json(Path(config["runtime_root"]) / "receipts/preflight-latest.json", receipt)
    return receipt


def serve(config, service):
    verify_source(config)
    env = environment(config, frontend=service == "frontend")
    os.environ.clear()
    os.environ.update(env)
    os.chdir(config["repository"])
    sys.path[:0] = [config["repository"], config["repository"] + "/hermes_m0"]
    root = Path(config["runtime_root"])
    write_json(root / f"receipts/{service}-running.json", {**identity(config), "pid": os.getpid(), "started_at": time.time()})
    if service == "frontend":
        os.chdir(Path(config["repository"]) / "Audit_assistant")
        os.execve(config["node"], [config["node"], "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", str(config["frontend_port"]), "--strictPort"], env)
    if service == "worker":
        from backend.investigation_creation.worker import main
        sys.argv = ["worker", "--workers", "1"]
        main()
        return
    import uvicorn
    from backend.main import app
    running = {**identity(config), "pid": os.getpid()}
    @app.get("/api/local-runtime", include_in_schema=False)
    def runtime_identity():
        return running
    uvicorn.run(app, host="127.0.0.1", port=config["api_port"], access_log=False)


def occupied(port):
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def start_service(config, config_path, service):
    label = config["launch_label"] + "." + service
    domain = f"gui/{os.getuid()}"
    if subprocess.run(["launchctl", "print", domain + "/" + label], capture_output=True).returncode == 0:
        return
    if service == "frontend" and occupied(config["frontend_port"]):
        pid = subprocess.check_output(["lsof", "-tiTCP:" + str(config["frontend_port"]), "-sTCP:LISTEN"], text=True).strip()
        cwd = subprocess.check_output(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], text=True)
        if "n" + config["repository"] + "/Audit_assistant\n" not in cwd:
            raise RuntimeError("Frontend port belongs to another checkout")
        return
    if service == "api" and occupied(config["api_port"]):
        raise RuntimeError("Backend port belongs to an unmanaged process")
    root = Path(config["runtime_root"])
    (root / "logs").mkdir(exist_ok=True)
    plist = Path.home() / "Library/LaunchAgents" / (label + ".plist")
    payload = {"Label": label, "ProgramArguments": [config["python"], str(Path(__file__).resolve()), "--config", str(config_path), "_serve", service],
               "WorkingDirectory": config["repository"], "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30,
               "StandardOutPath": str(root / "logs" / (service + ".log")), "StandardErrorPath": str(root / "logs" / (service + ".log")),
               "EnvironmentVariables": {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONUNBUFFERED": "1"}}
    plist.write_bytes(plistlib.dumps(payload))
    plist.chmod(0o600)
    subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)


def status(config):
    cookies = http.cookiejar.MozillaCookieJar(config["auth_cookie_file"])
    cookies.load(ignore_discard=True)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    with opener.open(f"http://127.0.0.1:{config['api_port']}/api/local-runtime", timeout=5) as response:
        running = json.load(response)
    if any(running.get(k) != v for k, v in identity(config).items()):
        raise RuntimeError("Running API does not match pinned runtime")
    worker = json.loads((Path(config["runtime_root"]) / "receipts/worker-running.json").read_text())
    os.kill(worker["pid"], 0)
    if any(worker.get(k) != v for k, v in identity(config).items()):
        raise RuntimeError("Running worker does not match pinned runtime")
    with opener.open(f"http://127.0.0.1:{config['frontend_port']}/api/local-runtime", timeout=5) as response:
        if json.load(response) != running:
            raise RuntimeError("Frontend proxy does not reach this API")
    return {"status": "ready", "api": running, "worker_pid": worker["pid"], "frontend": f"http://127.0.0.1:{config['frontend_port']}/investigation"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("action", choices=["check", "start", "status", "run", "_probe", "_serve"])
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text())
    if config_path.parent != Path(config["runtime_root"]).resolve() or not config["launch_label"].startswith("local.xhs.audit-"):
        raise RuntimeError("Invalid local runtime scope")
    if {config["api_port"], config["frontend_port"]} != {8398, 3398}:
        raise RuntimeError("This controller is scoped to local 3398/8398")
    if args.action == "_probe":
        result = probe(config)
    elif args.action == "_serve":
        serve(config, args.args[0])
        return
    elif args.action == "check":
        result = check(config, config_path)
    elif args.action == "run":
        check(config, config_path)
        os.chdir(config["repository"])
        os.execve(config["python"], [config["python"], *args.args], environment(config))
    elif args.action == "status":
        result = status(config)
    else:
        check(config, config_path)
        for service in ("api", "worker", "frontend"):
            start_service(config, config_path, service)
        deadline = time.monotonic() + 35
        while True:
            try:
                result = status(config)
                break
            except (OSError, ValueError, RuntimeError):
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
