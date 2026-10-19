"""Offline SQLite migration rehearsal; never writes backups or installs a runtime.

The caller must supply stopped, immutable snapshots with no journal sidecars.
Schema initializers and old-version business-read probes run separately on copies.
No profile copying, account activation, fallback engine or network operations occur.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
from urllib.parse import quote

DATABASES = (
    'audit_index.sqlite3', 'historical_report_demo.sqlite3',
    'investigation.sqlite3', 'investigation_creation.sqlite3',
    'report-collections.sqlite3', 'report_checkpoints.sqlite3',
    'request_scheduler.sqlite3',
)
BASELINE = 'recovery-baseline.json'


class RecoveryRejected(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise RecoveryRejected(reason)


def safe_path(value):
    path = Path(os.path.abspath(os.path.expanduser(str(value))))
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink_path_rejected')
    return path


def readonly(path, *, immutable=False):
    suffix = '?mode=ro&immutable=1' if immutable else '?mode=ro'
    return sqlite3.connect('file:' + quote(str(path), safe='/') + suffix, uri=True)


def file_digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


def row_digest(row, key):
    result = hashlib.blake2b(key=key, digest_size=32)
    for value in row:
        if value is None:
            kind, raw = b'n', b''
        elif isinstance(value, bytes):
            kind, raw = b'b', value
        else:
            kind, raw = type(value).__name__.encode(), str(value).encode('utf-8', 'surrogatepass')
        result.update(kind + b':' + str(len(raw)).encode() + b':' + raw)
    return result.hexdigest()


def inspect_database(path, key, original=None):
    with closing(readonly(path)) as connection:
        require(connection.execute('PRAGMA quick_check').fetchall() == [('ok',)], 'sqlite_integrity_failed')
        tables = {}
        names = [r[0] for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for name in (original if original is not None else names):
            require(name in names, 'original_table_missing')
            available = [r[1] for r in connection.execute('PRAGMA table_info(' + identifier(name) + ')')]
            columns = original[name]['columns'] if original is not None else available
            require(set(columns) <= set(available), 'original_column_missing')
            query = 'SELECT ' + ','.join(map(identifier, columns)) + ' FROM ' + identifier(name)
            rows = Counter(row_digest(row, key) for row in connection.execute(query))
            tables[name] = {'columns': columns, 'rows': dict(rows), 'count': sum(rows.values())}
        return tables


def inventory(tables):
    # All columns, including account ownership, risk state and ciphertext, are
    # covered by the keyed row baseline. Only table counts are public output.
    return {name: tables[name]['count'] for name in (
        'crawler_accounts', 'crawler_proxies', 'app_users', 'login_sessions',
        'crawler_account_owners', 'crawler_account_owner_bindings',
    ) if name in tables}


def write_private(path, payload):
    with open(path, 'x', encoding='utf-8', opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
        stream.write('\n')


def prepare(source, destination, *, immutable_backup=False, business_only=False):
    require(immutable_backup, 'explicit_immutable_backup_required')
    source, destination = safe_path(source), safe_path(destination)
    require(source.is_dir(), 'backup_directory_missing')
    require(not destination.exists(), 'destination_must_be_new')
    require(source not in destination.parents and destination not in source.parents,
            'backup_destination_overlap')
    require(destination.parent.is_dir(), 'destination_parent_missing')
    # Validate every input before creating anything. SQLite immutable=1 must never
    # be used on a live WAL database or an incomplete hot-journal snapshot.
    fingerprints = {}
    for name in DATABASES:
        path = safe_path(source / name)
        require(path.is_file() and path.stat().st_nlink == 1, 'database_missing_or_hardlinked')
        require(not any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')),
                'snapshot_has_journal_sidecars')
        fingerprints[name] = file_digest(path)
    destination.mkdir(mode=0o700)
    key = secrets.token_bytes(32)
    baseline = {'format': 1, 'source': str(source), 'key': key.hex(),
                'scope': 'business-only-maintenance' if business_only else 'offline-migration-rehearsal',
                'databases': {}, 'profiles_migrated': False, 'old_binary_verified': False,
                'runtime_installed': False, 'collection_must_remain_paused': True}
    for name in DATABASES:
        target = destination / name
        with closing(readonly(source / name, immutable=True)) as src, closing(sqlite3.connect(target)) as dst:
            with dst:
                src.backup(dst)
        target.chmod(0o600)
        require(file_digest(source / name) == fingerprints[name], 'backup_changed_during_copy')
        tables = inspect_database(target, key)
        baseline['databases'][name] = {'source_sha256': fingerprints[name], 'tables': tables}
    require(all(file_digest(source / name) == digest for name, digest in fingerprints.items()),
            'backup_changed_during_copy')
    auth_files = [name for name, item in baseline['databases'].items() if 'app_users' in item['tables']]
    baseline['auth_database_files'] = auth_files
    baseline['auth_collocated_with_creation'] = 'investigation_creation.sqlite3' in auth_files
    write_private(destination / BASELINE, baseline)
    return summary(baseline)


def summary(baseline):
    return {'scope': baseline['scope'], 'database_count': len(baseline['databases']),
            'inventories': {name: inventory(item['tables']) for name, item in baseline['databases'].items()},
            'auth_database_files': baseline['auth_database_files'],
            'auth_collocated_with_creation': baseline['auth_collocated_with_creation'],
            'collection_must_remain_paused': True, 'profiles_migrated': False,
            'old_binary_verified': False, 'runtime_installed': False}


def verify(directory):
    directory = safe_path(directory)
    path = safe_path(directory / BASELINE)
    require(path.is_file(), 'baseline_missing')
    baseline = json.loads(path.read_text())
    require(baseline.get('format') == 1 and set(baseline['databases']) == set(DATABASES), 'baseline_invalid')
    key = bytes.fromhex(baseline['key'])
    for name, item in baseline['databases'].items():
        target = safe_path(directory / name)
        require(target.is_file() and target.stat().st_nlink == 1, 'database_missing_or_hardlinked')
        current = inspect_database(target, key, item['tables'])
        for table, original in item['tables'].items():
            # Schema additions and new rows are permitted; no baseline row may be
            # removed/rewritten, including passwords, owners and protection state.
            require(not (Counter(original['rows']) - Counter(current[table]['rows'])),
                    'original_rows_changed_or_missing')
    result = summary(baseline)
    result['original_column_rows_preserved'] = True
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ('prepare', 'business-rollback'):
        sub = commands.add_parser(action)
        sub.add_argument('--source', required=True)
        sub.add_argument('--destination', required=True)
        sub.add_argument('--immutable-backup', action='store_true')
        if action == 'business-rollback':
            sub.add_argument('--business-only-maintenance', action='store_true', required=True)
    check = commands.add_parser('verify')
    check.add_argument('--directory', required=True)
    args = parser.parse_args(argv)
    try:
        result = verify(args.directory) if args.action == 'verify' else prepare(
            args.source, args.destination, immutable_backup=args.immutable_backup,
            business_only=args.action == 'business-rollback')
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except RecoveryRejected as exc:
        code = str(exc)
        if re.fullmatch(r'[a-z][a-z0-9_]{0,95}', code) is None:
            code = 'offline_recovery_rejected'
        print(json.dumps({'ok': False, 'error': code}), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error, ValueError, KeyError):
        # Never echo SQLite row values, credentials or raw exception messages.
        print('{"ok":false,"error":"offline_recovery_rejected"}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
