"""Offline preservation tests: no network, live DB writes, profiles or activation."""
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recovery import BASELINE, DATABASES, RecoveryRejected, main, prepare, verify


@pytest.fixture
def backup(tmp_path):
    source = tmp_path / 'immutable-backup'
    source.mkdir()
    for name in DATABASES:
        with sqlite3.connect(source / name) as connection:
            connection.execute('CREATE TABLE business_rows(id INTEGER, value BLOB)')
            connection.executemany('INSERT INTO business_rows VALUES(?,?)', [(1, b'secret'), (1, b'secret'), (2, None)])
    with sqlite3.connect(source / 'audit_index.sqlite3') as connection:
        connection.execute('CREATE TABLE crawler_accounts(id TEXT, owner TEXT, risk_count INTEGER, status TEXT, auth_state_ciphertext TEXT)')
        connection.execute("INSERT INTO crawler_accounts VALUES('account','owner',3,'risk_paused','encrypted-secret')")
    with sqlite3.connect(source / 'investigation_creation.sqlite3') as connection:
        connection.execute('CREATE TABLE app_users(id TEXT, password_hash TEXT, role TEXT)')
        connection.execute("INSERT INTO app_users VALUES('owner','private-hash','admin')")
        connection.execute('CREATE TABLE login_sessions(id TEXT, user_id TEXT, token_hash TEXT)')
        connection.execute("INSERT INTO login_sessions VALUES('session','owner','private-token')")
    return source


def test_schema_additions_preserve_rows_and_collocated_auth(backup, tmp_path):
    before = {p.name: p.read_bytes() for p in backup.iterdir()}
    target = tmp_path / 'migration'
    result = prepare(backup, target, immutable_backup=True)
    assert result['auth_database_files'] == ['investigation_creation.sqlite3']
    assert result['auth_collocated_with_creation'] is True
    assert result['inventories']['audit_index.sqlite3']['crawler_accounts'] == 1
    assert target.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in target.iterdir())
    with sqlite3.connect(target / 'audit_index.sqlite3') as connection:
        connection.execute("ALTER TABLE crawler_accounts ADD COLUMN engine TEXT DEFAULT 'camoufox'")
        connection.execute('CREATE TABLE new_schema(id INTEGER)')
    assert verify(target)['original_column_rows_preserved'] is True
    assert {p.name: p.read_bytes() for p in backup.iterdir()} == before
    public = json.dumps(result)
    assert all(value not in public for value in ['encrypted-secret', 'private-hash', 'private-token', 'owner'])


@pytest.mark.parametrize('field,value', [('owner', "'other'"), ('risk_count', '0'), ('status', "'active'"), ('auth_state_ciphertext', "'replaced'")])
def test_account_owner_auth_and_protection_changes_rejected(backup, tmp_path, field, value):
    target = tmp_path / 'migration'
    prepare(backup, target, immutable_backup=True)
    with sqlite3.connect(target / 'audit_index.sqlite3') as connection:
        connection.execute(f'UPDATE crawler_accounts SET {field}={value}')
    with pytest.raises(RecoveryRejected, match='original_rows_changed'):
        verify(target)


def test_duplicate_row_loss_is_not_hidden_by_row_count(backup, tmp_path):
    target = tmp_path / 'migration'
    prepare(backup, target, immutable_backup=True)
    with sqlite3.connect(target / 'audit_index.sqlite3') as connection:
        connection.execute('DELETE FROM business_rows WHERE rowid=1')
        connection.execute("INSERT INTO business_rows VALUES(9,'other')")
    with pytest.raises(RecoveryRejected, match='original_rows_changed'):
        verify(target)


@pytest.mark.parametrize('case', ['exists', 'overlap', 'missing', 'symlink', 'ancestor_symlink', 'hardlink', 'wal', 'journal', 'unacknowledged'])
def test_unsafe_inputs_fail_without_changing_backups(backup, tmp_path, case):
    target = tmp_path / 'migration'
    source = backup
    acknowledge = True
    if case == 'exists':
        target.mkdir()
    elif case == 'overlap':
        target = backup / 'nested'
    elif case == 'missing':
        (backup / DATABASES[-1]).unlink()
    elif case == 'symlink':
        target.symlink_to(backup, target_is_directory=True)
    elif case == 'ancestor_symlink':
        (tmp_path / 'alias').symlink_to(backup, target_is_directory=True)
        target = tmp_path / 'alias' / 'new'
    elif case == 'hardlink':
        (tmp_path / 'linked').hardlink_to(backup / DATABASES[0])
    elif case in ('wal', 'journal'):
        Path(str(backup / DATABASES[0]) + '-' + case).write_bytes(b'journal')
    else:
        acknowledge = False
    before = {p.name: p.read_bytes() for p in backup.iterdir()}
    with pytest.raises(RecoveryRejected):
        prepare(source, target, immutable_backup=acknowledge)
    assert {p.name: p.read_bytes() for p in backup.iterdir()} == before


