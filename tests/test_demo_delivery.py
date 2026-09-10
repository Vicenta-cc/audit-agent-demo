from pathlib import Path
import importlib.util
import socket
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('demo_launcher', ROOT / 'scripts' / 'demo.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def test_config_fences_inherited_business_environment(tmp_path, monkeypatch):
    config = tmp_path / 'demo.env'
    config.write_text('DEMO_DATA_DIR=./data/example\nDEMO_API_PORT=18158\nDEMO_WEB_PORT=13158\nDASHSCOPE_API_KEY=explicit-test-key\n')
    monkeypatch.setenv('XHS_AUDIT_DATA_DIR', '/wrong/production')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'inherited-key')
    monkeypatch.setenv('MEDIACRAWLER_DIR', '/wrong/crawler')
    monkeypatch.setenv('HISTORICAL_REPORT_A_DB', '/wrong/archive')
    monkeypatch.setenv('M3_ANALYZE_LIMIT', '340')
    env, data, api, web = demo.environment(config)
    assert data == ROOT / 'data' / 'example'
    assert env['DASHSCOPE_API_KEY'] == 'explicit-test-key'
    assert env['XHS_AUDIT_OUTPUTS_DIR'] == str(data / 'outputs')
    assert env['CRAWLER_AUTH_KEY_FILE'] == str(data / 'crawler_auth.key')
    assert env['MEDIACRAWLER_DIR'] == str(ROOT / 'external' / 'MediaCrawler')
    assert 'HISTORICAL_REPORT_A_DB' not in env
    assert 'M3_ANALYZE_LIMIT' not in env
    assert env['PYTHON_DOTENV_DISABLED'] == '1'
    assert (api, web) == (18158, 13158)


def test_init_refuses_unowned_database_without_changing_it(tmp_path):
    database = tmp_path / 'audit_index.sqlite3'
    database.write_bytes(b'unrelated existing database')
    with pytest.raises(RuntimeError, match='unmarked'):
        demo.initialize(tmp_path)
    assert database.read_bytes() == b'unrelated existing database'
    assert not (tmp_path / 'demo-data.json').exists()


def test_occupied_port_is_not_taken_over():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        server.listen()
        with pytest.raises(RuntimeError, match='occupied'):
            demo.vacant(server.getsockname()[1])
        assert server.fileno() >= 0


def test_seed_contains_only_report_tables_and_integrity_matches(tmp_path):
    import sqlite3
    from backend.historical_reports.importer import _REPORT_COPY_PLAN
    demo.restore_seed(tmp_path)
    demo.restore_seed(tmp_path)
    for archive in (tmp_path / 'demo-seed').glob('*.sqlite3'):
        with sqlite3.connect(archive.as_uri() + '?mode=ro', uri=True) as connection:
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert names == {row[0] for row in _REPORT_COPY_PLAN}
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    archive.write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='Refusing to overwrite'):
        demo.restore_seed(tmp_path)
