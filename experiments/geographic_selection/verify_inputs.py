"""Read-only, reproducible verification of pinned Git blobs and local snapshots."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess


def verify():
    directory = Path(__file__).resolve().parent
    root = directory.parents[1]
    pins = json.loads((directory/'pinned_docs/geographic-relevance-p4-pinned-inputs-v1.json').read_bytes())
    count = 0
    for source in ('p1', 'p3'):
        pin = pins[source]
        def git(*args):
            return subprocess.check_output(['git', '-C', str(root), *args])
        assert git('rev-parse', pin['tag']+'^{commit}').decode().strip() == pin['commit']
        assert git('rev-parse', pin['commit']+'^{tree}').decode().strip() == pin['tree']
        for entry in pin['files']:
            data = git('show', pin['commit']+':'+entry['path'])
            assert len(data) == entry['bytes']
            assert hashlib.sha256(data).hexdigest() == entry['file_bytes_sha256']
            if source == 'p1':
                assert (root/entry['path']).read_bytes() == data
            count += 1
    manifest = json.loads((directory/'dependencies/IMPORT_MANIFEST.json').read_text())
    for entry in manifest['exports']:
        data = (directory/entry['destination']).read_bytes()
        assert len(data) == entry['bytes']
        assert hashlib.sha256(data).hexdigest() == entry['sha256']
    for entry in pins['documentation_observation']['files']:
        assert hashlib.sha256(Path(entry['path']).read_bytes()).hexdigest() == entry['file_bytes_sha256']
    inventory = json.loads((directory/'preflight.json').read_text())
    for entry in inventory['inventory']:
        if entry['source'] == 'planning_observation':
            data = (directory/'pinned_docs'/entry['path']).read_bytes()
            assert len(data) == entry['bytes']
            assert hashlib.sha256(data).hexdigest() == entry['file_bytes_sha256']
    return {'verified_git_files': count, 'verified_exports': len(manifest['exports']),
            'tag_commit_tree_matches': True, 'p1_readonly_files_unchanged': True,
            'doc_observation_matches': True, 'pinned_docs_unchanged': True}

if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
