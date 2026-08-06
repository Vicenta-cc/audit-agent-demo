import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.audit_agent.crawler_adapter import (
    ACCOUNT_AUTH_INVALID_MARKER,
    ACCOUNT_AUTH_STATE_ENV,
    CrawlOutput,
    CrawlerAuthenticationError,
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


if __name__ == "__main__":
    unittest.main()
