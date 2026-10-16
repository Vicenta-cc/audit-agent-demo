"""Actual App Settings and release entrypoints share the V2 deployment contract."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import control
from control import (
    InvalidRelease,
    environment,
    owner_preflight,
    validate,
    validate_login_v2_origins,
)
from test_control import manifest


class LoginV2ReleaseContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = manifest(self.root)
        self.config["ports"].update(frontend=13299, allowed=[18298, 13299])

    def tearDown(self):
        self.tmp.cleanup()

    def private_origins(self, value):
        path = self.root / "private.json"
        raw = json.dumps({"CRAWLER_LOGIN_V2_ORIGINS": value}).encode()
        path.write_bytes(raw)
        path.chmod(0o600)
        self.config["environment"].pop("CRAWLER_LOGIN_V2_ORIGINS")
        self.config["parent_env_files"] = [
            {
                "path": str(path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "expected_uid": os.geteuid(),
                "reviewed_allowlist": ["CRAWLER_LOGIN_V2_ORIGINS"],
            }
        ]
        return path

    def test_v2_always_enabled_and_overlay_override_forbidden(self):
        self.assertEqual(
            environment(validate(self.config))["CRAWLER_LOGIN_V2_ENABLED"], "true"
        )
        self.config["environment"]["CRAWLER_LOGIN_V2_ENABLED"] = "false"
        with self.assertRaises(InvalidRelease):
            validate(self.config)
        with self.assertRaises(InvalidRelease):
            environment(self.config)
        self.config["environment"].pop("CRAWLER_LOGIN_V2_ENABLED")
        self.private_origins("https://app.example")
        self.config["parent_env_files"][0]["reviewed_allowlist"].append(
            "CRAWLER_LOGIN_V2_ENABLED"
        )
        with self.assertRaises(InvalidRelease):
            validate(self.config)

    def test_missing_or_ambiguous_origins_source_is_rejected(self):
        self.config["environment"].clear()
        with self.assertRaises(InvalidRelease):
            validate(self.config)
        self.config["environment"]["CRAWLER_LOGIN_V2_ORIGINS"] = "https://app.example"
        self.private_origins("https://app.example")
        self.config["environment"]["CRAWLER_LOGIN_V2_ORIGINS"] = "https://other.example"
        with self.assertRaises(InvalidRelease):
            validate(self.config)

    def test_explicit_http_https_and_ipv6_origins_are_accepted(self):
        value = "https://content-audit.guixucloud.cn,http://127.0.0.1:13299,http://[::1]:13299"
        self.assertEqual(validate_login_v2_origins(value), value)

    def test_unsafe_non_origin_values_are_rejected(self):
        for value in (
            "",
            "*",
            "https://*.example",
            "https://user:password@app.example",
            "https://app.example/",
            "https://app.example?",
            "https://app.example#",
            "https://app.example:",
            "https://app.example;bad",
            "https://app.example/path",
            "https://app.example?ticket=private",
            "https://app.example#fragment",
            "ftp://app.example",
            "https://app.example:99999",
            "https://app.example:0",
            "https://app.example,,https://other.example",
            "https://app.example\\evil",
            "https://app.example%2f.evil",
            "https://app. example",
        ):
            with self.subTest(value=value):
                with self.assertRaises(InvalidRelease):
                    validate_login_v2_origins(value)

    def test_private_origins_validated_as_owner_without_printing_values(self):
        self.private_origins("https://content-audit.guixucloud.cn")
        self.assertEqual(
            environment(validate(self.config))["CRAWLER_LOGIN_V2_ORIGINS"],
            "https://content-audit.guixucloud.cn",
        )
        with patch("control.check_artifacts"):
            self.assertTrue(owner_preflight(self.config)["passed"])

    def test_bad_private_origin_blocks_owner_preflight(self):
        self.private_origins("https://app.example/path?ticket=SECRET")
        validate(self.config)  # structural validation never reads a secret file
        with patch("control.check_artifacts"):
            with self.assertRaisesRegex(InvalidRelease, "login_v2_origin_invalid"):
                owner_preflight(self.config)

    def test_actual_app_settings_receive_enabled_v2_and_exact_origins(self):
        values = environment(validate(self.config))
        # Disable dotenv file loading only; run the actual Settings class in a fresh
        # Python process so no preexisting Settings globals or private .env leak in.
        code = "from unittest.mock import patch\nwith patch('dotenv.load_dotenv'):\n from backend.audit_agent.config import Settings\n assert Settings.crawler_login_v2_enabled is True\n assert Settings.crawler_login_v2_origins == ['https://app.example']\nprint('actual-settings-v2-pass')"
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[3],
            env=values,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "actual-settings-v2-pass")

    def test_cutover_rejects_bad_private_origins_before_manifest_write(self):
        self.private_origins("https://app.example/path")
        target = self.root / "current.json"
        target.write_text("unchanged")
        receipt = {
            "schema_version": 1,
            "release_id": self.config["release_id"],
            "maintenance_verified": True,
            "operator": "test-only",
            "manifest_sha256": hashlib.sha256(
                json.dumps(self.config, sort_keys=True).encode()
            ).hexdigest(),
        }
        for key in ("quiescence", "backup", "migration", "linux_smoke", "docker_smoke"):
            path = self.root / (key + ".json")
            path.write_text(
                json.dumps({"passed": True, "release_id": self.config["release_id"]})
            )
            receipt[key] = {"path": str(path), "sha256": control.digest(path)}
        path = self.root / "cutover.json"
        path.write_text(json.dumps(receipt))
        # Explicitly bypass unrelated gates ONLY in this isolated unit test to reach
        # the final environment validation. No release evidence is written or approved.
        with (
            patch("control.security_gate"),
            patch("control.check_artifacts"),
            patch("quiescence.verify_receipt"),
        ):
            with self.assertRaisesRegex(InvalidRelease, "login_v2_origin_invalid"):
                control.cutover(validate(self.config), target, path)
        self.assertEqual(target.read_text(), "unchanged")

    def test_frontend_startup_also_rejects_invalid_private_origin(self):
        self.private_origins("https://app.example/path")
        source = Path(__file__).resolve().parents[1] / "runtime-support/frontend.py"
        spec = importlib.util.spec_from_file_location(
            "frontend_v2_origin_contract", source
        )
        module = importlib.util.module_from_spec(spec)
        with (
            patch.dict(
                os.environ, {"CAMOUFOX_RELEASE_MANIFEST": "/test-only/runtime.json"}
            ),
            patch.object(control, "read_json", return_value=self.config),
            patch.object(control, "check_artifacts"),
        ):
            with self.assertRaisesRegex(InvalidRelease, "login_v2_origin_invalid"):
                spec.loader.exec_module(module)

    def test_documented_manifest_example_has_v2_origin_but_no_production_approval(self):
        config = validate(
            control.read_json(
                Path(__file__).resolve().parents[1] / "runtime.json.example"
            )
        )
        values = environment(config)
        self.assertEqual(values["CRAWLER_LOGIN_V2_ENABLED"], "true")
        self.assertEqual(
            values["CRAWLER_LOGIN_V2_ORIGINS"], "https://content-audit.guixucloud.cn"
        )
        with self.assertRaises((InvalidRelease, OSError)):
            control.security_gate(config)

    def test_runtime_startup_rejects_invalid_private_origin(self):
        self.private_origins("https://app.example/path")
        source = Path(__file__).resolve().parents[1] / "runtime-support/serve.py"
        spec = importlib.util.spec_from_file_location("startup_v2_contract", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with (
            patch.dict(
                os.environ,
                {"CAMOUFOX_RELEASE_MANIFEST": "/test-only/runtime.json"},
                clear=True,
            ),
            patch.object(control, "read_json", return_value=self.config),
            patch.object(module, "read_json", return_value=self.config),
            patch.object(module, "check_artifacts"),
        ):
            with self.assertRaisesRegex(InvalidRelease, "login_v2_origin_invalid"):
                module.configure()


if __name__ == "__main__":
    unittest.main()
