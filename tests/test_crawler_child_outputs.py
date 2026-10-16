import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

from backend.audit_agent.crawler_child_outputs import ChildOutputError, ChildOutputSpool


class ChildOutputSpoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        (self.state / "outputs").mkdir(mode=0o700)
        self.destination = self.root / "business"
        self.destination.mkdir()
        self.spool = ChildOutputSpool.create(
            self.state, os.geteuid(), os.getegid(), self.destination, 100000, 50
        )

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        path = self.spool.child_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def bind_writer(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True
        )
        self.addCleanup(
            lambda: process.poll() is None
            and (process.terminate(), process.wait(timeout=3))
        )
        self.spool.bind_process_group(process.pid)
        return process

    def stop_writer(self, process):
        process.terminate()
        process.wait(timeout=3)

    def test_cleanup_removes_only_this_run_after_real_group_drain(self):
        self.write("douyin/videos/1/a.mp4", b"media")
        sentinel = self.state / "outputs/other-task"
        sentinel.mkdir()
        (sentinel / "keep").write_text("safe")
        source = self.destination / "skip.txt"
        source.write_text("123\n")
        command = self.spool.stage_inputs(
            ["python", "--skip_aweme_ids_file", str(source)]
        )
        inputs = Path(command[-1]).parent
        process = self.bind_writer()
        self.stop_writer(process)
        self.spool.sync_final()
        self.spool.cleanup()
        self.assertFalse(self.spool.child_root.exists())
        self.assertFalse(inputs.exists())
        self.assertEqual((sentinel / "keep").read_text(), "safe")
        self.assertEqual(
            (self.destination / "douyin/videos/1/a.mp4").read_bytes(), b"media"
        )
        self.assertTrue(self.spool.cleanup()["already_cleaned"])

    def test_active_group_or_unbound_cleanup_is_refused(self):
        with self.assertRaisesRegex(ChildOutputError, "NOT_BOUND"):
            self.spool.cleanup(failed=True)
        with self.assertRaisesRegex(ChildOutputError, "NOT_BOUND"):
            self.spool.sync_final()
        process = self.bind_writer()
        with self.assertRaisesRegex(ChildOutputError, "STILL_ACTIVE"):
            self.spool.cleanup(failed=True)
        with self.assertRaisesRegex(ChildOutputError, "STILL_ACTIVE"):
            self.spool.sync_final()
        self.stop_writer(process)

    def test_group_drain_without_handoff_refuses_success_cleanup(self):
        process = self.bind_writer()
        self.stop_writer(process)
        with self.assertRaisesRegex(ChildOutputError, "HANDOFF_NOT_VERIFIED"):
            self.spool.cleanup()

    def test_failed_cleanup_retains_only_small_receipt_not_duplicate_media(self):
        self.write("douyin/videos/1/a.mp4", b"x" * 10000)
        process = self.bind_writer()
        self.stop_writer(process)
        self.spool.cleanup(failed=True)
        files = list(self.spool.child_root.rglob("*"))
        self.assertEqual([path.name for path in files], ["failure-receipt.json"])
        self.assertLess(files[0].stat().st_size, 1024)
        self.assertTrue(json.loads(files[0].read_text())["media_removed"])

    def test_cleanup_rejects_changed_scope_and_symlinks(self):
        process = self.bind_writer()
        self.stop_writer(process)
        original = self.spool.child_root
        self.spool.child_root = self.state
        with self.assertRaisesRegex(ChildOutputError, "SCOPE_CHANGED"):
            self.spool.cleanup(failed=True)
        self.spool.child_root = original
        target = self.root / "untouched"
        target.write_text("safe")
        (original / "link").symlink_to(target)
        with self.assertRaisesRegex(ChildOutputError, "SYMLINK"):
            self.spool.cleanup(failed=True)
        self.assertEqual(target.read_text(), "safe")

    def test_cleanup_rejects_replaced_run_inode(self):
        process = self.bind_writer()
        self.stop_writer(process)
        original = self.spool.child_root
        old = original.with_name(original.name + ".saved")
        original.rename(old)
        original.mkdir(mode=0o700)
        with self.assertRaisesRegex(ChildOutputError, "IDENTITY_CHANGED"):
            self.spool.cleanup(failed=True)
        self.assertTrue(old.exists())

    def test_skip_ids_staged_privately_outside_result_tree(self):
        source = self.destination / "skip.txt"
        source.write_text("123\naweme_abc-123\n")
        command = [
            "python",
            "--skip_aweme_ids_file",
            str(source),
            "--reusable_content_db",
            "/parent-only/db",
        ]
        staged = self.spool.stage_inputs(command)
        path = Path(staged[2])
        self.assertEqual(path.read_text(), source.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn(self.spool.child_root, path.parents)
        self.assertEqual(staged[-1], "/parent-only/db")
        self.assertEqual(self.spool.sync()["files"], 0)

    def test_skip_ids_reject_invalid_file_and_duplicate_flags(self):
        source = self.destination / "skip.txt"
        source.write_text("../escape\n")
        with self.assertRaises(ChildOutputError):
            self.spool.stage_inputs(["python", "--skip_aweme_ids_file", str(source)])
        source.write_text("123\n")
        with self.assertRaises(ChildOutputError):
            self.spool.stage_inputs(
                [
                    "python",
                    "--skip_aweme_ids_file",
                    str(source),
                    "--skip_aweme_ids_file",
                    str(source),
                ]
            )
        alias = self.destination / "alias"
        alias.symlink_to(source)
        with self.assertRaises(OSError):
            self.spool.stage_inputs(["python", "--skip_aweme_ids_file", str(alias)])

    def test_skip_ids_hardlink_or_bad_encoding_rejected(self):
        source = self.destination / "skip.txt"
        source.write_bytes(b"\xff")
        with self.assertRaises(ChildOutputError):
            self.spool.stage_inputs(["python", "--skip_aweme_ids_file", str(source)])
        source.write_text("123\n")
        alias = self.destination / "hardlink"
        os.link(source, alias)
        with self.assertRaises(ChildOutputError):
            self.spool.stage_inputs(["python", "--skip_aweme_ids_file", str(alias)])

    def test_unique_private_spool_without_business_permissions(self):
        self.assertEqual(self.spool.child_root.stat().st_mode & 0o777, 0o700)
        second = ChildOutputSpool.create(
            self.state, os.geteuid(), os.getegid(), self.destination, 100000, 50
        )
        self.assertNotEqual(second.child_root, self.spool.child_root)

    def test_command_rewrites_only_exact_task_path(self):
        command = ["python", "main.py", "--save_data_path", str(self.destination)]
        self.assertEqual(
            self.spool.rewrite_command(command)[-1], str(self.spool.child_root)
        )
        self.assertEqual(command[-1], str(self.destination))
        for bad in (
            command + ["--save_data_path", "/tmp"],
            ["python", "--save_data_path=/tmp"],
            ["python", "--save_data_path", str(self.destination / "../else")],
        ):
            with self.assertRaises(ChildOutputError):
                self.spool.rewrite_command(bad)

    def test_stream_complete_lines_and_rewrite_media_path(self):
        path = self.write(
            "douyin/jsonl/search_contents_1.jsonl",
            json.dumps(
                {
                    "id": "1",
                    "path": str(self.spool.child_root / "douyin/images/1/a.jpg"),
                }
            ).encode()
            + b'\n{"partial":',
        )
        self.spool.sync()
        result = list((self.destination / "douyin/jsonl").glob("*contents*"))[0]
        data = json.loads(result.read_text())
        self.assertEqual(data["path"], str(self.destination / "douyin/images/1/a.jpg"))
        path.write_bytes(path.read_bytes() + b"true}\n")
        self.spool.sync(final=True)
        self.assertEqual(len(result.read_text().splitlines()), 2)

    def test_unchanged_files_not_copied_again(self):
        self.write("douyin/videos/1/a.mp4", b"video")
        self.assertEqual(self.spool.sync()["updated"], 1)
        self.assertEqual(self.spool.sync()["updated"], 0)
        self.assertEqual(self.spool.sync(final=True)["updated"], 1)

    def test_existing_different_media_never_overwritten(self):
        destination = self.destination / "douyin/videos/1/a.mp4"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"original")
        self.write("douyin/videos/1/a.mp4", b"new")
        with self.assertRaisesRegex(ChildOutputError, "CONFLICT"):
            self.spool.sync()
        self.assertEqual(destination.read_bytes(), b"original")

    def test_existing_identical_media_can_be_reused(self):
        destination = self.destination / "douyin/videos/1/a.mp4"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"same")
        self.write("douyin/videos/1/a.mp4", b"same")
        self.spool.sync(final=True)
        self.assertEqual(destination.read_bytes(), b"same")

    def test_source_symlink_hardlink_fifo_and_unknown_paths_rejected(self):
        for kind in ("symlink", "hardlink", "fifo", "unknown"):
            with self.subTest(kind=kind):
                name = (
                    "douyin/videos/1/a.mp4" if kind != "unknown" else "business.sqlite"
                )
                path = self.spool.child_root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                external = self.root / "external"
                external.write_bytes(b"secret")
                if kind == "symlink":
                    path.symlink_to(external)
                elif kind == "hardlink":
                    os.link(external, path)
                elif kind == "fifo":
                    os.mkfifo(path)
                else:
                    path.write_bytes(b"injection")
                with self.assertRaises((ChildOutputError, OSError)):
                    self.spool.sync()
                path.unlink()

    def test_destination_symlink_does_not_escape(self):
        external = self.root / "external"
        external.mkdir()
        (self.destination / "douyin").symlink_to(external, target_is_directory=True)
        self.write("douyin/videos/1/a.mp4", b"new")
        with self.assertRaises(OSError):
            self.spool.sync()
        self.assertEqual(list(external.iterdir()), [])

    def test_changed_published_file_is_not_overwritten(self):
        source = self.write("douyin/videos/1/a.mp4", b"one")
        self.spool.sync()
        destination = self.destination / "douyin/videos/1/a.mp4"
        destination.write_bytes(b"other-task")
        source.write_bytes(b"two")
        with self.assertRaisesRegex(ChildOutputError, "DESTINATION_CHANGED"):
            self.spool.sync()
        self.assertEqual(destination.read_bytes(), b"other-task")

    def test_limits_fail_closed(self):
        self.spool.max_bytes = 3
        self.write("douyin/videos/1/a.mp4", b"four")
        with self.assertRaisesRegex(ChildOutputError, "LIMIT"):
            self.spool.sync()

    def test_final_partial_json_is_explicit_error(self):
        self.write("douyin/jsonl/search_contents_1.jsonl", b'{"id":')
        self.spool.sync()
        with self.assertRaisesRegex(ChildOutputError, "INCOMPLETE"):
            self.spool.sync(final=True)

    def test_private_parent_required(self):
        (self.state / "outputs").chmod(0o755)
        with self.assertRaisesRegex(ChildOutputError, "PARENT_NOT_PRIVATE"):
            ChildOutputSpool.create(
                self.state, os.geteuid(), os.getegid(), self.destination, 100000, 50
            )


if __name__ == "__main__":
    unittest.main()
