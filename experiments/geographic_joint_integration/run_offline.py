"""Offline-only test/replay bootstrap; no product entry point."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / 'experiments/geographic_joint_integration'
PYTHON = Path('/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python')
ACTIVE = None
BLOB_SCRIPT = ('from experiments.geographic_execution_plan.storage import load_blob; import sys; '
               'sys.stdout.buffer.write(load_blob(sys.argv[1],expected_digest=sys.argv[2]))')
PLAN_SCRIPT = ('import json,sys; from experiments.geographic_execution_plan.storage import load_plan; '
               'p=load_plan(sys.argv[1],expected_identity=json.loads(sys.argv[2]),expected_digest=sys.argv[3]); '
               "print(json.dumps(p.model_dump(mode='json'),ensure_ascii=False,sort_keys=True))")
PROBE_SCRIPT = ('from experiments.geographic_execution_plan.offline_guards import ACTIVE, OfflineAccessBlocked; '
                'assert ACTIVE is not None; import sqlite3; '
                "\ntry: sqlite3.connect('/Users/offline-forbidden-child.sqlite3')"
                "\nexcept OfflineAccessBlocked: print('child guarded')"
                "\nelse: raise AssertionError('child guard absent')")


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def bootstrap():
    global ACTIVE
    if ACTIVE is not None or not os.environ.get('GEO_JOINT_TEMP'):
        return ACTIVE
    temporary = Path(os.environ['GEO_JOINT_TEMP']).resolve()
    output = Path(os.environ['GEO_JOINT_OUTPUT']).resolve()
    assert temporary.is_dir() and output.is_dir()
    # Legacy test collection supplies dir=/tmp explicitly. Keep its scratch
    # directories inside the same parent/child lifetime without editing tests.
    tempfile.tempdir = str(temporary)
    original_mkdtemp = tempfile.mkdtemp
    def mkdtemp(suffix=None, prefix=None, dir=None):
        if dir is not None and Path(dir).resolve() in {Path('/tmp').resolve(), Path('/var/tmp').resolve()}:
            dir = str(temporary)
        return original_mkdtemp(suffix=suffix, prefix=prefix, dir=dir)
    tempfile.mkdtemp = mkdtemp
    from experiments.geographic_execution_plan import offline_guards as upstream
    class JointGuard(upstream.OfflineGuards):
        def block(self, kind, target):
            expected = (kind == 'sqlite' and str(target) == '/Users/offline-forbidden-child.sqlite3'
                        and sys.argv[0] == '-c')
            expected = expected or (kind == 'sqlite' and str(target) == '/Users/offline-forbidden-bound.sqlite3'
                and os.environ.get('PYTEST_CURRENT_TEST') in {
                    'tests/test_geographic_execution_plan_guards.py::test_bound_attach_validated_per_execution[connection] (call)',
                    'tests/test_geographic_execution_plan_guards.py::test_bound_attach_validated_per_execution[cursor] (call)'})
            event = {'pid': os.getpid(), 'kind': kind, 'target': str(target), 'blocked': True,
                     'actual_succeeded': False, 'expected_probe': expected,
                     'test': os.environ.get('PYTEST_CURRENT_TEST')}
            self.attempts.append(event)
            with self.event_log.open('a') as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            raise upstream.OfflineAccessBlocked('JOINT_OFFLINE_BLOCKED:' + kind + ':' + str(target))
    guard = JointGuard(experiment_root=output, event_log=output/'guard-attempts.jsonl')
    guard.allowed_roots = (temporary, output)
    guard.install()
    upstream.ACTIVE = guard
    ACTIVE = guard
    def audit(event, args):
        if event == 'subprocess.Popen':
            executable, argv, cwd, env = args
            valid = (isinstance(argv, (list, tuple)) and len(argv) >= 3
                     and Path(str(argv[0])).resolve() == PYTHON.resolve())
            valid = valid and ((argv[1] == '-c' and argv[2] in {BLOB_SCRIPT, PLAN_SCRIPT, PROBE_SCRIPT})
                or (argv[1] == '-m' and argv[2] in {
                    'experiments.geographic_execution_plan.replay',
                    'experiments.geographic_joint_integration.replay'}))
            inherited = os.environ if env is None else env
            valid = valid and all(inherited.get(k) == os.environ.get(k) for k in
                ('GEO_JOINT_TEMP', 'GEO_JOINT_OUTPUT', 'GEO_OFFLINE_OUTPUT', 'PYTHONPATH'))
            if not valid:
                guard.block('process', argv)
            with (output/'allowed-children.jsonl').open('a') as stream:
                stream.write(json.dumps({'argv': list(argv), 'cwd': str(cwd), 'guards_inherited': True}) + '\n')
        elif event in {'os.system', 'os.posix_spawn', 'os.spawn', 'os.exec', 'os.fork', 'pty.spawn'}:
            guard.block('process', event)
        elif event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
            guard.block('network', event)
    sys.addaudithook(audit)
    return guard


class Tee(io.TextIOBase):
    def __init__(self, original, log): self.original, self.log = original, log
    def write(self, value):
        self.original.write(value); self.log.write(value); self.log.flush()
        return len(value)
    def flush(self): self.original.flush(); self.log.flush()


def worker(args):
    output = args.output_dir.resolve()
    from experiments.geographic_joint_integration.run_offline import bootstrap as installed_bootstrap
    guard = installed_bootstrap()
    assert guard is not None
    import pytest
    tests = args.tests or sorted(str(p.relative_to(ROOT)) for pattern in (
        'test_geographic_joint_*.py', 'test_geographic_execution_plan*.py', 'test_scoped_*.py')
        for p in (ROOT/'tests').glob(pattern))
    argv = ['-q', '-p', 'no:cacheprovider', '--basetemp='+str(Path(os.environ['GEO_JOINT_TEMP'])/'pytest'),
            '--junitxml='+str(output/'junit.xml'), *tests]
    write(output/'commands.json', {'python': sys.executable, 'cwd': str(ROOT), 'pytest_argv': argv,
          'guard_installed_before_collection': True, 'allowed_database_roots': [str(p) for p in guard.allowed_roots]})
    start = time.monotonic()
    with (output/'pytest.log').open('w') as log, contextlib.redirect_stdout(Tee(sys.stdout, log)), contextlib.redirect_stderr(Tee(sys.stderr, log)):
        code = int(pytest.main(argv))
    attempts = [json.loads(s) for s in (output/'guard-attempts.jsonl').read_text().splitlines()] if (output/'guard-attempts.jsonl').exists() else []
    unexpected = [e for e in attempts if not e['expected_probe']]
    counts = {}
    if (output/'junit.xml').exists():
        tree = ET.parse(output/'junit.xml')
        counts = {k: sum(int(n.get(k, 0)) for n in tree.iter('testsuite')) for k in ('tests', 'failures', 'errors', 'skipped')}
    write(output/'summary.json', {'pytest_exit_code': code, 'exit_code': code or int(bool(unexpected)),
        'elapsed_seconds': round(time.monotonic()-start, 2), 'pytest': counts,
        'guard_expected_probes': [e for e in attempts if e['expected_probe']], 'guard_unexpected_attempts': unexpected,
        'actual_network_model_collection_dispatch_succeeded': 0, 'fixture_only': True})
    return code or int(bool(unexpected))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('tests', nargs='*')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if not output.is_relative_to(EXPERIMENT/'artifacts') or Path.cwd() != ROOT:
        raise ValueError('explicit new joint artifacts output and repository cwd required')
    if Path(sys.executable).resolve() != PYTHON.resolve():
        raise ValueError('use existing verified interpreter')
    if args.worker:
        return worker(args)
    if output.exists():
        raise ValueError('fresh output directory required')
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='p234-offline-') as temporary:
        temp = Path(temporary)
        bootstrap_dir = temp/'bootstrap'; bootstrap_dir.mkdir()
        (bootstrap_dir/'sitecustomize.py').write_text('from experiments.geographic_joint_integration.run_offline import bootstrap\nbootstrap()\n')
        p2_output = temp/'p2-evidence'; p2_output.mkdir()
        env = dict(os.environ)
        env.update(GEO_JOINT_TEMP=temporary, GEO_JOINT_OUTPUT=str(output), GEO_OFFLINE_OUTPUT=str(p2_output),
            GEO_OFFLINE_TEMP_ROOT=temporary, PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
            XHS_AUDIT_DATA_DIR=str(temp/'data'), XHS_AUDIT_OUTPUTS_DIR=str(temp/'outputs'),
            HERMES_HOME=str(temp/'hermes'), APP_AUTH_MODE='disabled', APP_AUTH_COOKIE_SECURE='false',
            APP_AUTH_DB=str(temp/'data/auth.sqlite3'), REQUEST_SCHEDULER_DB=str(temp/'data/scheduler.sqlite3'))
        env['PYTHONPATH'] = os.pathsep.join([str(bootstrap_dir), str(ROOT), str(ROOT/'backend')])
        command = [sys.executable, '-c', 'from experiments.geographic_joint_integration.run_offline import main; raise SystemExit(main())', '--output-dir', str(output), '--worker', *args.tests]
        write(output/'invocation.json', {'command': command, 'parent_command': sys.argv, 'temp_root': temporary,
            'sitecustomize': (bootstrap_dir/'sitecustomize.py').read_text()})
        result = subprocess.run(command, env=env, cwd=ROOT, check=False)
        destination = output/'upstream-p2-test-evidence'; destination.mkdir()
        for p in p2_output.glob('*.json'):
            shutil.copyfile(p, destination/p.name)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
