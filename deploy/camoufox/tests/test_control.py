import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from control import (  # noqa: E402
    InvalidRelease,
    atomic_json,
    cutover,
    digest,
    environment,
    main,
    validate,
)


def manifest(root):
    paths = {
        key: str(root / key)
        for key in (
            "app",
            "crawler",
            "frontend_dist",
            "runtime_support",
            "app_requirements",
            "crawler_requirements",
            "wheelhouse",
            "browser_archive",
            "browser_binary",
            "browser_assets",
        )
    }
    return {
        "schema_version": 1,
        "release_id": "test-release",
        "mode": "staging",
        "maintenance": True,
        "sources": {
            "app_commit": "a" * 40,
            "crawler_commit": "b" * 40,
            "frontend_hotfix_commit": "c" * 40,
        },
        "artifacts": {
            key: {"path": value, "sha256": "d" * 64} for key, value in paths.items()
        },
        "browser": {
            "engine": "camoufox",
            "sdk": "0.5.8",
            "playwright": "1.62.0",
            "version": "156.0.1-beta.39",
            "platform": "linux.x86_64",
            "prerelease": True,
            "asset_url": "https://github.com/daijro/camoufox/releases/download/v156.0.1-beta.39/camoufox.zip",
        },
        "runtime": {
            "state_root": str(root / "state"),
            "profile_root": str(root / "state/profiles"),
            "logical_profile_root": str(root / "locks"),
            "cache_root": str(root / "state/cache"),
            "tmp_root": str(root / "state/tmp"),
            "home": str(root / "state/home"),
            "uid": 10001,
            "gid": 10001,
            "app_python": str(root / "venv-app/bin/python"),
            "crawler_python": str(root / "venv-crawler/bin/python"),
            "node": "/usr/bin/node",
            "authority_schema": 1,
            "scheduler_db": str(root / "db/scheduler.db"),
            "account_db": str(root / "db/audit_index.sqlite3"),
            "owner_db": str(root / "db/owners.db"),
            "archive_root": str(root / "archive"),
        },
        "ports": {
            "host": "127.0.0.1",
            "backend": 18298,
            "frontend": 13298,
            "allowed": [18298, 13298],
            "protected": [80, 443, 3298, 8298],
        },
        "environment": {"CRAWLER_LOGIN_V2_ORIGINS": "https://app.example"},
        "parent_env_files": [],
        "quiescence_scope": {
            "units": ["xhs-audit-formal.service"],
            "process_roots": [str(root / "old-runtime")],
        },
        "security_evidence": {"path": str(root / "g0.json"), "sha256": "e" * 64},
    }


class ControlTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.m = manifest(self.root)
        # relay13298 is a production entrypoint too; staging must avoid it.
        self.m["ports"].update(frontend=13299, allowed=[18298, 13299])

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_manifest(self):
        self.assertEqual(validate(self.m), self.m)

    def test_unknown_and_secret_environment_refused(self):
        for key in (
            "HTTP_PROXY",
            "CRAWLER_AUTH_ENCRYPTION_KEY",
            "REQUEST_SCHEDULER_DB",
        ):
            m = copy.deepcopy(self.m)
            m["environment"][key] = "secret"
            with self.assertRaises(InvalidRelease):
                validate(m)

    def test_production_ports_not_reused(self):
        for port in (3298, 8298, 13298):
            m = copy.deepcopy(self.m)
            m["ports"].update(frontend=port, allowed=[18298, port])
            with self.assertRaises(InvalidRelease):
                validate(m)

    def test_nonroot_and_private_state(self):
        for key, value in (
            ("uid", 0),
            ("profile_root", "/elsewhere/profile"),
            ("account_db", str(self.root / "state/accounts.db")),
        ):
            m = copy.deepcopy(self.m)
            m["runtime"][key] = value
            with self.assertRaises(InvalidRelease):
                validate(m)

    def test_environment_does_not_inherit_secrets(self):
        with patch.dict(
            "os.environ",
            {"SECRET": "password", "HTTP_PROXY": "http://user:password@proxy"},
        ):
            value = environment(validate(self.m))
        self.assertNotIn("SECRET", value)
        self.assertNotIn("HTTP_PROXY", value)

    def test_manifest_paths_match_runtime_settings_contract(self):
        for key in ("profile_root", "cache_root", "tmp_root", "home"):
            m = copy.deepcopy(self.m)
            m["runtime"][key] += "-different"
            with self.assertRaisesRegex(InvalidRelease, "state_path_contract"):
                validate(m)
        m = copy.deepcopy(self.m)
        m["runtime"]["account_db"] = str(self.root / "db/different.sqlite3")
        with self.assertRaisesRegex(InvalidRelease, "account_database_contract"):
            validate(m)
        value = environment(validate(self.m))
        self.assertEqual(
            Path(value["XHS_AUDIT_DATA_DIR"]) / "audit_index.sqlite3",
            Path(self.m["runtime"]["account_db"]),
        )
        self.assertEqual(value["APP_AUTH_DB"], self.m["runtime"]["owner_db"])
        self.assertEqual(value["CRAWLER_BROWSER_MAINTENANCE"], "true")
        self.assertEqual(value["CRAWLER_CAMOUFOX_STATE_ROOT"], str(self.root / "state"))

    def test_unresolved_G0_rejects_before_target_change(self):
        evidence = self.root / "g0.json"
        evidence.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "browser_version": "156.0.1-beta.39",
                    "asset_sha256": "d" * 64,
                    "sdk_compatibility_passed": False,
                    "reviewer": "",
                    "reviewed_at": "",
                    "advisories": [],
                    "gate": "blocked",
                }
            )
        )
        self.m["security_evidence"]["sha256"] = digest(evidence)
        target = self.root / "current.json"
        target.write_text("original")
        for mode in ("staging", "production"):
            with self.subTest(mode=mode):
                config = copy.deepcopy(self.m)
                config["mode"] = mode
                config["geographic_runtime"] = True
                if mode == "production":
                    config["ports"].update(backend=8298, frontend=3298,
                                           allowed=[8298, 3298], protected=[80, 443, 13298])
                with self.assertRaisesRegex(InvalidRelease, "G0_not_passed"):
                    cutover(validate(config), target, self.root / "nonexistent-receipt")
                self.assertEqual(target.read_text(), "original")

    def test_hash_detects_changes_and_rejects_symlink(self):
        artifact = self.root / "file"
        artifact.write_text("a")
        before = digest(artifact)
        artifact.write_text("b")
        self.assertNotEqual(before, digest(artifact))
        alias = self.root / "alias"
        alias.symlink_to(artifact)
        with self.assertRaises(InvalidRelease):
            digest(alias)

    def test_atomic_private_manifest(self):
        target = self.root / "current.json"
        atomic_json(target, {"maintenance": True})
        self.assertEqual(json.loads(target.read_text()), {"maintenance": True})
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_candidate_cannot_be_labeled_stable(self):
        self.m["browser"]["prerelease"] = False
        with self.assertRaises(InvalidRelease):
            validate(self.m)

    def test_cli_does_not_echo_credentials(self):
        import contextlib
        import io

        path = self.root / "invalid.json"
        path.write_text('{"HTTP_PROXY":"http://user:secret@proxy"}')
        stderr = io.StringIO()
        with (
            patch("sys.argv", ["control", "validate", str(path)]),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(main(), 2)
        self.assertNotIn("secret", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
