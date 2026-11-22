"""Worker startup must serialize each inspect-and-alter schema migration."""
import multiprocessing
import sqlite3

import pytest

from backend.audit_agent.crawler_account_store import CrawlerAccountStore


class MigrationConnection(sqlite3.Connection):
    def execute(self, sql, *args, **kwargs):
        if sql.startswith('PRAGMA table_info('):
            assert self.in_transaction, 'schema inspection must hold the writer reservation'
        return super().execute(sql, *args, **kwargs)


class InspectedStore(CrawlerAccountStore):
    def _connect(self, *, timeout=5.0):
        conn = sqlite3.connect(self.db_path, timeout=timeout, factory=MigrationConnection)
        conn.row_factory = sqlite3.Row
        return conn


def test_all_account_column_migrations_hold_writer_reservation(tmp_path):
    path = tmp_path / 'accounts.sqlite3'
    InspectedStore(path)
    InspectedStore(path)


def initialize(path, barrier, results):
    try:
        barrier.wait(timeout=20)
        store = CrawlerAccountStore(path)
        results.put(('ok', len(store.list())))
    except BaseException as exc:
        results.put((type(exc).__name__, str(exc)))


@pytest.mark.parametrize('existing', [False, True])
def test_four_workers_initialize_same_account_database(tmp_path, existing):
    path = tmp_path / 'accounts.sqlite3'
    if existing:
        CrawlerAccountStore(path).create(platform='dy', display_name='preserved')
    context = multiprocessing.get_context('spawn')
    barrier, results = context.Barrier(4), context.Queue()
    children = [context.Process(target=initialize, args=(path, barrier, results)) for _ in range(4)]
    try:
        for child in children:
            child.start()
        received = [results.get(timeout=30) for _ in children]
        assert received == [('ok', int(existing))] * 4
        for child in children:
            child.join(timeout=10)
            assert child.exitcode == 0
        with sqlite3.connect(path) as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(crawler_manual_recovery_operations)')}
        assert {'initial_login', 'risk_recorded'} <= columns
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            if child.pid:
                child.join(timeout=10)
        results.close()
        results.join_thread()
