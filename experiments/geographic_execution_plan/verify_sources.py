"""Read-only Git/source boundary audit; writes only the requested experiment JSON."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import py_compile
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT/'experiments/geographic_execution_plan'
P1 = '031e0cd26379ee44c2372c9a28536c377ab3d4b9'
P3 = '2120c53379d1b79c45e3cb8232b9c9b96657b183'
TAGS = {'checkpoint/geographic-relevance-p0p1-final-20261015':P1,
        'p3-post-only-names-v2-local-20261015':P3}


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def audit():
    tags = {tag:git('rev-parse', tag+'^{commit}').decode().strip() for tag in TAGS}
    assert tags == TAGS
    assert git('rev-parse','HEAD').decode().strip() == P1
    assert not git('diff','--name-only',P1,'--').strip(), 'original tracked files changed'
    baseline = json.loads((EXPERIMENT/'artifacts/baseline-files.json').read_text())
    changed = [path for path, digest in baseline.items() if not (ROOT/path).is_file()
               or sha((ROOT/path).read_bytes()) != digest]
    assert not changed, changed
    imports = []
    for relative in git('ls-files','backend').decode().splitlines():
        if not relative.endswith('.py'):
            continue
        tree = ast.parse((ROOT/relative).read_text(encoding='utf-8'), filename=relative)
        for node in ast.walk(tree):
            modules = ([a.name for a in node.names] if isinstance(node, ast.Import)
                       else [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
            if any('geographic_execution_plan' in name or 'scope_search_plan' in name for name in modules):
                imports.append({'path':relative, 'line':node.lineno, 'modules':modules})
    assert not imports, imports
    manifest = json.loads((EXPERIMENT/'dependencies/p3/import_manifest.json').read_text())
    imported = []
    for row in manifest['files']:
        data = (ROOT/row['target_path']).read_bytes()
        assert sha(data) == row['file_bytes_sha256'] and len(data) == row['bytes']
        assert data == git('show',P3+':'+row['source_path'])
        imported.append({'path':row['target_path'], 'sha256':sha(data), 'bytes':len(data)})
    new_files = git('ls-files','--others','--exclude-standard').decode().splitlines()
    def allowed(path):
        return (path == 'backend/investigation_creation/scope_search_plan.py'
            or path.startswith('experiments/geographic_execution_plan/')
            or path.startswith('tests/test_geographic_execution_plan') and path.endswith('.py'))
    assert all(allowed(path) for path in new_files), 'new file outside allowed boundary'
    authored = [ROOT/'backend/investigation_creation/scope_search_plan.py',
        *EXPERIMENT.glob('*.py'), *EXPERIMENT.glob('*.md'),
        *(ROOT/'tests').glob('test_geographic_execution_plan*.py')]
    whitespace = []
    with tempfile.TemporaryDirectory(prefix='geo-source-compile-') as temporary:
        for index, path in enumerate(authored):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if line.rstrip() != line:
                    whitespace.append({'file':str(path.relative_to(ROOT)), 'line':number})
            if path.suffix == '.py':
                py_compile.compile(str(path), cfile=str(Path(temporary)/f'{index}.pyc'), doraise=True)
    assert not whitespace, whitespace
    subprocess.run(['git','diff','--check'], cwd=ROOT, check=True)
    return dict(schema_version='p2-source-boundary-audit-v1', tags=tags, head=P1,
        branch=git('branch','--show-current').decode().strip(), original_tracked_file_count=len(baseline),
        original_changed_files=changed, tracked_diff_empty=True, p3_imports=imported,
        production_imports_of_new_plan_or_experiment=imports, new_files=new_files,
        authored_files=[dict(path=str(p.relative_to(ROOT)),sha256=sha(p.read_bytes())) for p in sorted(authored)],
        static_compile='passed', whitespace='passed', git_diff_check='passed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(EXPERIMENT) or output.suffix != '.json':
        raise ValueError('audit output must be experiment JSON')
    result = audit()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('head','branch','original_tracked_file_count',
        'original_changed_files','static_compile','whitespace')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
