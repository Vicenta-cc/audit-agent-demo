"""Prepare (never install) a checked history merge in a new staging directory.

Sources and the current candidate remain untouched. Immutable report payloads
and externally referenced IDs are retained; execution is held on imported runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def tables(connection):
    return [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )]


def rows(connection, table, column=None, values=None):
    if column is None:
        return [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]
    if not values:
        return []
    values = tuple(values)
    return [dict(row) for row in connection.execute(
        f'SELECT * FROM "{table}" WHERE "{column}" IN ({",".join("?" for _ in values)})', values
    )]


def backup(source, target):
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


class Merger:
    def __init__(self, destination, source_name):
        self.destination = destination
        self.source_name = source_name
        self.receipts = []

    def insert(self, connection, table, records, *, regenerate_id=False, keep_existing=False):
        if not records:
            return
        info = list(connection.execute(f'PRAGMA table_info("{table}")'))
        if not info:
            raise RuntimeError(f"Missing target schema: {table}")
        columns = {item[1] for item in info}
        primary = [item[1] for item in sorted(info, key=lambda item: item[5]) if item[5]]
        inserted = kept = 0
        id_map = {}
        for record in records:
            record = dict(record)
            assert set(record) <= columns, (table, set(record) - columns)
            old_id = record.pop("id") if regenerate_id else None
            existing = None
            if not regenerate_id and primary:
                query = " AND ".join(f'"{key}"=?' for key in primary)
                existing = connection.execute(f'SELECT * FROM "{table}" WHERE {query}', tuple(record[k] for k in primary)).fetchone()
            if existing is not None:
                if not keep_existing and any(existing[k] != v for k, v in record.items()):
                    raise RuntimeError(f"Conflicting record: {self.source_name}/{table}/{[record[k] for k in primary]}")
                kept += 1
                continue
            names = ",".join(f'"{name}"' for name in record)
            cursor = connection.execute(f'INSERT INTO "{table}" ({names}) VALUES ({",".join("?" for _ in record)})', tuple(record.values()))
            if regenerate_id:
                id_map[str(old_id)] = cursor.lastrowid
            inserted += 1
        self.receipts.append({"source": self.source_name, "table": table, "inserted": inserted,
                              "existing_retained": kept, "regenerated_local_ids": id_map})

    def merge(self, source, *, extra_jobs=()):
        creation_source = connect(source / "investigation_creation.sqlite3")
        conversation_source = connect(source / "investigation.sqlite3")
        audit_source = connect(source / "audit_index.sqlite3")
        runs = rows(creation_source, "investigation_runs")
        jobs = {run["job_id"] for run in runs if run["job_id"]} | set(extra_jobs)
        sessions = {item["id"] for item in rows(conversation_source, "investigation_sessions")
                    if item["scope_type"] == "creation" or item["anchor_key"].startswith("m3-run:")}
        # Empty legacy report anchors remain in the source archive; the visible
        # creation conversations and their published follow-up sessions migrate.
        turns = {item["id"] for item in rows(conversation_source, "investigation_turns", "session_id", sessions)}
        with connect(self.destination / "investigation.sqlite3") as dst:
            for table in tables(conversation_source):
                columns = {row[1] for row in conversation_source.execute(f'PRAGMA table_info("{table}")')}
                if table == "investigation_sessions":
                    selected = rows(conversation_source, table, "id", sessions)
                elif "session_id" in columns:
                    selected = rows(conversation_source, table, "session_id", sessions)
                elif "turn_id" in columns:
                    selected = rows(conversation_source, table, "turn_id", turns)
                else:
                    selected = []
                    if rows(conversation_source, table):
                        raise RuntimeError(f"Unclassified conversation dependency: {table}")
                self.insert(dst, table, selected)
        with connect(self.destination / "investigation_creation.sqlite3") as dst:
            for table in tables(creation_source):
                self.insert(dst, table, rows(creation_source, table))
            for run in runs:
                dst.execute("INSERT INTO investigation_run_execution_holds(run_id,source_name,reason) VALUES (?,?,?)",
                            (run["id"], self.source_name, "迁入历史记录；原任务不自动恢复，执行归属另行核对"))
        with connect(self.destination / "audit_index.sqlite3") as dst:
            task_contents = rows(audit_source, "task_contents", "task_id", jobs)
            content_ids = {item["content_id"] for item in task_contents}
            content_map = {}
            for content in rows(audit_source, "contents", "id", content_ids):
                old_id = content['id']
                existing = dst.execute('SELECT id FROM contents WHERE platform=? AND content_key=?',
                                       (content['platform'], content['content_key'])).fetchone()
                if existing:
                    content_map[old_id] = existing[0]
                    continue
                if dst.execute('SELECT 1 FROM contents WHERE id=?', (old_id,)).fetchone():
                    content['id'] = dst.execute('SELECT coalesce(max(id),0)+1 FROM contents').fetchone()[0]
                content_map[old_id] = content['id']
                self.insert(dst, 'contents', [content])
            self.receipts.append({'source': self.source_name, 'content_id_map': content_map,
                                  'deduplication_key': ['platform', 'content_key']})
            for table, key in [("jobs", "id"), ("task_audit_config_revisions", "job_id"),
                               ("audit_results", "job_id"), ("task_contents", "task_id")]:
                selected = rows(audit_source, table, key, jobs)
                for record in selected:
                    if record.get('content_id') is not None:
                        record['content_id'] = content_map[record['content_id']]
                self.insert(dst, table, selected)
            for table, key in [("job_logs", "job_id"), ("ingest_batches", "task_id"), ("content_matches", "task_id")]:
                selected = rows(audit_source, table, key, jobs)
                for record in selected:
                    if record.get('content_id') is not None:
                        record['content_id'] = content_map[record['content_id']]
                self.insert(dst, table, selected, regenerate_id=True)

            # Existing formal resources win on shared IDs. Source resource rows
            # remain in the immutable migration backup with explicit receipts.
            for table in ["crawler_accounts", "rule_sets", "rule_set_revisions", "rule_set_publish_idempotency",
                          "lexicon_categories", "lexicon_prompt_profiles", "audit_policies"]:
                if table in tables(audit_source):
                    self.insert(dst, table, rows(audit_source, table), keep_existing=True)
            existing_terms = {(r[0], r[1]) for r in dst.execute("SELECT category_id,keyword FROM lexicon_keywords")}
            terms = [r for r in rows(audit_source, "lexicon_keywords") if (r['category_id'], r['keyword']) not in existing_terms]
            self.insert(dst, "lexicon_keywords", terms, regenerate_id=True)

            reports = rows(audit_source, "reports", "task_id", jobs)
            report_ids = {r['id'] for r in reports}
            versions = rows(audit_source, "report_versions", "report_id", report_ids)
            version_ids = {r['id'] for r in versions}
            generation_ids = {r['id'] for r in rows(audit_source, "report_generation_runs", "report_id", report_ids)}
            claim_ids = {r['id'] for r in rows(audit_source, "report_claims", "report_version_id", version_ids)}
            category_ids = {r['id'] for r in rows(audit_source, "report_categories", "report_version_id", version_ids)} if 'report_categories' in tables(audit_source) else set()
            for table in tables(audit_source):
                if not table.startswith("report"):
                    continue
                columns = {row[1] for row in audit_source.execute(f'PRAGMA table_info("{table}")')}
                if table == 'reports': selected = reports
                elif table == 'report_versions': selected = [{**v, 'status': 'draft'} if v['status'] == 'published' else v for v in versions]
                elif 'report_version_id' in columns: selected = rows(audit_source, table, 'report_version_id', version_ids)
                elif 'claim_id' in columns: selected = rows(audit_source, table, 'claim_id', claim_ids)
                elif 'category_id' in columns: selected = rows(audit_source, table, 'category_id', category_ids)
                elif 'run_id' in columns: selected = rows(audit_source, table, 'run_id', generation_ids)
                else:
                    selected = []
                    if rows(audit_source, table): raise RuntimeError(f'Unclassified report dependency: {table}')
                self.insert(dst, table, selected)
            for version in versions:
                if version['status'] == 'published':
                    dst.execute("UPDATE report_versions SET status='published' WHERE id=?", (version['id'],))
                saved = dict(dst.execute('SELECT * FROM report_versions WHERE id=?', (version['id'],)).fetchone())
                assert all(saved[k] == value for k, value in version.items()), version['id']
        self.receipts.append({"source": self.source_name, "sessions": sorted(sessions), "jobs": sorted(jobs), "held_runs": [r['id'] for r in runs]})
        for connection in (creation_source, conversation_source, audit_source): connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--production', type=Path, required=True)
    parser.add_argument('--demo', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    assert not args.stage.exists(), 'Staging path must be new'
    args.stage.mkdir(parents=True)
    target = args.stage / 'data'; target.mkdir()
    manifest = {'sources': {}, 'receipts': []}
    names = ['audit_index.sqlite3', 'investigation.sqlite3', 'investigation_creation.sqlite3']
    for label, directory in [('candidate', args.candidate), ('production', args.production), ('demo', args.demo)]:
        snapshot = args.stage / label; snapshot.mkdir()
        manifest['sources'][label] = {'directory': str(directory.resolve()), 'files': {}}
        for name in names:
            backup(directory.resolve() / name, snapshot / name)
            manifest['sources'][label]['files'][name] = hashlib.sha256((snapshot/name).read_bytes()).hexdigest()
            if label == 'candidate': backup(snapshot / name, target / name)
    for label in ['production', 'demo']:
        merger = Merger(target, label)
        merger.merge(args.stage / label, extra_jobs=('m3-206117dc6e4a9202cd10',) if label == 'production' else ())
        manifest['receipts'].extend(merger.receipts)
    output_roots = {str((path / 'outputs').resolve()) for path in [args.production, args.demo]}
    output_roots.update(str(path / 'outputs') for path in [args.production, args.demo])
    # The candidate may itself still carry paths from its copied single-post
    # environment. Derive that old root from the saved task artifact paths.
    with connect(target/'audit_index.sqlite3') as connection:
        for row in connection.execute('SELECT raw_item_path FROM task_contents'):
            value = str(row[0] or '')
            if '/outputs/' in value:
                output_roots.add(value.split('/outputs/', 1)[0] + '/outputs')
        new_root = str((args.candidate / 'outputs').resolve())
        changed = {}
        for table in ['contents', 'task_contents', 'audit_results', 'ingest_batches', 'jobs']:
            info = list(connection.execute(f'PRAGMA table_info("{table}")'))
            text_columns = [r[1] for r in info if r[2].upper() == 'TEXT']
            for column in text_columns:
                for old_root in sorted(output_roots, key=len, reverse=True):
                    if old_root == new_root: continue
                    cursor = connection.execute(f'UPDATE "{table}" SET "{column}"=replace("{column}",?,?) WHERE instr("{column}",?)>0',
                                                (old_root+'/', new_root+'/', old_root+'/'))
                    if cursor.rowcount: changed[f'{table}.{column}'] = changed.get(f'{table}.{column}',0) + cursor.rowcount
        manifest['mutable_artifact_paths'] = {'source_roots':sorted(output_roots), 'target':new_root, 'changed':changed,
                                             'frozen_report_payloads_modified':False}
    for name in names:
        with connect(target/name) as connection:
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            problems = list(connection.execute('PRAGMA foreign_key_check'))
            assert not problems, (name, [tuple(r) for r in problems[:10]])
    (args.stage/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({'prepared':str(target), 'source_databases_modified':False, 'installed':False}))


if __name__ == '__main__': main()
