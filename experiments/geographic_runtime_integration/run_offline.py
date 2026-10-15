"""Guarded formal-runtime tests, including one explicitly allowed replay child."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = Path(__file__).parent / 'artifacts'
ACTIVE = None


def bootstrap():
    global ACTIVE
    if ACTIVE is not None or not os.environ.get('GEO_RUNTIME_TEMP'):
        return ACTIVE
    temporary = Path(os.environ['GEO_RUNTIME_TEMP']).resolve()
    output = Path(os.environ['GEO_RUNTIME_OUTPUT']).resolve()
    tempfile.tempdir = str(temporary)
    from experiments.geographic_execution_plan import offline_guards
    guard = offline_guards.OfflineGuards(experiment_root=output, event_log=output/'guard-attempts.jsonl')
    guard.allowed_roots = (temporary, output)
    guard.install()
    offline_guards.ACTIVE = ACTIVE = guard

    def audit(event, args):
        if event == 'subprocess.Popen':
            executable, argv, cwd, env = args
            inherited = os.environ if env is None else env
            valid = (isinstance(argv, (list, tuple)) and len(argv) == 4
                and Path(argv[0]).resolve() == Path(sys.executable).resolve()
                and argv[1] == '-m' and argv[2] in {
                    'experiments.geographic_runtime_integration.replay_delivery',
                    'experiments.geographic_runtime_integration.replay_gateway_budget'}
                and Path(argv[3]).resolve().is_relative_to(temporary)
                and all(inherited.get(k) == os.environ.get(k) for k in
                    ('GEO_RUNTIME_TEMP', 'GEO_RUNTIME_OUTPUT', 'GEO_OFFLINE_TEMP_ROOT', 'PYTHONPATH')))
            if not valid:
                guard.block('process', argv)
            with (output/'allowed-children.jsonl').open('a') as handle:
                handle.write(json.dumps({'argv':list(argv), 'guards_inherited':True})+'\n')
        elif event in {'os.system', 'os.posix_spawn', 'os.spawn', 'os.exec', 'os.fork', 'pty.spawn'}:
            guard.block('process', event)
        elif event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
            guard.block('network', event)
    sys.addaudithook(audit)
    return guard


def sources():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('backend', 'tests', 'experiments/geographic_runtime_integration')
            for p in (ROOT/folder).rglob('*.py') if '__pycache__' not in p.parts and 'artifacts' not in p.parts}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('tests', nargs='+')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if Path.cwd() != ROOT or not output.is_relative_to(ARTIFACTS):
        raise ValueError('repository cwd and explicit fresh runtime artifacts directory required')
    if args.worker:
        from experiments.geographic_runtime_integration.run_offline import bootstrap as installed_bootstrap
        assert installed_bootstrap() is not None
        import pytest
        before = sources()
        write(output/'source-before.json', before)
        started = time.monotonic()
        with (output/'pytest.log').open('w') as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            code = int(pytest.main(['-q','-p','no:cacheprovider',
                '--basetemp='+str(Path(os.environ['GEO_RUNTIME_TEMP'])/'pytest'),
                '--junitxml='+str(output/'junit.xml'), *args.tests]))
        after = sources()
        write(output/'source-after.json', after)
        events = [json.loads(s) for s in (output/'guard-attempts.jsonl').read_text().splitlines()] if (output/'guard-attempts.jsonl').exists() else []
        counts = {}
        if (output/'junit.xml').exists():
            tree=ET.parse(output/'junit.xml')
            counts={key:sum(int(n.get(key,0)) for n in tree.iter('testsuite')) for key in ('tests','failures','errors','skipped')}
        result={'pytest_exit_code':code, 'exit_code':code or int(bool(events) or before!=after),
                'elapsed_seconds':round(time.monotonic()-started,2),'pytest':counts,
                'source_unchanged':before==after,'guard_attempts':events,
                'network_model_collection_dispatch_succeeded':0}
        write(output/'summary.json',result)
        print(json.dumps(result,ensure_ascii=False))
        return result['exit_code']
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='geographic-runtime-') as temp:
        temporary=Path(temp)
        site=temporary/'bootstrap';site.mkdir()
        (site/'sitecustomize.py').write_text('from experiments.geographic_runtime_integration.run_offline import bootstrap\nbootstrap()\n')
        env={**os.environ, 'GEO_RUNTIME_TEMP':temp, 'GEO_RUNTIME_OUTPUT':str(output),
            'GEO_OFFLINE_TEMP_ROOT':temp,'GEO_OFFLINE_OUTPUT':str(output),
            'PYTHONPATH':os.pathsep.join((str(site),str(ROOT),str(ROOT/'backend'))),
            'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','PYTHONDONTWRITEBYTECODE':'1',
            'XHS_AUDIT_DATA_DIR':str(temporary/'data'),'XHS_AUDIT_OUTPUTS_DIR':str(temporary/'outputs'),
            'HERMES_HOME':str(temporary/'hermes'),'APP_AUTH_MODE':'disabled','APP_AUTH_COOKIE_SECURE':'false',
            'APP_AUTH_DB':str(temporary/'data/auth.sqlite3'),'REQUEST_SCHEDULER_DB':str(temporary/'data/scheduler.sqlite3')}
        command=[sys.executable,'-m','experiments.geographic_runtime_integration.run_offline',
                 '--worker','--output-dir',str(output),*args.tests]
        write(output/'commands.json',{'command':command,'cwd':str(ROOT),'guard_installed_before_collection':True,
            'allowed_database_roots':[temp,str(output)],'bootstrap':(site/'sitecustomize.py').read_text()})
        return subprocess.run(command,cwd=ROOT,env=env,check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
