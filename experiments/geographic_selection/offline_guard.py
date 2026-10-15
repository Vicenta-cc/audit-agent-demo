"""Pytest-session audit interception for accidental external effects."""
import sys
from pathlib import Path
from backend.audit_agent.qwen_client import QwenClient
from backend.audit_agent.crawler_adapter import MediaCrawlerAdapter
from audit_agent.qwen_client import QwenClient as AlternateQwenClient
from audit_agent.crawler_adapter import MediaCrawlerAdapter as AlternateCrawler


class OfflineGuard:
    def __init__(self, database_root):
        self.metrics = {'network_attempts': 0, 'process_attempts': 0,
                        'real_model_attempts': 0, 'crawler_launch_attempts': 0,
                        'forbidden_database_attempts': 0}
        self.database_root = Path(database_root).resolve()
        self.database_paths = set()
        self._patches = []
        self.test_counts = {}
        self.active = False

    def _deny(self, category):
        def deny(*args, **kwargs):
            self.metrics[category] += 1
            raise RuntimeError('P4_OFFLINE_GUARD:' + category)
        return deny

    def _audit(self, event, args):
        if not self.active:
            return
        if event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
            self._deny('network_attempts')()
        if event in {'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.spawn', 'os.exec', 'os.fork', 'pty.spawn'}:
            self._deny('process_attempts')()
        if event == 'sqlite3.connect':
            path = str(args[0])
            if path != ':memory:' and not Path(path).resolve().is_relative_to(self.database_root):
                self._deny('forbidden_database_attempts')()
            self.database_paths.add(path)

    def pytest_sessionstart(self, session):
        self.active = True
        sys.addaudithook(self._audit)
        for owner, name, category in ((QwenClient, 'audit_text', 'real_model_attempts'),
                (MediaCrawlerAdapter, '_run_command', 'crawler_launch_attempts'),
                (MediaCrawlerAdapter, '_run_command_locked', 'crawler_launch_attempts'),
                (AlternateQwenClient, 'audit_text', 'real_model_attempts'),
                (AlternateCrawler, '_run_command', 'crawler_launch_attempts'),
                (AlternateCrawler, '_run_command_locked', 'crawler_launch_attempts')):
            self._patches.append((owner, name, getattr(owner, name)))
            setattr(owner, name, self._deny(category))

    def pytest_sessionfinish(self, session, exitstatus):
        # Denials caught by tested code must still fail the overall test session.
        if any(self.metrics.values()):
            session.exitstatus = 1
        self.active = False
        for owner, name, original in self._patches:
            setattr(owner, name, original)

    def pytest_terminal_summary(self, terminalreporter):
        self.test_counts = {key: len(value) for key, value in terminalreporter.stats.items() if key}
