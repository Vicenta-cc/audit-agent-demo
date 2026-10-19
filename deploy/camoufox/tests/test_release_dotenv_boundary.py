"""Run release entrypoints against a hostile checkout .env without starting services."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_control import manifest
from control import digest


DEPLOY = Path(__file__).resolve().parents[1]
APP = DEPLOY.parents[1]


class ReleaseDotenvBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = manifest(self.root)
        self.config["ports"].update(frontend=13299, allowed=[18298, 13299])
        self.app = self.root / "app"
        package = self.app / "backend/audit_agent"
        package.mkdir(parents=True)
        for name in ("config.py", "limits.py", "triage.py", "triage_matcher.py"):
            shutil.copyfile(APP / "backend/audit_agent" / name, package / name)
        (self.app / "backend/__init__.py").touch()
        (package / "__init__.py").touch()
        (self.app / ".env").write_text(
            "APP_AUTH_MODE=disabled\nSEARCH_TERMS_MAX=2\n"
            "RESOURCE_GENERATION_MODEL=unexpected-checkout-model\n"
            "REMOTE_ASR_BASE_URL=http://unexpected-checkout.invalid\n"
            "FFMPEG_PATH=/unexpected/checkout/ffmpeg\n"
        )
        self.path = self.root / "runtime.json"

    def run_entrypoint(self, entrypoint, private=None, *, restricted_child=False):
        if private:
            path = self.root / "private.json"
            path.write_text(json.dumps(private))
            path.chmod(0o600)
            self.config["parent_env_files"] = [{
                "path": str(path), "sha256": digest(path),
                "expected_uid": os.geteuid(), "reviewed_allowlist": list(private),
            }]
        self.path.write_text(json.dumps(self.config))
        # Stub only service loops and asset verification: this is not a Linux
        # artifact/sandbox receipt. Manifest validation and Settings are real.
        code = """
import json, os, runpy, sys, types
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import control
def inspect_settings(*args, **kwargs):
    from backend.audit_agent.config import Settings
    print(json.dumps({
        'auth': Settings.app_auth_mode,
        'search_terms': Settings.search_terms_max,
        'resource_model': os.getenv('RESOURCE_GENERATION_MODEL'),
        'asr': os.getenv('REMOTE_ASR_BASE_URL'),
        'ffmpeg': os.getenv('FFMPEG_PATH'),
    }))
entry = sys.argv[2]
if entry == 'legacy':
    sys.path.insert(0, sys.argv[3])
    inspect_settings()
else:
    sys.path.insert(0, sys.argv[1] + '/runtime-support')
    if entry == 'serve':
        uvicorn = types.ModuleType('uvicorn')
        uvicorn.run = inspect_settings
        sys.modules['uvicorn'] = uvicorn
    else:
        worker = types.ModuleType('backend.investigation_creation.worker')
        worker.build_worker = lambda: types.SimpleNamespace(run_forever=inspect_settings)
        sys.modules['backend.investigation_creation.worker'] = worker
    with patch.object(control, 'check_artifacts'):
        runpy.run_path(sys.argv[1] + '/runtime-support/' + entry + '.py', run_name='__main__')
"""
        values = {
            "PATH": os.defpath,
            "CAMOUFOX_RELEASE_MANIFEST": str(self.path),
            # Deliberately no PYTHON_DOTENV_DISABLED: even a dotenv version
            # without that feature must not get called by managed App Settings.
        }
        if entrypoint == "legacy":
            values.pop("CAMOUFOX_RELEASE_MANIFEST")
        if restricted_child:
            values["CRAWLER_RESTRICTED_CHILD"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", code, str(DEPLOY), entrypoint, str(self.app)],
            cwd=self.root, env=values, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_api_does_not_fill_missing_settings_from_checkout(self):
        actual = self.run_entrypoint("serve")
        self.assertEqual(actual, {
            "auth": "required", "search_terms": 10,
            "resource_model": None, "asr": None, "ffmpeg": None,
        })

    def test_worker_does_not_fill_missing_settings_from_checkout(self):
        actual = self.run_entrypoint("worker")
        self.assertEqual(actual, {
            "auth": "required", "search_terms": 10,
            "resource_model": None, "asr": None, "ffmpeg": None,
        })

    def test_reviewed_business_configuration_survives_both_entrypoints(self):
        for entrypoint in ("serve", "worker"):
            with self.subTest(entrypoint=entrypoint):
                actual = self.run_entrypoint(entrypoint, {
                    "APP_AUTH_MODE": "required", "SEARCH_TERMS_MAX": "7",
                    "RESOURCE_GENERATION_MODEL": "reviewed-test-model",
                    "REMOTE_ASR_BASE_URL": "http://reviewed-asr.invalid",
                    "FFMPEG_PATH": "/reviewed/ffmpeg",
                })
                self.assertEqual(actual, {
                    "auth": "required", "search_terms": 7,
                    "resource_model": "reviewed-test-model",
                    "asr": "http://reviewed-asr.invalid", "ffmpeg": "/reviewed/ffmpeg",
                })

    def test_unmanaged_development_still_supports_checkout_dotenv(self):
        self.assertEqual(self.run_entrypoint("legacy")["search_terms"], 2)

    def test_restricted_child_still_rejects_checkout_dotenv(self):
        actual = self.run_entrypoint("legacy", restricted_child=True)
        self.assertEqual(actual, {
            "auth": "required", "search_terms": 10,
            "resource_model": None, "asr": None, "ffmpeg": None,
        })


if __name__ == "__main__":
    unittest.main()
