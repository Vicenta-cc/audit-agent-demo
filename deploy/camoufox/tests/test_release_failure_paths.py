import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build import build
from control import (
    InvalidRelease,
    check_child_artifacts,
    check_profiles,
    owner_preflight,
    preflight,
)
from maintenance import set_maintenance
from docker_rehearse import rehearse
from test_control import manifest


class FailurePaths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.m = manifest(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_owner_preflight_only_opens_parent_files_as_owner(self):
        with (
            patch("control.check_artifacts") as artifacts,
            patch("control.parent_private_environment") as secrets,
        ):
            result = owner_preflight(self.m)
        artifacts.assert_called_once_with(self.m)
        secrets.assert_called_once_with(self.m)
        self.assertEqual(result["scope"], "app_owner")

    def test_child_artifact_check_never_opens_private_parent_environment_or_git(self):
        with (
            patch("control.digest", return_value="d" * 64) as hashed,
            patch("control.parent_private_environment") as secrets,
            patch("control.subprocess.check_output") as git,
        ):
            check_child_artifacts(self.m)
        self.assertEqual(
            {call.args[0] for call in hashed.call_args_list},
            {
                self.m["artifacts"][k]["path"]
                for k in ("browser_binary", "browser_assets", "runtime_support")
            },
        )
        secrets.assert_not_called()
        git.assert_not_called()

    def test_existing_venv_never_upgraded(self):
        with patch("build.check_artifacts"):
            with self.assertRaisesRegex(
                InvalidRelease, "new_build_destination_required"
            ):
                build(self.m, self.root)

    def test_failed_build_does_not_write_success_receipt(self):
        destination = self.root / "new"
        with (
            patch("build.check_artifacts"),
            patch(
                "build.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, ["pip"]),
            ),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                build(self.m, destination)
        self.assertFalse((destination / "build-receipt.json").exists())

    def test_disable_maintenance_requires_verified_release(self):
        self.m["ports"].update(frontend=13299, allowed=[18298, 13299])
        path = self.root / "runtime.json"
        path.write_text(json.dumps(self.m))
        with self.assertRaises(InvalidRelease):
            set_maintenance(path, False)
        self.assertTrue(json.loads(path.read_text())["maintenance"])

    def test_profile_os_mismatch_refused(self):
        profile = self.root / "state/profiles/account"
        profile.mkdir(parents=True)
        (profile / "profile.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "engine": "camoufox",
                    "version": "156.0.1-beta.39",
                    "os": "macos",
                    "account_key": "account",
                    "generation": "migration-test-generation",
                    "display_mode": "headed",
                }
            )
        )
        with self.assertRaisesRegex(InvalidRelease, "profile_version_os"):
            check_profiles(self.m)

    def test_profile_identity_change_refused(self):
        profile = self.root / "state/profiles/account"
        profile.mkdir(parents=True)
        identity = profile / "launch-identity.json"
        identity.write_text("{}")
        (profile / "profile.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "engine": "camoufox",
                    "version": "156.0.1-beta.39",
                    "os": "linux",
                    "account_key": "account",
                    "generation": "migration-test-generation",
                    "display_mode": "headed",
                    "identity_hash": "0" * 64,
                }
            )
        )
        with self.assertRaisesRegex(InvalidRelease, "profile_fingerprint_hash"):
            check_profiles(self.m)

    def test_mac_cannot_pass_linux_preflight(self):
        with (
            patch("control.check_child_artifacts"),
            patch("control.platform.system", return_value="Darwin"),
        ):
            with self.assertRaisesRegex(InvalidRelease, "linux_x86_required"):
                preflight(self.m)

    def test_mutable_docker_image_rejected_before_running(self):
        with patch("docker_rehearse.subprocess.check_output") as inspect:
            with self.assertRaisesRegex(InvalidRelease, "immutable_image_id_required"):
                rehearse(
                    "latest", "runtime.json", self.root, self.root / "receipt.json"
                )
            inspect.assert_not_called()

    def test_docker_failure_does_not_write_receipt(self):
        with (
            patch(
                "docker_rehearse.subprocess.check_output",
                return_value=json.dumps([{"Architecture": "amd64", "Os": "linux"}]),
            ),
            patch(
                "docker_rehearse.subprocess.run",
                return_value=subprocess.CompletedProcess([], 1),
            ),
        ):
            with self.assertRaisesRegex(InvalidRelease, "docker_smoke_failed"):
                rehearse(
                    "sha256:" + "a" * 64,
                    "runtime.json",
                    self.root,
                    self.root / "receipt.json",
                )
        self.assertFalse((self.root / "receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
