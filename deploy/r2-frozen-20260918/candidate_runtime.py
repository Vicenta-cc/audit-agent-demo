"""Control the isolated R2-ABC server runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request


class RuntimeConfigurationError(RuntimeError):
    pass


def absolute_path(value: str) -> Path:
    return Path(value).expanduser().absolute()


def resolved_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def load_config(path: Path) -> dict:
    try:
        config = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise RuntimeConfigurationError(f"Runtime config not found: {path}") from error
    required = {
        "environment_id", "runtime_root", "application_repository", "application_commit",
        "application_tag", "crawler_repository", "crawler_commit", "crawler_runtime",
        "crawler_fingerprint_paths", "formal_runtime_root", "data_dir", "outputs_dir",
        "browser_profile_root", "application_python", "crawler_python", "node",
        "frontend_root", "frontend_dependencies", "host", "frontend_port", "backend_port",
        "protected_ports", "entrypoints", "expected_hashes", "media_inventory",
    }
    missing = sorted(required - config.keys())
    if missing:
        raise RuntimeConfigurationError("Missing config keys: " + ", ".join(missing))
    runtime_root = resolved_path(config["runtime_root"])
    if path.resolve().parent != runtime_root:
        raise RuntimeConfigurationError("runtime.json must live directly under runtime_root")
    if config["host"] != "127.0.0.1":
        raise RuntimeConfigurationError("R2-ABC runtime must bind to 127.0.0.1")
    active_ports = {int(config["frontend_port"]), int(config["backend_port"])}
    if active_ports & {int(port) for port in config["protected_ports"]}:
        raise RuntimeConfigurationError("R2-ABC port overlaps a protected port")
    if active_ports != {3198, 8198}:
        raise RuntimeConfigurationError("This controller is restricted to 3198/8198")
    return config


def clean_environment(config: dict) -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in ("HOME", "TMPDIR", "LANG", "USER")
        if os.environ.get(key)
    }
    environment["PATH"] = os.pathsep.join((
        str(absolute_path(config["node"]).parent),
        str(absolute_path(config["application_python"]).parent),
        "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin",
    ))
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["DOUYIN_CANDIDATE_CONFIG"] = str(resolved_path(config["runtime_root"]) / "runtime.json")
    return environment


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_hash(root: Path, entries: list[str]) -> str:
    digest = hashlib.sha256()
    seen: set[Path] = set()
    for entry in entries:
        path = root / entry
        if path.is_file():
            candidates = [path]
        elif path.is_dir():
            candidates = sorted(candidate for candidate in path.rglob("*") if candidate.is_file())
        else:
            raise RuntimeConfigurationError(f"Fingerprint path is missing: {path}")
        for candidate in candidates:
            if "__pycache__" in candidate.parts or candidate.suffix in {".pyc", ".pyo"}:
                continue
            relative = candidate.relative_to(root)
            if relative in seen:
                continue
            seen.add(relative)
            digest.update(relative.as_posix().encode())
            digest.update(b"\0")
            digest.update(candidate.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def fingerprint(config: dict) -> dict:
    runtime_root = resolved_path(config["runtime_root"])
    entrypoints = {
        name: sha256_file(runtime_root / filename)
        for name, filename in config["entrypoints"].items()
    }
    crawler_runtime = resolved_path(config["crawler_runtime"])
    return {
        "entrypoints": entrypoints,
        "crawler_runtime": tree_hash(crawler_runtime, config["crawler_fingerprint_paths"]),
        "crawler_client": sha256_file(crawler_runtime / "media_platform/douyin/client.py"),
    }


def git_commit(repository: Path, revision: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", f"{revision}^{{commit}}"],
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    ).strip()


def require_path(path: Path, *, executable: bool = False) -> None:
    if not path.exists():
        raise RuntimeConfigurationError(f"Required path is missing: {path}")
    if executable and not os.access(path, os.X_OK):
        raise RuntimeConfigurationError(f"Required executable is not executable: {path}")


def build_receipt_path(config: dict) -> Path:
    return resolved_path(config["runtime_root"]) / "receipts/build-latest.json"


def check(config: dict, *, require_build: bool = True) -> dict:
    runtime_root = resolved_path(config["runtime_root"])
    application = resolved_path(config["application_repository"])
    crawler_repository = resolved_path(config["crawler_repository"])
    crawler_runtime = resolved_path(config["crawler_runtime"])
    data_dir = resolved_path(config["data_dir"])
    frontend_root = resolved_path(config["frontend_root"])
    dependencies = resolved_path(config["frontend_dependencies"])
    profile_root = resolved_path(config["browser_profile_root"])
    for path in (
        runtime_root, application, crawler_repository, crawler_runtime, data_dir,
        resolved_path(config["outputs_dir"]), frontend_root, dependencies, profile_root,
        resolved_path(config["formal_runtime_root"]),
    ):
        require_path(path)
    for value in (config["application_python"], config["crawler_python"], config["node"]):
        require_path(absolute_path(value), executable=True)
    for filename in config["entrypoints"].values():
        require_path(runtime_root / filename)
    require_path(data_dir / "audit_index.sqlite3")
    require_path(data_dir / "crawler_auth.key")

    media_manifest = runtime_root / "media-files.txt"
    require_path(media_manifest)
    media_files = 0
    media_bytes = 0
    for raw_path in media_manifest.read_text().splitlines():
        if not raw_path.strip():
            continue
        relative = Path(raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeConfigurationError(f"Unsafe media manifest path: {raw_path}")
        item = (data_dir / relative).resolve()
        if not item.is_relative_to(data_dir) or not item.is_file():
            raise RuntimeConfigurationError(f"Required report media is missing: {raw_path}")
        media_files += 1
        media_bytes += item.stat().st_size
    expected_media = config["media_inventory"]
    if media_files != int(expected_media["files"]) or media_bytes != int(expected_media["bytes"]):
        raise RuntimeConfigurationError(
            f"Report media inventory mismatch: files={media_files}, bytes={media_bytes}"
        )

    application_head = git_commit(application, "HEAD")
    if application_head != config["application_commit"]:
        raise RuntimeConfigurationError(f"Application HEAD is {application_head}, expected {config['application_commit']}")
    if git_commit(application, config["application_tag"]) != config["application_commit"]:
        raise RuntimeConfigurationError("R2-ABC tag does not resolve to application_commit")
    application_status = subprocess.check_output(
        ["git", "-C", str(application), "status", "--porcelain", "--untracked-files=all"],
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    ).strip()
    if application_status:
        raise RuntimeConfigurationError("R2-ABC application worktree is not clean")
    if git_commit(crawler_repository, "HEAD") != config["crawler_commit"]:
        raise RuntimeConfigurationError("Crawler repository HEAD does not match crawler_commit")
    if git_commit(crawler_repository, config["crawler_commit"]) != config["crawler_commit"]:
        raise RuntimeConfigurationError("crawler_commit is not an immutable full commit")

    actual_hashes = fingerprint(config)
    if actual_hashes != config["expected_hashes"]:
        raise RuntimeConfigurationError(
            "Pinned runtime source changed: "
            + json.dumps({"expected": config["expected_hashes"], "actual": actual_hashes})
        )

    with sqlite3.connect(f"file:{data_dir / 'audit_index.sqlite3'}?mode=ro", uri=True) as database:
        integrity = database.execute("pragma integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeConfigurationError(f"R2-ABC database integrity check failed: {integrity}")

    environment = clean_environment(config)
    subprocess.run(
        [
            config["application_python"],
            "-c",
            "import fastapi, uvicorn, pydantic, dotenv, hermes_cli; "
            "assert hermes_cli.__version__ == '0.20.4'",
        ],
        cwd=application,
        env=environment,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [config["crawler_python"], "-c", "from media_platform.douyin import DouYinCrawler"],
        cwd=crawler_runtime,
        env=environment,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    node_version = subprocess.check_output([config["node"], "--version"], text=True, env=environment).strip()

    result = {
        "status": "checked",
        "environment": config["environment_id"],
        "application_commit": application_head,
        "application_tag": config["application_tag"],
        "crawler_commit": config["crawler_commit"],
        "database": "ok",
        "media": {"files": media_files, "bytes": media_bytes},
        "profile_root": str(profile_root),
        "frontend_port": int(config["frontend_port"]),
        "backend_port": int(config["backend_port"]),
        "node": node_version,
        "hashes": actual_hashes,
    }
    if require_build:
        receipt_path = build_receipt_path(config)
        require_path(receipt_path)
        receipt = json.loads(receipt_path.read_text())
        dist = frontend_root / "dist"
        require_path(dist / "index.html")
        actual_dist_hash = tree_hash(dist, ["."])
        if receipt.get("application_commit") != application_head or receipt.get("dist_sha256") != actual_dist_hash:
            raise RuntimeConfigurationError("Frontend build receipt does not match R2-ABC commit/dist")
        result["frontend_dist_sha256"] = actual_dist_hash
    return result


def build(config: dict) -> dict:
    check(config, require_build=False)
    frontend = resolved_path(config["frontend_root"])
    dependencies = resolved_path(config["frontend_dependencies"])
    node = config["node"]
    environment = clean_environment(config)
    environment["VITE_API_PROXY_TARGET"] = f"http://{config['host']}:{int(config['backend_port'])}"
    for command in (
        [node, str(dependencies / "typescript/bin/tsc"), "--noEmit"],
        [node, str(dependencies / "vite/bin/vite.js"), "build"],
    ):
        subprocess.run(command, cwd=frontend, env=environment, check=True)
    receipt = {
        "status": "built",
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "application_commit": config["application_commit"],
        "application_tag": config["application_tag"],
        "dist_sha256": tree_hash(frontend / "dist", ["."]),
        "node": subprocess.check_output([node, "--version"], text=True, env=environment).strip(),
    }
    receipt_path = build_receipt_path(config)
    receipt_path.parent.mkdir(exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return receipt


def process_command(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def pid_path(config: dict, name: str) -> Path:
    return resolved_path(config["runtime_root"]) / f"logs/{name}.pid"


def managed_pid(config: dict, name: str) -> int:
    path = pid_path(config, name)
    if not path.exists():
        raise RuntimeConfigurationError(f"No managed {name} PID file")
    pid = int(path.read_text().strip())
    command = process_command(pid)
    entrypoint = str(resolved_path(config["runtime_root"]) / config["entrypoints"][name])
    if not command or entrypoint not in command:
        raise RuntimeConfigurationError(f"PID {pid} is not the managed {name} entrypoint")
    return pid


def port_available(host: str, port: int) -> bool:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def expected_identity(config: dict) -> dict:
    return {
        "environment": config["environment_id"],
        "app": str(resolved_path(config["application_repository"])),
        "data": str(resolved_path(config["data_dir"])),
        "port": int(config["backend_port"]),
        "auto_analysis": True,
    }


def status(config: dict) -> dict:
    checked = check(config)
    pids = {name: managed_pid(config, name) for name in ("backend", "worker", "frontend")}
    backend_url = f"http://{config['host']}:{int(config['backend_port'])}"
    frontend_url = f"http://{config['host']}:{int(config['frontend_port'])}"
    backend_identity = get_json(backend_url + "/api/acceptance-runtime")
    frontend_identity = get_json(frontend_url + "/api/acceptance-runtime")
    if backend_identity != expected_identity(config) or frontend_identity != backend_identity:
        raise RuntimeConfigurationError("Running R2-ABC identity does not match runtime.json")
    with urllib.request.urlopen(frontend_url + "/investigation", timeout=5) as response:
        index_prefix = response.read(256).decode("utf-8", errors="replace")
    if "<!doctype html>" not in index_prefix.lower():
        raise RuntimeConfigurationError("3198 /investigation is not serving the R2-ABC frontend")
    result = {
        "status": "ready",
        "frontend_url": frontend_url + "/investigation",
        "backend_url": backend_url,
        "identity": backend_identity,
        "pids": pids,
        "application_commit": checked["application_commit"],
        "application_tag": checked["application_tag"],
        "crawler_commit": checked["crawler_commit"],
        "frontend_dist_sha256": checked["frontend_dist_sha256"],
    }
    receipt = resolved_path(config["runtime_root"]) / "receipts/status-latest.json"
    receipt.parent.mkdir(exist_ok=True)
    receipt.write_text(json.dumps({**result, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, ensure_ascii=False, indent=2) + "\n")
    return result


def launch(config: dict, name: str) -> subprocess.Popen:
    runtime_root = resolved_path(config["runtime_root"])
    logs = runtime_root / "logs"
    logs.mkdir(exist_ok=True)
    log_handle = (logs / f"{name}.log").open("a")
    process = subprocess.Popen(
        [config["application_python"], str(runtime_root / config["entrypoints"][name])],
        cwd=runtime_root,
        env=clean_environment(config),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_handle.close()
    pid_path(config, name).write_text(str(process.pid) + "\n")
    return process


def start(config: dict) -> dict:
    check(config)
    existing = [name for name in ("backend", "worker", "frontend") if pid_path(config, name).exists()]
    if existing:
        return status(config)
    for port in (int(config["frontend_port"]), int(config["backend_port"])):
        if not port_available(config["host"], port):
            raise RuntimeConfigurationError(f"Port {port} is occupied by an unmanaged process")

    processes: dict[str, subprocess.Popen] = {}
    try:
        processes["backend"] = launch(config, "backend")
        backend_url = f"http://{config['host']}:{int(config['backend_port'])}/api/acceptance-runtime"
        deadline = time.monotonic() + 40
        while True:
            if processes["backend"].poll() is not None:
                raise RuntimeError("Backend exited during startup; inspect logs/backend.log")
            try:
                if get_json(backend_url) == expected_identity(config):
                    break
            except (OSError, urllib.error.URLError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Backend did not become ready; inspect logs/backend.log")
            time.sleep(0.25)

        processes["worker"] = launch(config, "worker")
        time.sleep(1)
        if processes["worker"].poll() is not None:
            raise RuntimeError("Worker exited during startup; inspect logs/worker.log")

        processes["frontend"] = launch(config, "frontend")
        frontend_url = f"http://{config['host']}:{int(config['frontend_port'])}/investigation"
        deadline = time.monotonic() + 20
        while True:
            if processes["frontend"].poll() is not None:
                raise RuntimeError("Frontend exited during startup; inspect logs/frontend.log")
            try:
                with urllib.request.urlopen(frontend_url, timeout=3) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("Frontend did not become ready; inspect logs/frontend.log")
            time.sleep(0.25)
        return status(config)
    except Exception:
        for name, process in reversed(processes.items()):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            pid_path(config, name).unlink(missing_ok=True)
        raise


def stop_one(config: dict, name: str) -> None:
    path = pid_path(config, name)
    if not path.exists():
        return
    pid = managed_pid(config, name)
    os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + 20
    while process_command(pid):
        if time.monotonic() >= deadline:
            os.killpg(pid, signal.SIGKILL)
            break
        time.sleep(0.2)
    path.unlink(missing_ok=True)


def stop(config: dict) -> dict:
    for name in ("frontend", "worker", "backend"):
        stop_one(config, name)
    return {"status": "stopped"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("action", choices=("fingerprint", "check", "build", "status", "start", "stop", "restart"))
    args = parser.parse_args()
    config = load_config(args.config.resolve())
    if args.action == "fingerprint":
        result = fingerprint(config)
    elif args.action == "check":
        result = check(config)
    elif args.action == "build":
        result = build(config)
    elif args.action == "status":
        result = status(config)
    elif args.action == "start":
        result = start(config)
    elif args.action == "stop":
        result = stop(config)
    else:
        stop(config)
        result = start(config)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeConfigurationError, RuntimeError, subprocess.CalledProcessError, urllib.error.URLError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
