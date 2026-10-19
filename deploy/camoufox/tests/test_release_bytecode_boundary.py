"""Fresh Turn interpreters must not mutate pinned source artifacts."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_control import manifest
from control import InvalidRelease, environment, validate


class ReleaseBytecodeBoundary(unittest.TestCase):
    def test_fresh_child_does_not_write_source_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = manifest(root)
            config["ports"].update(frontend=13299, allowed=[18298, 13299])
            source = root / "source"
            source.mkdir()
            (source / "pinned_module.py").write_text("value = 42\n")
            env = environment(validate(config))
            result = subprocess.run(
                [sys.executable, "-c", "import pinned_module; assert pinned_module.value == 42"],
                cwd=source, env=env, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
            self.assertFalse((source / "__pycache__").exists())

    def test_private_environment_cannot_disable_bytecode_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = manifest(root)
            config["ports"].update(frontend=13299, allowed=[18298, 13299])
            config["parent_env_files"] = [{
                "path": str(root / "private.json"), "sha256": "a" * 64,
                "expected_uid": os.geteuid(),
                "reviewed_allowlist": ["PYTHONDONTWRITEBYTECODE"],
            }]
            with self.assertRaisesRegex(InvalidRelease, "parent_env_controlled_override"):
                validate(config)
