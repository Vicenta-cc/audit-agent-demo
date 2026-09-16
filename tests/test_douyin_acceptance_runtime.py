from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "scripts/douyin_acceptance_runtime.py"
SPEC = importlib.util.spec_from_file_location("douyin_acceptance_runtime", MODULE_PATH)
assert SPEC and SPEC.loader
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def config_for(tmp_path: Path) -> dict:
    root = tmp_path / "runtime"
    root.mkdir()
    paths = {
        "application_repository": tmp_path / "app-repo",
        "crawler_repository": tmp_path / "crawler-repo",
        "application_runtime": root / "app",
        "crawler_runtime": root / "crawler",
        "data_dir": root / "data",
        "outputs_dir": root / "outputs",
        "browser_profile_root": root / "browser-profiles",
        "frontend_dependencies": tmp_path / "node_modules",
    }
    for path in paths.values():
        path.mkdir(parents=True)
    app_python = tmp_path / "app-python"
    crawler_python = tmp_path / "crawler-python"
    node = tmp_path / "node"
    for path in (app_python, crawler_python, node):
        path.write_text("")
        path.chmod(0o755)
    return {
        "environment_id": "douyin-fixture",
        "runtime_root": str(root),
        **{key: str(value) for key, value in paths.items()},
        "application_commit": "a" * 40,
        "crawler_commit": "b" * 40,
        "application_python": str(app_python),
        "crawler_python": str(crawler_python),
        "node": str(node),
        "host": "127.0.0.1",
        "port": 8027,
        "protected_ports": [8017, 3198, 8198],
        "entrypoint_sha256": "fixture",
        "fingerprint_paths": {"application": ["sample.py"], "crawler": ["sample.py"]},
        "runtime_hashes": {"application": "fixture", "crawler": "fixture"},
    }


def test_clean_environment_drops_shell_pythonpath_and_uses_pinned_tools(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    monkeypatch.setenv("PYTHONPATH", "/polluted")
    monkeypatch.setenv("PATH", "/polluted")
    environment = runtime.clean_environment(config)
    assert "PYTHONPATH" not in environment
    assert "/polluted" not in environment["PATH"]
    assert str(Path(config["node"]).parent) in environment["PATH"].split(":")
    assert environment["DOUYIN_ACCEPTANCE_CONFIG"].endswith("runtime.json")


def test_executable_path_preserves_virtualenv_symlink(tmp_path):
    actual = tmp_path / "python3.12"
    actual.write_text("")
    venv_bin = tmp_path / ".venv/bin"
    venv_bin.mkdir(parents=True)
    launcher = venv_bin / "python"
    launcher.symlink_to(actual)
    assert runtime.executable_path(str(launcher)) == launcher.absolute()
    assert runtime.executable_path(str(launcher)) != actual.resolve()


def test_tree_hash_changes_with_content_and_ignores_pycache(tmp_path):
    root = tmp_path / "tree"
    (root / "pkg/__pycache__").mkdir(parents=True)
    source = root / "pkg/code.py"
    source.write_text("one")
    (root / "pkg/__pycache__/code.pyc").write_bytes(b"ignored")
    first = runtime.tree_hash(root, ["pkg"])
    (root / "pkg/__pycache__/code.pyc").write_bytes(b"still ignored")
    assert runtime.tree_hash(root, ["pkg"]) == first
    source.write_text("two")
    assert runtime.tree_hash(root, ["pkg"]) != first


def test_load_config_rejects_protected_port(tmp_path):
    config = config_for(tmp_path)
    config["port"] = 8017
    path = Path(config["runtime_root"]) / "runtime.json"
    path.write_text(json.dumps(config))
    with pytest.raises(runtime.RuntimeConfigurationError, match="protected"):
        runtime.load_config(path)


def test_load_config_rejects_paths_outside_runtime(tmp_path):
    config = config_for(tmp_path)
    config["data_dir"] = str(tmp_path / "other-data")
    path = Path(config["runtime_root"]) / "runtime.json"
    path.write_text(json.dumps(config))
    with pytest.raises(runtime.RuntimeConfigurationError, match="inside runtime_root"):
        runtime.load_config(path)
