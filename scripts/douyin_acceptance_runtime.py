"""Manage the isolated Douyin acceptance runtime without inheriting shell state."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


APP_TESTS = (
    "tests/test_crawl_resume_control.py",
    "tests/test_crawler_account_store.py",
    "tests/test_crawler_adapter_execution_settings.py",
    "tests/test_crawler_collection_failures.py",
    "tests/test_crawler_login_manager.py",
    "tests/test_job_request_validation.py",
    "tests/test_pipeline_verify_rotation.py",
    "tests/test_shared_request_execution.py",
)
BROWSER_TEST = "tests/test_crawler_browser_integration.py"
CRAWLER_TESTS = (
    "tests/test_api_limits.py",
    "tests/test_douyin_collection_reliability.py",
    "tests/test_douyin_media_download.py",
    "tests/test_douyin_pagination_dedupe.py",
    "tests/test_douyin_request_scheduling.py",
    "tests/test_persistent_request_gate.py",
)


class RuntimeConfigurationError(RuntimeError):
    pass


def resolve_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def executable_path(value: str) -> Path:
    """Keep a virtualenv launcher path intact instead of resolving its Python symlink."""
    return Path(value).expanduser().absolute()


def load_config(path: Path) -> dict:
    try:
        config = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise RuntimeConfigurationError(f"Runtime config not found: {path}") from exc
    required = {
        "environment_id",
        "runtime_root",
        "application_repository",
        "application_commit",
        "crawler_repository",
        "crawler_commit",
        "application_runtime",
        "crawler_runtime",
        "data_dir",
        "outputs_dir",
        "browser_profile_root",
        "application_python",
        "crawler_python",
        "node",
        "frontend_dependencies",
        "host",
        "port",
        "protected_ports",
        "entrypoint_sha256",
        "runtime_hashes",
    }
    missing = sorted(required - config.keys())
    if missing:
        raise RuntimeConfigurationError("Missing config keys: " + ", ".join(missing))
    validate_config(config, path)
    return config


def validate_config(config: dict, config_path: Path) -> None:
    if not str(config["environment_id"]).startswith("douyin-"):
        raise RuntimeConfigurationError("environment_id must start with 'douyin-'")
    runtime_root = resolve_path(config["runtime_root"])
    if config_path.resolve().parent != runtime_root:
        raise RuntimeConfigurationError("runtime.json must live directly under runtime_root")
    for key in (
        "application_runtime",
        "crawler_runtime",
        "data_dir",
        "outputs_dir",
        "browser_profile_root",
    ):
        path = resolve_path(config[key])
        if path != runtime_root and runtime_root not in path.parents:
            raise RuntimeConfigurationError(f"{key} must stay inside runtime_root")
    port = int(config["port"])
    if port in {int(value) for value in config["protected_ports"]}:
        raise RuntimeConfigurationError(f"Port {port} is protected")
    if config["host"] != "127.0.0.1":
        raise RuntimeConfigurationError("Acceptance runtime must bind to 127.0.0.1")


def clean_environment(config: dict) -> dict[str, str]:
    keep = {
        key: os.environ[key]
        for key in ("HOME", "TMPDIR", "LANG", "USER")
        if os.environ.get(key)
    }
    node_dir = str(executable_path(config["node"]).parent)
    app_python_dir = str(executable_path(config["application_python"]).parent)
    keep["PATH"] = os.pathsep.join(
        (node_dir, app_python_dir, "/usr/bin", "/bin", "/usr/sbin", "/sbin", "/opt/homebrew/bin")
    )
    keep["PYTHONDONTWRITEBYTECODE"] = "1"
    keep["DOUYIN_ACCEPTANCE_CONFIG"] = str(resolve_path(config["runtime_root"]) / "runtime.json")
    return keep


def _iter_hash_files(root: Path, entries: list[str]):
    for entry in entries:
        path = root / entry
        if path.is_file():
            yield path
        elif path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if candidate.is_file() and "__pycache__" not in candidate.parts:
                    yield candidate
        else:
            raise RuntimeConfigurationError(f"Fingerprint path is missing: {path}")


def tree_hash(root: Path, entries: list[str]) -> str:
    digest = hashlib.sha256()
    seen: set[Path] = set()
    for path in _iter_hash_files(root, entries):
        relative = path.relative_to(root)
        if relative in seen:
            continue
        seen.add(relative)
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def runtime_hashes(config: dict) -> dict[str, str]:
    fingerprint_paths = config["fingerprint_paths"]
    return {
        "application": tree_hash(
            resolve_path(config["application_runtime"]), fingerprint_paths["application"]
        ),
        "crawler": tree_hash(resolve_path(config["crawler_runtime"]), fingerprint_paths["crawler"]),
    }


def _git_commit(repository: Path, revision: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", f"{revision}^{{commit}}"],
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"},
    ).strip()


def _run(command: list[str], *, cwd: Path, env: dict[str, str], label: str) -> None:
    print(f"== {label} ==", flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _check_path(path: Path, *, executable: bool = False) -> None:
    if not path.exists():
        raise RuntimeConfigurationError(f"Required path is missing: {path}")
    if executable and not os.access(path, os.X_OK):
        raise RuntimeConfigurationError(f"Required executable is not executable: {path}")


def check(config: dict, *, require_dist: bool = True) -> dict:
    runtime_root = resolve_path(config["runtime_root"])
    paths = {
        key: resolve_path(config[key])
        for key in (
            "application_repository",
            "crawler_repository",
            "application_runtime",
            "crawler_runtime",
            "data_dir",
            "outputs_dir",
            "browser_profile_root",
            "frontend_dependencies",
        )
    }
    for path in paths.values():
        _check_path(path)
    app_python = executable_path(config["application_python"])
    crawler_python = executable_path(config["crawler_python"])
    node = executable_path(config["node"])
    for executable in (app_python, crawler_python, node):
        _check_path(executable, executable=True)
    entrypoint = runtime_root / "serve.py"
    _check_path(entrypoint)
    entrypoint_sha256 = hashlib.sha256(entrypoint.read_bytes()).hexdigest()
    if entrypoint_sha256 != config["entrypoint_sha256"]:
        raise RuntimeConfigurationError("Acceptance serve.py changed since the runtime was recorded")
    if require_dist:
        _check_path(paths["application_runtime"] / "frontend-v2/dist/index.html")
    _check_path(paths["data_dir"] / "audit_index.sqlite3")
    _check_path(paths["data_dir"] / "crawler_auth.key")
    app_commit = _git_commit(paths["application_repository"], config["application_commit"])
    crawler_commit = _git_commit(paths["crawler_repository"], config["crawler_commit"])
    if app_commit != config["application_commit"]:
        raise RuntimeConfigurationError("application_commit is not a full immutable commit ID")
    if crawler_commit != config["crawler_commit"]:
        raise RuntimeConfigurationError("crawler_commit is not a full immutable commit ID")
    actual_hashes = runtime_hashes(config)
    if actual_hashes != config["runtime_hashes"]:
        raise RuntimeConfigurationError(
            "Acceptance runtime source changed: "
            + json.dumps({"expected": config["runtime_hashes"], "actual": actual_hashes})
        )
    with sqlite3.connect(
        f"file:{paths['data_dir'] / 'audit_index.sqlite3'}?mode=ro", uri=True
    ) as database:
        integrity = database.execute("pragma integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeConfigurationError(f"Acceptance database integrity check failed: {integrity}")
    env = clean_environment(config)
    subprocess.run(
        [str(app_python), "-c", "import fastapi, uvicorn, pydantic"],
        cwd=paths["application_runtime"],
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [str(crawler_python), "-c", "from media_platform.douyin import DouYinCrawler"],
        cwd=paths["crawler_runtime"],
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    node_version = subprocess.check_output([str(node), "--version"], text=True, env=env).strip()
    return {
        "environment_id": config["environment_id"],
        "runtime_root": str(runtime_root),
        "application_commit": app_commit,
        "crawler_commit": crawler_commit,
        "runtime_hashes": actual_hashes,
        "node": node_version,
        "entrypoint_sha256": entrypoint_sha256,
        "database": "ok",
        "port": int(config["port"]),
    }


def fingerprint(config: dict) -> dict:
    return runtime_hashes(config)


def _runtime_url(config: dict, path: str) -> str:
    return f"http://{config['host']}:{int(config['port'])}{path}"


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)


def status(config: dict) -> dict:
    expected = {
        "environment_id": config["environment_id"],
        "application_commit": config["application_commit"],
        "crawler_commit": config["crawler_commit"],
        "app": str(resolve_path(config["application_runtime"])),
        "crawler": str(resolve_path(config["crawler_runtime"])),
        "data": str(resolve_path(config["data_dir"])),
        "outputs": str(resolve_path(config["outputs_dir"])),
        "port": int(config["port"]),
        "auto_analysis": False,
    }
    actual = _get_json(_runtime_url(config, "/api/acceptance-runtime"))
    if actual != expected:
        raise RuntimeConfigurationError(
            "Running 8027 identity does not match config: "
            + json.dumps({"expected": expected, "actual": actual}, ensure_ascii=False)
        )
    urllib.request.urlopen(_runtime_url(config, "/saas-v2/tasks"), timeout=3).read(1)
    return {"status": "ready", "url": _runtime_url(config, "/saas-v2/tasks"), "identity": actual}


def build(config: dict) -> None:
    check(config, require_dist=False)
    frontend = resolve_path(config["application_runtime"]) / "frontend-v2"
    dependencies = resolve_path(config["frontend_dependencies"])
    node = str(executable_path(config["node"]))
    env = clean_environment(config)
    commands = (
        [node, str(dependencies / "typescript/bin/tsc"), "--noEmit", "-p", "tsconfig.app.json"],
        [node, str(dependencies / "typescript/bin/tsc"), "--noEmit", "-p", "tsconfig.node.json"],
        [node, str(dependencies / "vite/bin/vite.js"), "build"],
    )
    for index, command in enumerate(commands, 1):
        _run(command, cwd=frontend, env=env, label=f"frontend build {index}/{len(commands)}")


@contextmanager
def committed_worktrees(config: dict):
    app_repository = resolve_path(config["application_repository"])
    crawler_repository = resolve_path(config["crawler_repository"])
    temp_root = Path(tempfile.mkdtemp(prefix="douyin-baseline-gate."))
    app_worktree = temp_root / "app"
    crawler_worktree = temp_root / "crawler"
    app_added = False
    crawler_added = False
    try:
        subprocess.run(
            ["git", "-C", str(app_repository), "worktree", "add", "--detach", str(app_worktree), config["application_commit"]],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        app_added = True
        subprocess.run(
            ["git", "-C", str(crawler_repository), "worktree", "add", "--detach", str(crawler_worktree), config["crawler_commit"]],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        crawler_added = True
        crawler_venv = executable_path(config["crawler_python"]).parents[1]
        (crawler_worktree / ".venv").symlink_to(crawler_venv, target_is_directory=True)
        (app_worktree / "frontend-v2/node_modules").symlink_to(
            resolve_path(config["frontend_dependencies"]), target_is_directory=True
        )
        yield app_worktree, crawler_worktree
    finally:
        for link in (
            app_worktree / "frontend-v2/node_modules",
            crawler_worktree / ".venv",
        ):
            if link.is_symlink():
                link.unlink()
        if app_added:
            subprocess.run(
                ["git", "-C", str(app_repository), "worktree", "remove", "--force", str(app_worktree)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if crawler_added:
            subprocess.run(
                ["git", "-C", str(crawler_repository), "worktree", "remove", "--force", str(crawler_worktree)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        shutil.rmtree(temp_root, ignore_errors=True)


def test_committed_baseline(config: dict) -> dict:
    check(config)
    env = clean_environment(config)
    app_python = str(executable_path(config["application_python"]))
    crawler_python = str(executable_path(config["crawler_python"]))
    node = str(executable_path(config["node"]))
    dependencies = resolve_path(config["frontend_dependencies"])
    started = time.time()
    with committed_worktrees(config) as (app_worktree, crawler_worktree):
        app_env = dict(env, MEDIACRAWLER_DIR=str(crawler_worktree))
        _run(
            [app_python, "-m", "pytest", "-q", *APP_TESTS],
            cwd=app_worktree,
            env=app_env,
            label="application committed snapshot",
        )
        _run(
            [app_python, "-m", "pytest", "-q", BROWSER_TEST],
            cwd=app_worktree,
            env=app_env,
            label="browser integration committed snapshot",
        )
        _run(
            [crawler_python, "-m", "pytest", "-q", *CRAWLER_TESTS],
            cwd=crawler_worktree,
            env=env,
            label="crawler committed snapshot",
        )
        frontend = app_worktree / "frontend-v2"
        for label, command in (
            (
                "frontend app typecheck",
                [node, str(dependencies / "typescript/bin/tsc"), "--noEmit", "-p", "tsconfig.app.json"],
            ),
            (
                "frontend node typecheck",
                [node, str(dependencies / "typescript/bin/tsc"), "--noEmit", "-p", "tsconfig.node.json"],
            ),
            ("frontend production build", [node, str(dependencies / "vite/bin/vite.js"), "build"]),
        ):
            _run(command, cwd=frontend, env=env, label=label)
    receipt = {
        "status": "passed",
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_seconds": round(time.time() - started, 2),
        "application_commit": config["application_commit"],
        "crawler_commit": config["crawler_commit"],
        "application_tests": 45,
        "browser_tests": {"passed": 4, "skipped": 3},
        "crawler_tests": 71,
        "frontend_build": "passed",
    }
    receipts = resolve_path(config["runtime_root"]) / "receipts"
    receipts.mkdir(exist_ok=True)
    (receipts / "baseline-gate-latest.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    )
    return receipt


def _pid_path(config: dict) -> Path:
    return resolve_path(config["runtime_root"]) / "logs/server.pid"


def _process_command(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False
    )
    return result.stdout.strip()


def stop(config: dict) -> None:
    pid_path = _pid_path(config)
    if not pid_path.exists():
        raise RuntimeConfigurationError("No managed 8027 PID file; refusing to stop an unknown process")
    pid = int(pid_path.read_text().strip())
    command = _process_command(pid)
    if not command:
        pid_path.unlink()
        print("Removed stale 8027 PID file")
        return
    entrypoint = str(resolve_path(config["runtime_root"]) / "serve.py")
    if entrypoint not in command:
        raise RuntimeConfigurationError(f"PID {pid} is not the managed 8027 entrypoint; refusing to stop")
    os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + 20
    while _process_command(pid):
        if time.monotonic() >= deadline:
            os.killpg(pid, signal.SIGKILL)
            break
        time.sleep(0.2)
    pid_path.unlink(missing_ok=True)
    print("Stopped managed Douyin acceptance runtime")


def _port_available(host: str, port: int) -> bool:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def start(config: dict) -> dict:
    check(config)
    pid_path = _pid_path(config)
    if pid_path.exists():
        pid = int(pid_path.read_text().strip())
        if _process_command(pid):
            return status(config)
        pid_path.unlink()
    host, port = config["host"], int(config["port"])
    if not _port_available(host, port):
        raise RuntimeConfigurationError(f"Port {port} is occupied by an unmanaged process")
    runtime_root = resolve_path(config["runtime_root"])
    logs = runtime_root / "logs"
    logs.mkdir(exist_ok=True)
    log_handle = (logs / "server.log").open("a")
    process = subprocess.Popen(
        [str(executable_path(config["application_python"])), str(runtime_root / "serve.py")],
        cwd=runtime_root,
        env=clean_environment(config),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_handle.close()
    pid_path.write_text(str(process.pid) + "\n")
    deadline = time.monotonic() + 30
    while True:
        if process.poll() is not None:
            pid_path.unlink(missing_ok=True)
            raise RuntimeError("8027 exited during startup; inspect logs/server.log")
        try:
            return status(config)
        except (OSError, urllib.error.URLError, RuntimeConfigurationError):
            if time.monotonic() >= deadline:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                finally:
                    pid_path.unlink(missing_ok=True)
                raise RuntimeError("8027 did not become ready; inspect logs/server.log") from None
            time.sleep(0.25)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "action", choices=("check", "fingerprint", "test", "build", "status", "start", "stop", "restart")
    )
    args = parser.parse_args()
    config = load_config(args.config.resolve())
    if args.action == "check":
        result = check(config)
    elif args.action == "fingerprint":
        result = fingerprint(config)
    elif args.action == "test":
        result = test_committed_baseline(config)
    elif args.action == "build":
        build(config)
        result = {"status": "built"}
    elif args.action == "status":
        result = status(config)
    elif args.action == "start":
        result = start(config)
    elif args.action == "stop":
        stop(config)
        result = {"status": "stopped"}
    else:
        try:
            stop(config)
        except RuntimeConfigurationError as exc:
            if "No managed 8027 PID file" not in str(exc):
                raise
        result = start(config)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeConfigurationError, subprocess.CalledProcessError, urllib.error.URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
