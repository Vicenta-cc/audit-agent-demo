"""Run the bounded offline P4 checks with audit interception and saved evidence.

Run from repository root with the already-installed interpreter:
/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_selection.run_checks
"""
from __future__ import annotations
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
import pytest

ORIGINAL = ['tests/test_triage_engine.py', 'tests/test_triage_matcher.py',
            'tests/test_triaged_search.py', 'tests/test_triage_account_rotation.py',
            'tests/test_geographic_draft_configuration.py']


class Tee(io.TextIOBase):
    def __init__(self, original, log):
        self.original, self.log = original, log
    def write(self, text):
        self.original.write(text)
        self.log.write(text)
        self.log.flush()
        return len(text)
    def flush(self):
        self.original.flush()
        self.log.flush()


def main():
    root = Path(__file__).resolve().parents[2]
    if Path.cwd().resolve() != root:
        raise SystemExit('Run from the P4 repository root')
    expected = Path('/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python')
    if not expected.exists() or Path(sys.executable).resolve() != expected.resolve():
        raise SystemExit('Use the verified existing project .venv interpreter')
    files = sys.argv[1:] or ORIGINAL + sorted(str(p) for p in (root/'tests').glob('test_scoped_*.py'))
    output = root/'experiments/geographic_selection/artifacts'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='p4-offline-validation-') as temporary:
        isolated = Path(temporary)
        # These paths isolate test stores, without changing any geographic startup guard.
        os.environ['XHS_AUDIT_DATA_DIR'] = str(isolated/'data')
        os.environ['XHS_AUDIT_OUTPUTS_DIR'] = str(isolated/'outputs')
        os.environ['APP_AUTH_DB'] = str(isolated/'data/investigation_creation.sqlite3')
        os.environ['REQUEST_SCHEDULER_DB'] = str(isolated/'data/request_scheduler.sqlite3')
        from .offline_guard import OfflineGuard
        guard = OfflineGuard(isolated)
        pytest_args = ['-q', '--basetemp=' + str(isolated/'pytest'), *files]
        with (output/'pytest.log').open('w') as log, contextlib.redirect_stdout(Tee(sys.stdout, log)), contextlib.redirect_stderr(Tee(sys.stderr, log)):
            code = pytest.main(pytest_args, plugins=[guard])
    summary = {'interpreter': sys.executable, 'interpreter_resolved': str(expected.resolve()),
               'command': [sys.executable, '-m', 'pytest', *pytest_args],
               'actual_guarded_command': [sys.executable, '-m', 'experiments.geographic_selection.run_checks', *sys.argv[1:]],
               'exit_code': int(code), 'guard_metrics': guard.metrics, 'test_counts': guard.test_counts,
               'database_root': str(isolated), 'database_paths': sorted(guard.database_paths),
               'log': str(output/'pytest.log')}
    (output/'checks.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))
    return int(code)

if __name__ == '__main__':
    raise SystemExit(main())