def test_business_rollback_keeps_new_writes_and_requires_maintenance(backup, tmp_path, capsys):
    upgraded = tmp_path / 'upgraded'
    prepare(backup, upgraded, immutable_backup=True)
    # Represents a separately stopped post-upgrade snapshot. Never roll back to
    # the pre-upgrade backup after accepting new business writes.
    with sqlite3.connect(upgraded / 'investigation.sqlite3') as connection:
        connection.execute('ALTER TABLE business_rows ADD COLUMN new_metadata TEXT')
        connection.execute("INSERT INTO business_rows VALUES(3,'new-write','v2')")
    rollback = tmp_path / 'rollback'
    assert main(['business-rollback', '--source', str(upgraded), '--destination', str(rollback),
                 '--immutable-backup', '--business-only-maintenance']) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt['scope'] == 'business-only-maintenance'
    assert receipt['collection_must_remain_paused'] is True
    assert receipt['old_binary_verified'] is False
    assert receipt['profiles_migrated'] is False
    # Real old-column business reader on migrated/new-write data, not an engine
    # fallback or a claim of full old application binary compatibility.
    with sqlite3.connect(rollback / 'investigation.sqlite3') as old_reader:
        assert old_reader.execute('SELECT id,value FROM business_rows WHERE id=3').fetchone() == (3, 'new-write')
    assert verify(rollback)['original_column_rows_preserved'] is True
    with pytest.raises(SystemExit):
        main(['business-rollback', '--source', str(upgraded), '--destination', str(tmp_path / 'unsafe'), '--immutable-backup'])


def test_verify_missing_table_column_and_symlink_rejected(backup, tmp_path):
    for case in ('table', 'column', 'symlink'):
        target = tmp_path / case
        prepare(backup, target, immutable_backup=True)
        db = target / 'audit_index.sqlite3'
        if case == 'symlink':
            db.unlink()
            db.symlink_to(backup / db.name)
        else:
            with sqlite3.connect(db) as connection:
                connection.execute('DROP TABLE crawler_accounts' if case == 'table' else 'ALTER TABLE crawler_accounts DROP COLUMN owner')
        with pytest.raises(RecoveryRejected):
            verify(target)


def test_real_auth_schema_initializer_on_copy_preserves_credentials_and_owners(backup, tmp_path):
    # This constructor accepts an explicit DB path and has no global store. Other
    # app modules can construct global stores at import and remain external hooks.
    from backend.application_auth.store import AuthStore
    path = backup / 'investigation_creation.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('DROP TABLE login_sessions')
        connection.execute('DROP TABLE app_users')
    AuthStore(path)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO crawler_account_owners VALUES('account','legacy-unassigned','2026-10-19')")
    original = path.read_bytes()
    target = tmp_path / 'real-schema'
    prepare(backup, target, immutable_backup=True)
    AuthStore(target / 'investigation_creation.sqlite3')
    assert verify(target)['original_column_rows_preserved'] is True
    assert path.read_bytes() == original


def test_cli_rejection_codes_are_diagnostic_without_echoing_private_values(tmp_path, capsys, monkeypatch):
    import recovery
    source = tmp_path / 'private-account-secret'
    target = tmp_path / 'target'
    assert main(['prepare', '--source', str(source), '--destination', str(target),
                 '--immutable-backup']) == 2
    message = capsys.readouterr().err
    assert json.loads(message)['error'] == 'backup_directory_missing'
    assert 'private-account-secret' not in message
    def reject(*args, **kwargs):
        raise recovery.RecoveryRejected('secret cookie=private-token')
    monkeypatch.setattr(recovery, 'verify', reject)
    assert main(['verify', '--directory', str(source)]) == 2
    message = capsys.readouterr().err
    assert json.loads(message)['error'] == 'offline_recovery_rejected'
    assert 'private-token' not in message


@pytest.mark.parametrize('failure', [False, True])
def test_inspection_closes_connection_on_success_and_rejection(backup, monkeypatch, failure):
    import recovery
    opened = []
    connect = sqlite3.connect

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(recovery.sqlite3, 'connect', tracked_connect)
    if failure:
        with pytest.raises(RecoveryRejected, match='original_table_missing'):
            recovery.inspect_database(backup / DATABASES[0], b'key', {'missing': {}})
    else:
        recovery.inspect_database(backup / DATABASES[0], b'key')
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        opened[0].execute('SELECT 1')


@pytest.mark.parametrize('failure', [False, True])
def test_copy_closes_source_and_destination_even_when_backup_fails(backup, tmp_path, monkeypatch, failure):
    import recovery
    opened = []
    connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        def backup(self, target, *args, **kwargs):
            if failure:
                raise sqlite3.OperationalError('simulated copy failure')
            return super().backup(target, *args, **kwargs)

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, factory=TrackedConnection, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(recovery.sqlite3, 'connect', tracked_connect)
    target = tmp_path / 'copy-close'
    if failure:
        with pytest.raises(sqlite3.OperationalError, match='simulated copy failure'):
            prepare(backup, target, immutable_backup=True)
        assert len(opened) == 2
        assert not (target / BASELINE).exists()
    else:
        prepare(backup, target, immutable_backup=True)
        assert len(opened) == 3 * len(DATABASES)
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            connection.execute('SELECT 1')


def test_verification_reads_committed_wal_rows_without_mutating_backup(backup, tmp_path):
    from contextlib import closing
    original = {p.name: p.read_bytes() for p in backup.iterdir()}
    target = tmp_path / 'wal-copy'
    prepare(backup, target, immutable_backup=True)
    database = target / 'audit_index.sqlite3'
    with closing(sqlite3.connect(database)) as writer:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
        with writer:
            writer.execute('UPDATE crawler_accounts SET risk_count=0')
        # Keep the writer open so the committed protection change is still in
        # WAL. Resource closure must not switch verification to immutable reads
        # that could miss it or checkpoint/mutate the caller's working copy.
        assert Path(str(database) + '-wal').stat().st_size > 0
        with pytest.raises(RecoveryRejected, match='original_rows_changed'):
            verify(target)
        assert Path(str(database) + '-wal').stat().st_size > 0
    assert {p.name: p.read_bytes() for p in backup.iterdir()} == original
