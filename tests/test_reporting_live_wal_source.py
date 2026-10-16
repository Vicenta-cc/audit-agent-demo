import sqlite3
from contextlib import closing

import pytest

from backend.reporting.integration_source import CanonicalReportSource


def test_live_source_reads_committed_uncheckpointed_audit_rows_without_writes(tmp_path):
    path = tmp_path / 'live audit.sqlite3'
    with closing(sqlite3.connect(path)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.executescript('''
            CREATE TABLE audit_results (id INTEGER PRIMARY KEY, job_id TEXT, content_key TEXT, content_id INTEGER);
            CREATE TABLE task_contents (task_id TEXT, audit_result_id INTEGER, analyze_status TEXT,
                                        raw_item_path TEXT, created_at TEXT);
        ''')
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        writer.execute("INSERT INTO audit_results VALUES (1,'task','post',1)")
        writer.execute("INSERT INTO task_contents VALUES ('task',1,'completed','raw.json','2026-10-16')")
        writer.commit()
        # Establish that the row really exists only in WAL, rather than testing
        # an accidentally checkpointed fixture that immutable=1 could read.
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as stale:
            assert stale.execute('SELECT COUNT(*) FROM audit_results').fetchone()[0] == 0
        source = CanonicalReportSource(path, tmp_path / 'outputs')
        assert [row['content_key'] for row in source.canonical_rows('task')] == ['post']
        with closing(source.connect()) as reader:
            assert reader.execute('PRAGMA query_only').fetchone()[0] == 1
            with pytest.raises(sqlite3.OperationalError, match='readonly'):
                reader.execute('DELETE FROM audit_results')
        assert writer.execute('SELECT COUNT(*) FROM audit_results').fetchone()[0] == 1
