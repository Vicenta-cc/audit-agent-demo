from hashlib import sha256
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.hermes_runtime.report_snapshot import published_report_snapshot
from backend.hermes_runtime.adapter import HermesRuntimeBinding


def live_database(tmp_path):
    path = tmp_path / 'live.sqlite3'
    live = sqlite3.connect(path)
    live.execute('PRAGMA journal_mode=WAL')
    live.execute('CREATE TABLE evidence (id INTEGER PRIMARY KEY)')
    live.execute('INSERT INTO evidence VALUES (1)')
    live.commit()
    return path, live


def test_wal_snapshot_remains_stable_across_worker_writes_and_repeated_turns(tmp_path):
    source, live = live_database(tmp_path)
    args = dict(ledger_path=tmp_path / 'ledger.sqlite3', session_id='session',
                identities=(('report', 'content', 'snapshot'),))
    try:
        frozen, digest = published_report_snapshot(source, **args)
        live.execute('INSERT INTO evidence VALUES (2)')
        live.commit()
        assert published_report_snapshot(source, **args) == (frozen, digest)
        with sqlite3.connect(frozen.as_uri() + '?mode=ro&immutable=1', uri=True) as db:
            assert db.execute('SELECT id FROM evidence').fetchall() == [(1,)]
        assert sha256(frozen.read_bytes()).hexdigest() == digest
    finally:
        live.close()


def test_cached_snapshot_tampering_is_rejected(tmp_path):
    source, live = live_database(tmp_path)
    live.close()
    args = dict(ledger_path=tmp_path / 'ledger.sqlite3', session_id='session', identities=())
    frozen, _ = published_report_snapshot(source, **args)
    frozen.chmod(0o600)
    with frozen.open('ab') as out:
        out.write(b'tampered')
    with pytest.raises(RuntimeError, match='SHA-256 mismatch'):
        published_report_snapshot(source, **args)


def test_binding_uses_one_snapshot_for_all_reports_during_worker_write(tmp_path, monkeypatch):
    from backend.hermes_runtime import adapter
    source, live = live_database(tmp_path)
    runtime = SimpleNamespace(
        AuthorizedReportSource=lambda **kw: SimpleNamespace(**kw),
        report_runtime_binding_for_session=Mock(return_value=None),
        bind_report_task_session=Mock(), release_report_task_session=Mock(),
    )
    def configure(path, **kw):
        before = sha256(path.read_bytes()).hexdigest()
        live.execute('INSERT OR REPLACE INTO evidence VALUES (2)')
        live.commit()
        for additional in kw['additional_account_report_sources']:
            assert additional.database_path == path
            assert additional.database_sha256 == before
        assert before == kw['expected_database_sha256'] == sha256(path.read_bytes()).hexdigest()
        return object()
    runtime.configure_real_report_runtime = Mock(side_effect=configure)
    monkeypatch.setattr(adapter, 'import_module', lambda name: runtime if name == 'hermes_m0.runtime' else SimpleNamespace(DEFAULT_ACCOUNT_CORPUS_PATH=tmp_path / 'corpus'))
    monkeypatch.setattr(HermesRuntimeBinding, 'activate_product_mode', lambda self: None)
    args = dict(session_id='session', database_path=source, report_version_id='c',
                content_hash='content', snapshot_hash='snapshot', ledger_path=tmp_path / 'ledger',
                additional_report_contexts=(SimpleNamespace(report_version_id='a', content_hash='a-content', snapshot_hash='a-snapshot'),))
    try:
        binding = HermesRuntimeBinding()
        binding.bind_published_report_session(**args)
        frozen = runtime.configure_real_report_runtime.call_args.args[0]
        digest = sha256(frozen.read_bytes()).hexdigest()
        runtime.report_runtime_binding_for_session.return_value = SimpleNamespace(
            report_version_id='c', snapshot_hash='snapshot', content_hash='content', database_sha256=digest,
            authorized_report_sources=(('c', 'snapshot', 'content', digest), ('a', 'a-snapshot', 'a-content', digest)))
        binding.bind_published_report_session(**args)
        assert runtime.configure_real_report_runtime.call_count == 1
        with pytest.raises(RuntimeError, match='cannot be rebound'):
            binding.bind_published_report_session(**{**args, 'report_version_id': 'different'})
    finally:
        live.close()
