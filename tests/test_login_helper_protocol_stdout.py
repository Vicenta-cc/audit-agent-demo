"""First-use SDK configuration diagnostics must not enter the login control pipe."""

import json
import os
from pathlib import Path
import subprocess

import pytest


def test_first_profile_sdk_warnings_leave_login_and_route_protocol_intact():
    python = os.environ.get("LOGIN_BROWSER_TEST_PYTHON")
    if not python or not Path(python).is_file():
        pytest.skip("login browser interpreter is not installed")
    root = Path(__file__).resolve().parents[1]
    script = """
from types import SimpleNamespace
from camoufox import utils
from scripts.crawler_douyin_login_v2 import DouyinLoginV2, _isolate_camoufox_diagnostics
from tools.bound_proxy import BoundProxySession

utils._load_properties = lambda **kwargs: {'old': {'removed': '1'}}
_isolate_camoufox_diagnostics()
utils.validate_config({'old': False, 'unknown': 'private-configuration-value'})
DouyinLoginV2.emit(SimpleNamespace(operation_id='op'), 'checking')
snapshot = SimpleNamespace(proxy_id='direct', config_revision=1, account_id='a', managed=False)
BoundProxySession._event(SimpleNamespace(snapshot=snapshot), 'ROUTE_OBSERVED')
"""
    crawler = os.environ.get("MEDIACRAWLER_DIR")
    if not crawler:
        pytest.skip("paired crawler is not configured")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root), crawler))}
    result = subprocess.run([python, "-c", script], cwd=root, env=env,
                            capture_output=True, text=True, timeout=30, check=True)
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "checking"
    prefix = "COLLECTION_ROUTE_EVENT "
    assert lines[1].startswith(prefix)
    assert json.loads(lines[1][len(prefix):])["code"] == "ROUTE_OBSERVED"
    assert result.stderr.count("Camoufox configuration diagnostic") == 2
    assert "private-configuration-value" not in result.stderr
