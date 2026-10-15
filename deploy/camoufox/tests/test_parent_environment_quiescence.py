import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control import InvalidRelease, environment, parent_private_environment, validate
from quiescence import manifest_hash, verify_receipt
from test_control import manifest


class PrivateEnvironmentAndQuiescence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.m = manifest(self.root)
        self.m["ports"].update(frontend=13299, allowed=[18298, 13299])

    def tearDown(self):
        self.tmp.cleanup()

    def private_file(self, raw):
        path = self.root / "parent-env.json"
        path.write_text(raw)
        path.chmod(0o600)
        self.m["parent_env_files"] = [
            {
                "path": str(path),
                "sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "expected_uid": os.geteuid(),
                "reviewed_allowlist": ["BUSINESS_API_KEY"],
            }
        ]
        return path

    def test_parent_secret_available_only_through_reviewed_private_file(self):
        self.private_file('{"BUSINESS_API_KEY":"secret"}')
        value = environment(validate(self.m))
        self.assertEqual(value["BUSINESS_API_KEY"], "secret")
        self.assertEqual(value["CRAWLER_CHILD_UID"], "10001")
        self.assertEqual(value["CRAWLER_CHILD_GID"], "10001")

    def test_bad_mode_owner_hash_and_unknown_key_fail(self):
        path = self.private_file('{"BUSINESS_API_KEY":"secret"}')
        path.chmod(0o644)
        with self.assertRaises(InvalidRelease):
            parent_private_environment(self.m)
        path.chmod(0o600)
        path.write_text('{"BUSINESS_API_KEY":"changed"}')
        with self.assertRaises(InvalidRelease):
            parent_private_environment(self.m)

    def test_duplicate_secret_json_rejected(self):
        self.private_file('{"BUSINESS_API_KEY":"a","BUSINESS_API_KEY":"b"}')
        with self.assertRaises(InvalidRelease):
            parent_private_environment(self.m)

    def test_private_config_cannot_override_runtime(self):
        self.private_file("{}")
        self.m["parent_env_files"][0]["reviewed_allowlist"] = ["CRAWLER_CHILD_UID"]
        with self.assertRaises(InvalidRelease):
            validate(self.m)

    def receipt(self):
        return {
            "schema_version": 1,
            "release_id": self.m["release_id"],
            "manifest_sha256": manifest_hash(self.m),
            "observed_at": time.time(),
            "passed": True,
            "observation": {"boot_id": "boot", "remaining_pids": []},
            "old_runtime_fence_supported": False,
            "requires_formal_units_stopped": True,
        }

    def test_fresh_receipt_rechecks_live_state(self):
        receipt = self.receipt()
        path = self.root / "q.json"
        path.write_text(json.dumps(receipt))
        with patch(
            "quiescence.observe", return_value=receipt["observation"]
        ) as observe:
            verify_receipt(self.m, path)
            observe.assert_called_once()

    def test_stale_receipt_and_changed_live_state_fail(self):
        receipt = self.receipt()
        receipt["observed_at"] -= 301
        path = self.root / "q.json"
        path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(InvalidRelease, "expired"):
            verify_receipt(self.m, path)
        receipt["observed_at"] = time.time()
        path.write_text(json.dumps(receipt))
        with patch("quiescence.observe", return_value={"boot_id": "changed"}):
            with self.assertRaisesRegex(InvalidRelease, "live_state_changed"):
                verify_receipt(self.m, path)

    def test_boolean_only_proof_cannot_be_used(self):
        path = self.root / "q.json"
        path.write_text('{"passed":true}')
        with self.assertRaises(InvalidRelease):
            verify_receipt(self.m, path)


if __name__ == "__main__":
    unittest.main()
