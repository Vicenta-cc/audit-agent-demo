"""Maintainer-only: export the two published reports, never a runtime database."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.historical_reports.importer import _REPORT_COPY_PLAN


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export(source, destination, task, version):
    with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(destination) as dst:
        for table, where, kind in _REPORT_COPY_PLAN:
            schema = src.execute('SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone()[0]
            dst.execute(schema)
            rows = src.execute(f'SELECT * FROM "{table}" WHERE {where} ORDER BY rowid', (task if kind == 'task_id' else version,)).fetchall()
            if rows:
                dst.executemany(f'INSERT INTO "{table}" VALUES ({",".join("?" for _ in rows[0])})', rows)
        assert dst.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        record = dst.execute('SELECT content_hash FROM report_versions WHERE id=?', (version,)).fetchone()
        snapshot = dst.execute('SELECT snapshot_hash FROM report_source_snapshots WHERE report_version_id=?', (version,)).fetchone()
    return {'task_id': task, 'report_version_id': version, 'report_content_hash': record[0], 'snapshot_hash': snapshot[0], 'database_sha256': sha(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-a', type=Path, required=True)
    parser.add_argument('--report-b', type=Path, required=True)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'demo' / 'seed')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {'format': 1, 'scope': 'Published A/B report tables and their public-evidence account corpus; no crawler accounts, credentials or task queues.', 'reports': {}, 'files': {}}
    with tempfile.TemporaryDirectory() as temporary:
        for label, source, task, version in (
            ('a', args.report_a, '3ad102e072f6', 'report-version:8c355a5ba03f45619795813af83ac669'),
            ('b', args.report_b, '8bc179209e1e', 'report-version:4e3ebeccd2ed4f0c9c9a750332d22585'),
        ):
            database = Path(temporary) / f'report-{label}.sqlite3'
            manifest['reports'][label] = export(source, database, task, version)
            packed = args.output / f'report-{label}.sqlite3.gz'
            packed.write_bytes(gzip.compress(database.read_bytes(), mtime=0))
            manifest['files'][packed.name] = sha(packed)
    target = args.output / 'account_m22_corpus.json.gz'
    shutil.copyfile(args.corpus, target)
    manifest['files'][target.name] = sha(target)
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({name: (args.output / name).stat().st_size for name in manifest['files']}))

if __name__ == '__main__':
    main()
