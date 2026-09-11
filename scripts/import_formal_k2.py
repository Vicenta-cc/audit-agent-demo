"""Import only the verified K2 aggregate and immutable revisions, never task data."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.rulesets.compiler import content_hash, compile_ruleset_revision
from backend.rulesets.trial_profiles import K2_RULESET_ID, TRIAL_BUNDLES


def import_k2(source: Path, target: Path) -> dict:
    if source.resolve() == target.resolve():
        raise ValueError('Source and target must be separate databases')
    with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as src:
        src.row_factory = sqlite3.Row
        aggregate = src.execute('SELECT * FROM rule_sets WHERE id=?', (K2_RULESET_ID,)).fetchone()
        if not aggregate or aggregate['status'] != 'published':
            raise ValueError('Source has no published K2')
        revisions = src.execute('SELECT * FROM rule_set_revisions WHERE ruleset_id=? ORDER BY version', (K2_RULESET_ID,)).fetchall()
        pinned = content_hash(json.loads((TRIAL_BUNDLES[K2_RULESET_ID]/'ruleset.json').read_text()))
        if aggregate['draft_content_hash'] != pinned or content_hash(json.loads(aggregate['draft_content_json'])) != pinned:
            raise ValueError('Source aggregate does not match the pinned K2 bundle')
        for revision in revisions:
            if revision['content_hash'] != pinned:
                raise ValueError('Source revision does not match the pinned K2 bundle')
            compiled = compile_ruleset_revision({**dict(revision), **json.loads(revision['snapshot_json'])})
            assert compiled['prompt_profile_snapshot']['trial_profile']['id'] == K2_RULESET_ID
        if aggregate['published_revision_id'] not in {r['id'] for r in revisions}:
            raise ValueError('Source is missing its published revision')
    with sqlite3.connect(target.resolve().as_uri() + '?mode=rw', uri=True) as dst:
        dst.row_factory = sqlite3.Row
        dst.execute('PRAGMA foreign_keys=ON')
        dst.execute('BEGIN IMMEDIATE')
        existing = dst.execute('SELECT * FROM rule_sets WHERE id=?', (K2_RULESET_ID,)).fetchone()
        if existing:
            if dict(existing) != dict(aggregate):
                raise ValueError('Target K2 exists or was deleted; refusing to overwrite it')
            for row in revisions:
                found = dst.execute('SELECT * FROM rule_set_revisions WHERE id=?', (row['id'],)).fetchone()
                if found is None or dict(found) != dict(row):
                    raise ValueError('Target K2 revision differs; refusing to overwrite')
            imported = False
        else:
            for table, rows in [('rule_sets', [aggregate]), ('rule_set_revisions', revisions)]:
                for row in rows:
                    columns = list(row.keys())
                    dst.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(row))
            imported = True
    return {'ruleset_id': K2_RULESET_ID, 'content_hash': pinned, 'revision_count': len(revisions),
            'rule_count': sum(len(c['rules']) for c in json.loads(aggregate['draft_content_json'])['categories']),
            'imported': imported, 'source_db': str(source), 'target_db': str(target)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-db', type=Path, required=True)
    parser.add_argument('--target-db', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    result = import_k2(args.source_db, args.target_db)
    args.receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
