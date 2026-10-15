"""Read-only fixed source inventory and scoped change audit."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT/'experiments/geographic_joint_integration/artifacts/joint-20261015-01/INPUT_MANIFEST.json'


def audit():
    manifest = json.loads(MANIFEST.read_bytes())
    def git(*argv): return subprocess.check_output(['git', *argv], cwd=ROOT)
    assert git('rev-parse', 'HEAD').decode().strip() == manifest['application_base']
    for tag, expected in [('checkpoint/geographic-relevance-p0p1-final-20261015', manifest['application_base']),
                          (manifest['p3_tag'], manifest['p3_commit'])]:
        assert git('rev-parse', tag+'^{commit}').decode().strip() == expected
    tracked = git('ls-tree', '-rz', 'HEAD').split(b'\0')
    count = 0
    imported = {row['target_path'] for row in manifest['files']}
    for entry in tracked:
        if not entry: continue
        meta, raw_path = entry.split(b'\t', 1)
        mode, kind, blob = meta.split()
        p = ROOT/os.fsdecode(raw_path)
        data = os.fsencode(os.readlink(p)) if mode == b'120000' else p.read_bytes()
        assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() == blob.decode(), str(p)
        count += 1
    for row in manifest['files']:
        data = (ROOT/row['target_path']).read_bytes()
        assert len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256'], row['target_path']
        source = Path(row['source_root'])/row['source_path']
        assert source.read_bytes() == data, 'source changed: '+str(source)
    p3_count = 0
    p2 = ROOT/'experiments/geographic_execution_plan/dependencies/p3/import_manifest.json'
    for row in json.loads(p2.read_text())['files']:
        data = git('show', manifest['p3_commit']+':'+row['source_path'])
        assert hashlib.sha256(data).hexdigest() == row['file_bytes_sha256']
        assert (ROOT/row['target_path']).read_bytes() == data
        p3_count += 1
    p4 = ROOT/'experiments/geographic_selection/dependencies/IMPORT_MANIFEST.json'
    for row in json.loads(p4.read_text())['exports']:
        data = git('show', manifest['p3_commit']+':'+row['source'])
        assert (p4.parent.parent/row['destination']).read_bytes() == data
        p3_count += 1
    untracked = [os.fsdecode(p) for p in git('ls-files', '--others', '--exclude-standard', '-z').split(b'\0') if p]
    new = []
    for path in untracked:
        if path in imported: continue
        assert path.startswith('experiments/geographic_joint_integration/') or (
            path.startswith('tests/test_geographic_joint_') and path.endswith('.py')), path
        new.append(path)
    assert not git('diff', '--name-only', 'HEAD').strip()
    subprocess.run(['git', 'diff', '--check'], cwd=ROOT, check=True)
    for path in new:
        p = ROOT/path
        if p.suffix == '.py': ast.parse(p.read_bytes())
        if p.suffix in {'.py', '.md'}:
            result = subprocess.run(['git', 'diff', '--no-index', '--check', '/dev/null', str(p)], capture_output=True, text=True)
            assert not result.stdout and not result.stderr, (path, result.stdout, result.stderr)
    return {'head': manifest['application_base'], 'branch': git('branch', '--show-current').decode().strip(),
        'p1_files_unchanged': count, 'imported_files_unchanged': len(imported), 'upstream_source_trees_match_snapshot': True,
        'p3_git_exports_identical': p3_count, 'new_files': new, 'syntax_and_whitespace': 'passed',
        'lint_typecheck': 'not configured; no dependency installation',
        'p4_original_session_reacceptance': 'pending; user authorized offline continuation'}


if __name__ == '__main__':
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
