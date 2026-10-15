"""Reproducible guarded offline suite; launch with an explicit experiment output."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
import tarfile
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[2]
P1_COMMIT='031e0cd26379ee44c2372c9a28536c377ab3d4b9'
BASELINE_NODES=(
    'tests/test_search_terms_cap.py::test_prompts_are_formatted_with_the_setting[10]',
    'tests/test_search_terms_cap.py::test_prompts_are_formatted_with_the_setting[6]',
    'tests/test_search_terms_cap.py::test_task_creation_caps_search_terms_for_every_source[formal-temporary]',
    'tests/test_search_terms_cap.py::test_task_creation_caps_search_terms_for_every_source[temporary-temporary]',
)
REGRESSIONS=(
    'test_geographic_draft_configuration.py','test_geographic_scope_catalog.py',
    'test_geographic_national_catalog.py','test_geographic_scope_context.py',
    'test_geographic_scope_selection.py','test_geographic_scope_selection_history.py',
    'test_geographic_scope_intent.py','test_unified_draft_configuration.py',
    'test_frozen_workspace_configuration.py','test_temporary_ruleset_execution.py',
    'test_search_terms_cap.py','test_investigation_draft_configuration_m3.py','test_task_settings.py',
    'test_triage_engine.py','test_triage_matcher.py',
)

def write(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def worker(output, *, new_tests_only=False):
    from .offline_guards import install_from_environment
    guard=install_from_environment()
    assert guard is not None, 'guards must precede pytest import/collection'
    from .p3_bridge import verify_dependencies
    manifest=verify_dependencies()
    tests=sorted(str(p.relative_to(ROOT)) for p in (ROOT/'tests').glob('test_geographic_execution_plan*.py'))
    for name in (('test_investigation_draft_configuration_m3.py',) if new_tests_only else REGRESSIONS):
        path=ROOT/'tests'/name
        if not path.is_file(): raise ValueError('MISSING_REQUIRED_REGRESSION:'+name)
        tests.append('tests/'+name)
    selection=['-k','geographic_execution_plan'] if new_tests_only else []
    commands={'python':sys.executable,'cwd':str(ROOT),
        'pytest_argv':[*tests,*selection,'-q','--junitxml='+str(output/'junit.xml'),'-p','no:cacheprovider'],
        'dependency_verification':'verify_dependencies(): exact pinned manifest and 11 file byte digests',
        'static_check':'py_compile new scope_search_plan, experiment and geographic_execution_plan test Python files',
        'guard_installation':'before importing pytest; inherited sitecustomize for Python children',
        'allowed_sqlite_roots':[str(p) for p in guard.allowed_roots],
        'frozen_test_temp_root':os.environ.get('GEO_OFFLINE_TEMP_ROOT'),
        'selection':'all new tests; M3 imported during collection for environment regression' if new_tests_only else 'all new tests and targeted regressions'}
    write(output/'commands.json',commands)
    files=[ROOT/'backend/investigation_creation/scope_search_plan.py',
           *sorted((ROOT/'experiments/geographic_execution_plan').glob('*.py')),
           *sorted((ROOT/'tests').glob('test_geographic_execution_plan*.py'))]
    with tempfile.TemporaryDirectory(prefix='geo-pycompile-') as compiled:
        for index,path in enumerate(files):
            py_compile.compile(str(path),cfile=str(Path(compiled)/str(index))+'.pyc',doraise=True)
    import pytest
    with (output/'pytest.log').open('w',encoding='utf-8') as stream, contextlib.redirect_stdout(stream),contextlib.redirect_stderr(stream):
        code=int(pytest.main(commands['pytest_argv']))
    counts={}
    if (output/'junit.xml').exists():
        suites=ET.parse(output/'junit.xml').getroot()
        for key in ('tests','failures','errors','skipped'):
            counts[key]=sum(int(s.attrib.get(key,0)) for s in suites.iter('testsuite'))
    attempts=[json.loads(line) for line in (output/'guard-attempts.jsonl').read_text().splitlines()] if (output/'guard-attempts.jsonl').exists() else []
    write(output/'summary.json',{'schema_version':'p2-offline-verification-v1','fixture_only':True,
        'pytest_exit_code':code,'pytest':counts,'compiled_files':len(files),'compile_status':'passed',
        'p3_dependencies_verified':len(manifest['files']),'guards_installed_before_collection':True,
        'guard_attempts_blocked':len(attempts),
        'blocked_by_kind':{kind:sum(a['kind']==kind for a in attempts) for kind in sorted({a['kind'] for a in attempts})},
        'actual_network_model_collection_dispatch_succeeded':0,
        'sqlite_scope':'memory, system temporary directories, explicit experiment output only',
        'limits':['Accidental-side-effect guards, not a hostile-code sandbox.',
                  'Artificial responses do not establish model semantic quality.',
                  'Python children inherit guards; no production service or task is launched.']})
    return code

def baseline(output, reference, env):
    """Read exact P1 objects into temporary files; use only P2 guard infrastructure."""
    commit=subprocess.check_output(['git','rev-parse',reference+'^{commit}'],cwd=ROOT,text=True).strip()
    if commit!=P1_COMMIT: raise ValueError('BASELINE_MUST_BE_EXACT_FINAL_P1')
    archive_command=['git','archive',commit,'backend','tests']
    archive=subprocess.check_output(archive_command,cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix='geo-p1-baseline-') as directory:
        baseline_root=Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(baseline_root,filter='data')
        # P1 application and test modules precede P2; P2 supplies only guard bootstrap.
        env=dict(env)
        env['PYTHONPATH']=os.pathsep.join((str(output/'bootstrap'),str(baseline_root),
            str(baseline_root/'backend'),str(ROOT)))
        nodes=[*BASELINE_NODES,'-q','--junitxml='+str(output/'junit.xml'),'-p','no:cacheprovider']
        script=("import pathlib,sys; import backend; "
            "assert pathlib.Path(backend.__file__).resolve().is_relative_to(pathlib.Path.cwd()); "
            "from experiments.geographic_execution_plan.offline_guards import ACTIVE; assert ACTIVE is not None; "
            "import pytest; raise SystemExit(pytest.main("+repr(nodes)+"))")
        command=[sys.executable,'-c',script]
        write(output/'commands.json',{'python':sys.executable,'baseline_ref':reference,'baseline_commit':commit,
            'archive_command':archive_command,'archive_sha256':hashlib.sha256(archive).hexdigest(),
            'archive_scope':['backend','tests'],'cwd':str(baseline_root),'pytest_argv':nodes,
            'application_code_source':'all backend/ files from exact P1 git archive; no P2 backend path',
            'test_file_sha256':hashlib.sha256((baseline_root/'tests/test_search_terms_cap.py').read_bytes()).hexdigest(),
            'p2_infrastructure_only':['offline_guards.py','generated sitecustomize bootstrap']})
        with (output/'pytest.log').open('w',encoding='utf-8') as stream:
            result=subprocess.run(command,cwd=baseline_root,env=env,stdout=stream,stderr=subprocess.STDOUT,check=False)
    counts={}
    if (output/'junit.xml').exists():
        suites=ET.parse(output/'junit.xml').getroot()
        counts={key:sum(int(s.attrib.get(key,0)) for s in suites.iter('testsuite'))
                for key in ('tests','failures','errors','skipped')}
    attempts=[json.loads(line) for line in (output/'guard-attempts.jsonl').read_text().splitlines()] if (output/'guard-attempts.jsonl').exists() else []
    write(output/'summary.json',{'schema_version':'p2-p1-baseline-verification-v1','baseline_commit':commit,
        'pytest_exit_code':result.returncode,'pytest':counts,'guards_installed_before_collection':True,
        'guard_attempts_blocked':len(attempts),'actual_network_model_collection_dispatch_succeeded':0,
        'note':'Expected failures remain failures; this run does not modify or xfail P1 tests.'})
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True,type=Path)
    parser.add_argument('--new-tests-only',action='store_true',help='Run every new test while collecting M3 to retain its environment side effects')
    parser.add_argument('--baseline-ref',help='Run the four observed legacy failures against exact final P1 git objects')
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(); output=args.output_dir.resolve()
    experiment=ROOT/'experiments/geographic_execution_plan'
    if not output.is_relative_to(experiment): raise ValueError('OUTPUT_MUST_BE_INSIDE_EXPERIMENT')
    output.mkdir(parents=True,exist_ok=True)
    if args.worker:
        try: return worker(output,new_tests_only=args.new_tests_only)
        except Exception:
            import traceback
            (output/'pytest.log').write_text(traceback.format_exc(),encoding='utf-8')
            write(output/'summary.json',{'schema_version':'p2-offline-verification-v1',
                'fixture_only':True,'status':'preflight_failed','pytest_exit_code':2})
            return 2
    if any((output/name).exists() for name in ('summary.json','pytest.log','guard-attempts.jsonl')):
        raise ValueError('USE_A_FRESH_OUTPUT_DIRECTORY')
    bootstrap=output/'bootstrap'; bootstrap.mkdir()
    (bootstrap/'sitecustomize.py').write_text('from experiments.geographic_execution_plan.offline_guards import install_from_environment\ninstall_from_environment()\n',encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='geo-offline-run-') as temporary:
        env=dict(os.environ)
        env.update(GEO_OFFLINE_OUTPUT=str(output),GEO_OFFLINE_TEMP_ROOT=temporary,PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONDONTWRITEBYTECODE='1',
            XHS_AUDIT_DATA_DIR=str(Path(temporary)/'data'),XHS_AUDIT_OUTPUTS_DIR=str(Path(temporary)/'outputs'),
            HERMES_HOME=str(Path(temporary)/'hermes'),APP_AUTH_MODE='disabled',APP_AUTH_COOKIE_SECURE='false')
        env['PYTHONPATH']=os.pathsep.join((str(bootstrap),str(ROOT),str(ROOT/'backend')))
        command=[sys.executable,'-m','experiments.geographic_execution_plan.run_offline','--output-dir',str(output),'--worker']
        if args.new_tests_only: command.append('--new-tests-only')
        result=baseline(output,args.baseline_ref,env) if args.baseline_ref else subprocess.run(command,cwd=ROOT,env=env,check=False)
    if (output/'summary.json').exists(): print((output/'summary.json').read_text())
    return result.returncode

if __name__=='__main__': raise SystemExit(main())
