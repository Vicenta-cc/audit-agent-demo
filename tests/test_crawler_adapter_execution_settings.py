import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.audit_agent.crawler_adapter import (
    ACCOUNT_AUTH_INVALID_MARKER,
    ACCOUNT_AUTH_STATE_ENV,
    CrawlOutput,
    CrawlerAuthenticationError,
    CrawlerVerificationError,
    MediaCrawlerAdapter,
)


class RecordingAdapter(MediaCrawlerAdapter):
    def _build_runner(self) -> list[str]:
        return ["python", "main.py"]

    def _run_command(self, **kwargs) -> CrawlOutput:
        self.recorded = kwargs
        return CrawlOutput(
            platform=kwargs["platform"],
            contents=[],
            comments=[],
            output_dir=kwargs["save_root"],
            command=kwargs["command"],
        )


class CrawlerAdapterExecutionSettingsTest(unittest.TestCase):
    def test_shared_crawler_lock_prevents_a_second_process_from_starting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            code = "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read()"
            child = subprocess.Popen([sys.executable, '-c', code, str(Path(temp_dir) / '.xhs-audit-crawler.lock')], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), 'locked')
                adapter = MediaCrawlerAdapter(Path(temp_dir))
                with patch.object(adapter, '_run_command_locked') as run, patch('backend.audit_agent.crawler_adapter.sleep'):
                    with self.assertRaisesRegex(RuntimeError, '已停止'):
                        adapter._run_command(command=[], save_root=Path(temp_dir) / 'output', platform='dy', max_notes=20,
                                             progress_callback=None, content_callback=None,
                                             stop_checker=iter([False, True]).__next__)
                    run.assert_not_called()
            finally:
                child.communicate(timeout=5)

    def test_keyword_limit_remains_per_keyword_while_progress_counts_all_keywords(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = RecordingAdapter(Path(temp_dir))
            result = adapter.run_search(platform='dy', keyword='词一,词二', start_page=1, max_notes=20,
                                        max_comments=1000, max_concurrency=1, max_items_per_minute=3,
                                        get_sub_comment=False, save_root=Path(temp_dir) / 'output')
            self.assertEqual(result.command[result.command.index('--crawler_max_notes_count') + 1], '20')
            self.assertEqual(adapter.recorded['max_notes'], 40)

    def test_command_contains_rate_and_concurrency_but_not_login_state(self):
        auth_state = {
            "cookies": [{"name": "session", "value": "plain-cookie-secret"}],
            "origins": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = RecordingAdapter(Path(temp_dir))
            output = adapter.run_search(
                platform="xhs",
                keyword="测试",
                start_page=1,
                max_notes=5,
                max_comments=10,
                max_concurrency=3,
                max_items_per_minute=4,
                get_sub_comment=False,
                save_root=Path(temp_dir) / "output",
                auth_state=auth_state,
            )

        command = output.command
        self.assertEqual(command[command.index("--max_concurrency_num") + 1], "3")
        self.assertEqual(command[command.index("--crawler_max_items_per_minute") + 1], "4")
        self.assertEqual(command[command.index("--lt") + 1], "cookie")
        self.assertNotIn("plain-cookie-secret", " ".join(command))

    def test_login_state_is_encoded_only_in_subprocess_environment(self):
        adapter = MediaCrawlerAdapter(Path("/tmp/mediacrawler"))
        auth_state = {"cookies": [], "origins": [{"origin": "https://example.com", "localStorage": []}]}

        env = adapter._subprocess_env(auth_state)

        self.assertIn(ACCOUNT_AUTH_STATE_ENV, env)
        decoded = json.loads(base64.b64decode(env[ACCOUNT_AUTH_STATE_ENV]).decode("utf-8"))
        self.assertEqual(decoded, auth_state)

    def test_account_invalid_marker_becomes_typed_authentication_error(self):
        class FailedProcess:
            returncode = 1

            def __init__(self, *args, stderr, **kwargs):
                stderr.write(f"{ACCOUNT_AUTH_INVALID_MARKER}: expired")
                stderr.flush()

            def poll(self):
                return self.returncode

        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MediaCrawlerAdapter(Path(temp_dir))
            with patch("backend.audit_agent.crawler_adapter.subprocess.Popen", FailedProcess):
                with self.assertRaises(CrawlerAuthenticationError):
                    adapter._run_command(
                        command=["python", "main.py"],
                        save_root=Path(temp_dir) / "output",
                        platform="xhs",
                        max_notes=1,
                        progress_callback=None,
                        content_callback=None,
                        auth_state={"cookies": [], "origins": []},
                    )

    def test_account_verify_marker_becomes_typed_verification_error(self):
        class FailedProcess:
            returncode = 1
            def __init__(self, *args, stderr, **kwargs):
                stderr.write("ACCOUNT_VERIFY: platform request requires verification")
                stderr.flush()
            def poll(self):
                return self.returncode

        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MediaCrawlerAdapter(Path(temp_dir))
            with patch("backend.audit_agent.crawler_adapter.subprocess.Popen", FailedProcess):
                with self.assertRaises(CrawlerVerificationError):
                    adapter._run_command(
                        command=["python", "main.py"], save_root=Path(temp_dir) / "output",
                        platform="dy", max_notes=1, progress_callback=None,
                        content_callback=None, auth_state={"cookies": [], "origins": []},
                    )


if __name__ == "__main__":
    unittest.main()
