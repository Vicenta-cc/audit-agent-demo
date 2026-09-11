"""Clone a quiescent managed runtime into a new, independently writable environment.

SQLite uses its online backup API. macOS media copies use APFS copy-on-write,
not hard links. Never overwrite an existing runtime or modify the source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def assert_quiescent(data):
    checks = (
        ('audit_index.sqlite3', 'jobs', "status NOT IN ('completed','failed','cancelled','archived')"),
        ('investigation_creation.sqlite3', 'investigation_runs', "status NOT IN ('PUBLISHED','FAILED','CANCELLED')"),
        ('investigation.sqlite3', 'investigation_turns', "status NOT IN ('completed','failed','cancelled')"),
    )
    for filename, table, condition in checks:
        with sqlite3.connect((data / filename).as_uri() + '?mode=ro', uri=True) as db:
            if db.execute(f'SELECT count(*) FROM {table} WHERE {condition}').fetchone()[0]:
                raise RuntimeError(f'Source is busy: {filename}/{table}; finish its active work before snapshotting')


def copy_tree(source, target):
    if sys.platform == 'darwin':
        subprocess.run(['/bin/cp', '-cR', str(source), str(target)], check=True)
    else:
        shutil.copytree(source, target, symlinks=True)


def snapshot(source, target):
    assert_quiescent(source / 'data')
    receipt = {'created_at': datetime.now(timezone.utc).isoformat(), 'source': str(source),
               'copy_semantics': 'independent files; SQLite online backup', 'databases': {}, 'archives': {}}
    for name in ('data', 'archives', 'crawler'):
        copy_tree(source / name, target / name)
    for path in sorted((source / 'data').glob('*.sqlite3')):
        destination = target / 'data' / path.name
        temporary = destination.with_suffix('.backup')
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(temporary) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError(f'Invalid database snapshot: {path.name}')
        # Cloned WAL files belong to the pre-backup image, never the new image.
        for suffix in ('-wal', '-shm'):
            destination.with_name(destination.name + suffix).unlink(missing_ok=True)
        temporary.replace(destination)
        receipt['databases'][path.name] = {'sha256': digest(destination), 'bytes': destination.stat().st_size}
    for path in sorted((target / 'archives').glob('*.sqlite3')):
        expected = digest(source / 'archives' / path.name)
        if digest(path) != expected:
            raise RuntimeError(f'Archive checksum mismatch: {path.name}')
        receipt['archives'][path.name] = expected
    assert_quiescent(target / 'data')
    assert_quiescent(source / 'data')
    return receipt


def copy_model_configuration(source, target):
    from dotenv import dotenv_values, set_key
    model = {**json.loads((source / 'environment.json').read_text()), **dotenv_values(source / 'secrets.env')}
    if not model.get('DASHSCOPE_API_KEY') or not model.get('DASHSCOPE_BASE_URL'):
        raise RuntimeError('Model source must provide a key and endpoint')
    environment = json.loads((target / 'environment.json').read_text())
    environment.pop('DASHSCOPE_API_KEY', None)
    environment['DASHSCOPE_BASE_URL'] = model['DASHSCOPE_BASE_URL']
    (target / 'environment.json').write_text(json.dumps(environment, indent=2))
    set_key(str(target / 'secrets.env'), 'DASHSCOPE_API_KEY', model['DASHSCOPE_API_KEY'])
    set_key(str(target / 'secrets.env'), 'DASHSCOPE_BASE_URL', model['DASHSCOPE_BASE_URL'])
    (target / 'receipts/model-configuration.json').write_text(json.dumps({
        'source_runtime': str(source), 'endpoint': model['DASHSCOPE_BASE_URL'],
        'credentials': 'private secrets.env; values omitted'}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--model-runtime', type=Path, help='Use the model credential and endpoint from this explicit runtime')
    parser.add_argument('--environment-id', default='xhs-audit-formal-baseline-20260911')
    parser.add_argument('--frontend-port', type=int, default=3198)
    parser.add_argument('--api-port', type=int, default=8198)
    args = parser.parse_args()
    source, target = args.source.expanduser().resolve(), args.runtime.expanduser().resolve()
    if source == target or source in target.parents or target == ROOT or ROOT in target.parents:
        parser.error('Use a new runtime directory outside the source and code directories')
    if not args.environment_id.startswith('xhs-audit-formal'):
        parser.error('Formal environment ID must start with xhs-audit-formal')
    if args.frontend_port == args.api_port or {args.frontend_port, args.api_port} & {3188,8188,3178,8178,3148,8148,3151,8151,3168,8168,8000,3128,8127,8147,8149}:
        parser.error('Choose distinct ports outside the existing environments')
    for port in (args.frontend_port, args.api_port):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    if target.exists() and any(p.name != 'receipts' for p in target.iterdir()):
        parser.error('Target already contains runtime files; refusing to overwrite')
    os.umask(0o077)
    target.mkdir(parents=True, exist_ok=True)
    receipt = snapshot(source, target)
    config = json.loads((source / 'config.json').read_text())
    config.update(environment_id=args.environment_id, worktree=str(ROOT), data_dir=str(target / 'data'),
                  crawler_dir=str(target / 'crawler'), frontend_port=args.frontend_port, api_port=args.api_port)
    (target / 'config.json').write_text(json.dumps(config, indent=2))
    # Keep credentials private. configure() supplies authoritative storage paths.
    environment = json.loads((source / 'environment.json').read_text())
    for key in ('XHS_AUDIT_DATA_DIR', 'XHS_AUDIT_OUTPUTS_DIR', 'MEDIACRAWLER_DIR', 'CRAWLER_LOGIN_PYTHON',
                'HISTORICAL_REPORT_A_DB', 'HISTORICAL_REPORT_B_DB', 'HISTORICAL_REPORT_C_MANIFEST', 'CRAWLER_AUTH_KEY_FILE'):
        environment.pop(key, None)
    environment['M3_COMMENTS_PER_POST'] = '3'
    (target / 'environment.json').write_text(json.dumps(environment, indent=2))
    shutil.copyfile(source / 'secrets.env', target / 'secrets.env')
    (target / 'secrets.env').chmod(0o600)
    for path in (target / 'archives').glob('*manifest.json'):
        content = json.loads(path.read_text())
        saved = content.get('source_database')
        if saved:
            archive = target / 'archives' / Path(saved).name
            if not archive.is_file() or digest(archive) != content['source_database_sha256']:
                raise RuntimeError(f'Manifest archive mismatch: {path.name}')
            content['source_database'] = str(archive)
        path.write_text(json.dumps(content, ensure_ascii=False, indent=2))
    (target / 'logs').mkdir()
    (target / 'receipts').mkdir(exist_ok=True)
    if args.model_runtime:
        copy_model_configuration(args.model_runtime.expanduser().resolve(), target)
    (target / 'receipts/runtime-snapshot.json').write_text(json.dumps(receipt, indent=2))
    (target / 'initialized.json').write_text(json.dumps({'source': str(source), 'method': 'verified runtime snapshot'}))
    print(json.dumps({'runtime': str(target), 'frontend': args.frontend_port, 'api': args.api_port,
                      'databases': len(receipt['databases']), 'archives': len(receipt['archives'])}))


if __name__ == '__main__':
    main()
