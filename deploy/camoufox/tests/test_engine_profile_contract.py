"""Deployment contract tested against the actual sibling Crawler initializer.

Only binary discovery/fingerprint generation are replaced; engine Profile writes,
binding fields, canonical hashes and generation behavior run unchanged.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control import InvalidRelease, check_profiles
from test_control import manifest

BINDING_FIELDS = (
    "schema",
    "engine",
    "account_key",
    "version",
    "os",
    "generation",
    "display_mode",
)


class ActualEngineProfileContract(unittest.TestCase):
    def setUp(self):
        repo = Path(
            os.environ.get(
                "CAMOUFOX_CONTRACT_CRAWLER_REPOSITORY",
                str(Path(__file__).resolve().parents[4] / "wukong-proxy-crawler"),
            )
        )
        source = repo / "tools/camoufox_browser.py"
        if not source.is_file():
            self.skipTest(
                "Actual Crawler checkout required; set CAMOUFOX_CONTRACT_CRAWLER_REPOSITORY"
            )
        spec = importlib.util.spec_from_file_location(
            "deployment_engine_profile_contract", source
        )
        self.engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.engine)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = manifest(self.root)
        self.profile_root = Path(self.config["runtime"]["profile_root"])
        guard = types.ModuleType("tools.navigation_guard_addon")
        guard.build_navigation_guard = lambda root: {}
        options = {
            "env": {"CAMOU_CONFIG_1": "fixed"},
            "firefox_user_prefs": {"browser.startup.page": 0},
            "args": [],
        }
        with (
            patch.dict(sys.modules, {"tools.navigation_guard_addon": guard}),
            patch.object(self.engine, "_host_os", return_value="linux"),
            patch.object(
                self.engine,
                "_installed_executable",
                return_value="/immutable/camoufox-bin",
            ),
            patch.object(self.engine, "_generate_identity", return_value=options),
        ):
            self.profile = self.engine.initialize_account_profile(
                "account-contract",
                root=self.profile_root,
                generation="migration-actual-engine-contract",
            )
        self.directory = self.profile["directory"]

    def tearDown(self):
        if hasattr(self, "tmp"):
            self.tmp.cleanup()

    def rewrite(self, **changes):
        metadata = json.loads((self.directory / "profile.json").read_text())
        identity = json.loads((self.directory / "launch-identity.json").read_text())
        for key, value in changes.items():
            if value is None:
                metadata.pop(key, None)
                identity["binding"].pop(key, None)
            else:
                metadata[key] = value
                identity["binding"][key] = value
        metadata["identity_hash"] = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        (self.directory / "profile.json").write_text(json.dumps(metadata))
        (self.directory / "launch-identity.json").write_text(json.dumps(identity))

    def test_actual_initializer_seven_field_binding_passes_deployment(self):
        identity = json.loads((self.directory / "launch-identity.json").read_text())
        self.assertEqual(set(identity["binding"]), set(BINDING_FIELDS))
        check_profiles(self.config)

    def test_missing_generation_fails_even_with_consistent_hash(self):
        self.rewrite(generation=None)
        with self.assertRaisesRegex(InvalidRelease, "profile_generation_required"):
            check_profiles(self.config)

    def test_linux_headless_fails_even_with_consistent_hash(self):
        self.rewrite(display_mode="headless")
        with self.assertRaisesRegex(InvalidRelease, "profile_linux_headed_required"):
            check_profiles(self.config)

    def test_mac_profile_cannot_be_published_to_linux(self):
        self.rewrite(os="macos", display_mode="headless")
        with self.assertRaisesRegex(InvalidRelease, "profile_version_os"):
            check_profiles(self.config)

    def test_changed_generation_binding_fails(self):
        metadata = json.loads((self.directory / "profile.json").read_text())
        metadata["generation"] = "different-migration"
        (self.directory / "profile.json").write_text(json.dumps(metadata))
        with self.assertRaisesRegex(InvalidRelease, "profile_identity_binding"):
            check_profiles(self.config)


if __name__ == "__main__":
    unittest.main()
