import importlib.util
import json
import socket
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('m3_environment', Path(__file__).parents[1] / 'scripts/m3_environment.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def configuration(tmp_path, monkeypatch):
    runtime = tmp_path / 'outputs/m3-environment'
    runtime.mkdir(parents=True)
    config = {'environment_id': 'experiment', 'worktree': str(tmp_path),
              'data_dir': str(runtime / 'data'), 'crawler_dir': str(runtime / 'crawler'),
              'api_port': 8178, 'frontend_port': 3178}
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'RUNTIME', runtime)
    monkeypatch.setattr(module, 'CONFIG', runtime / 'config.json')
    (runtime / 'build.json').write_text(json.dumps({'source_hash': 'build-1'}))
    return config


@pytest.mark.parametrize('field,value', [('worktree', '/other'), ('data_dir', '/other/data'),
                                       ('crawler_dir', '/other/crawler'), ('api_port', 8168),
                                       ('frontend_port', 3148), ('api_port', 3178)])
def test_refuses_foreign_paths_and_reserved_ports_before_loading_secrets(configuration, field, value):
    configuration[field] = value
    module.CONFIG.write_text(json.dumps(configuration))
    with pytest.raises(RuntimeError):
        module.configure()


def test_status_rejects_plain_200_from_wrong_environment(configuration, monkeypatch):
    monkeypatch.setattr(module, 'get_json', lambda _: {'status': 'ok'})
    with pytest.raises(RuntimeError, match='身份'):
        module.status(configuration)


def test_status_requires_both_reports_and_reads_each_report(configuration, monkeypatch):
    calls = []
    reports = [{'workspace_id': 'historical-report-' + label, 'report_version_id': label} for label in ('a', 'b')]
    def get(url):
        calls.append(url)
        if url.endswith('/historical-report-workspaces'):
            return {'items': reports}
        if '/report-versions/' in url:
            return {'report': 'published'}
        return module.identity(configuration)
    monkeypatch.setattr(module, 'get_json', get)
    assert module.status(configuration, worker=False)['reports'] == 2
    assert any(url.endswith('/report-versions/a') for url in calls)
    assert any(url.endswith('/report-versions/b') for url in calls)
    reports.pop()
    with pytest.raises(RuntimeError, match='A/B'):
        module.status(configuration, worker=False)


def test_source_fingerprint_changes_when_ui_changes(configuration):
    source = module.ROOT / 'Audit_assistant/src'
    source.mkdir(parents=True)
    file = source / 'app.tsx'
    file.write_text('before')
    before = module.source_hash()
    file.write_text('after')
    assert module.source_hash() != before


def test_port_probe_rejects_live_listener():
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        with pytest.raises(OSError):
            module.check_port_available(listener.getsockname()[1])


def test_port_probe_allows_recently_closed_connection():
    with socket.socket() as listener, socket.socket() as client:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        listener.listen(1)
        client.connect(('127.0.0.1', port))
        connection, _ = listener.accept()
        connection.close()
        assert client.recv(1) == b''
    module.check_port_available(port)
