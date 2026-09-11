import importlib.util
from pathlib import Path
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location('formal_snapshot', Path(__file__).parents[1] / 'scripts/provision_formal_runtime.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def source_runtime(tmp_path):
    source = tmp_path / 'source'
    for directory in ('data', 'archives', 'crawler'):
        (source / directory).mkdir(parents=True)
    for filename, table, status in (
        ('audit_index.sqlite3', 'jobs', 'completed'),
        ('investigation_creation.sqlite3', 'investigation_runs', 'PUBLISHED'),
        ('investigation.sqlite3', 'investigation_turns', 'completed'),
    ):
        with sqlite3.connect(source / 'data' / filename) as db:
            db.execute(f'CREATE TABLE {table}(id TEXT PRIMARY KEY,status TEXT)')
            db.execute(f'INSERT INTO {table} VALUES (?,?)', ('saved', status))
    (source / 'archives' / 'report.sqlite3').write_bytes(b'opaque immutable archive')
    (source / 'data' / 'movie.mp4').write_bytes(b'preserved media')
    return source


def test_snapshot_includes_committed_wal_and_copies_are_independently_writable(tmp_path):
    source = source_runtime(tmp_path)
    target = tmp_path / 'target'
    target.mkdir()
    with sqlite3.connect(source / 'data' / 'audit_index.sqlite3') as live:
        live.execute('PRAGMA journal_mode=WAL')
        live.execute("INSERT INTO jobs VALUES ('wal-row','completed')")
        live.commit()
        receipt = module.snapshot(source, target)
        with sqlite3.connect(target / 'data' / 'audit_index.sqlite3') as copied:
            assert copied.execute('SELECT count(*) FROM jobs').fetchone()[0] == 2
            copied.execute("UPDATE jobs SET status='changed' WHERE id='wal-row'")
        assert live.execute("SELECT status FROM jobs WHERE id='wal-row'").fetchone()[0] == 'completed'
    (target / 'data' / 'movie.mp4').write_bytes(b'new media')
    assert (source / 'data' / 'movie.mp4').read_bytes() == b'preserved media'
    assert receipt['archives']['report.sqlite3'] == module.digest(source / 'archives' / 'report.sqlite3')


def test_active_source_is_rejected_before_copy(tmp_path):
    source = source_runtime(tmp_path)
    with sqlite3.connect(source / 'data' / 'investigation_creation.sqlite3') as db:
        db.execute("UPDATE investigation_runs SET status='QUEUED'")
    target = tmp_path / 'target'
    target.mkdir()
    with pytest.raises(RuntimeError, match='busy'):
        module.snapshot(source, target)
    assert not list(target.iterdir())


def test_failed_model_turn_is_preserved_as_terminal_history(tmp_path):
    source = source_runtime(tmp_path)
    with sqlite3.connect(source / 'data' / 'investigation.sqlite3') as db:
        db.execute("UPDATE investigation_turns SET status='error'")
    module.assert_quiescent(source / 'data')


def test_explicit_model_source_preserves_data_configuration_and_keeps_key_private(tmp_path):
    import json
    from dotenv import dotenv_values
    source, target = tmp_path / 'model', tmp_path / 'target'
    source.mkdir(); target.mkdir(); (target / 'receipts').mkdir()
    (source / 'environment.json').write_text(json.dumps({'DASHSCOPE_BASE_URL': 'https://model.test/v1', 'XHS_AUDIT_DATA_DIR': '/wrong'}))
    (source / 'secrets.env').write_text("DASHSCOPE_API_KEY='fixture-model-key'\n")
    (target / 'environment.json').write_text(json.dumps({'DASHSCOPE_API_KEY': 'obsolete', 'XHS_AUDIT_DATA_DIR': '/kept'}))
    (target / 'secrets.env').write_text("UNRELATED_KEY='fixture-existing'\n")
    module.copy_model_configuration(source, target)
    environment = json.loads((target / 'environment.json').read_text())
    private = dotenv_values(target / 'secrets.env')
    assert environment['XHS_AUDIT_DATA_DIR'] == '/kept'
    assert 'DASHSCOPE_API_KEY' not in environment
    assert private['DASHSCOPE_API_KEY'] == 'fixture-model-key'
    assert private['UNRELATED_KEY'] == 'fixture-existing'
    assert 'fixture-model-key' not in (target / 'receipts/model-configuration.json').read_text()
