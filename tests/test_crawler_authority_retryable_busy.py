"""Only known rolled-back BUSY may be replayed by the private client."""
import sqlite3
import time
import pytest
from backend.audit_agent.crawler_request_authority import RequestAuthority

@pytest.mark.parametrize('rolled_back', [False, True])
def test_busy_reply_requires_confirmed_rollback(rolled_back):
    owner=object.__new__(RequestAuthority)
    error=sqlite3.OperationalError('private fixture')
    error.sqlite_errorcode=sqlite3.SQLITE_BUSY
    error.reservation_rolled_back=rolled_back
    error.reservation_step='begin'
    with pytest.raises(RuntimeError, match='SCHEDULER_DATABASE_BUSY') as caught:
        owner._retry_database_error(error,operation='browser_validate',stage='browser_safety',
            retries=0,started=time.monotonic(),deadline=time.time()+5)
    assert getattr(caught.value,'retryable',False) is rolled_back
    assert (getattr(owner,'_database_retry_after',0)>time.monotonic()) is rolled_back


def test_non_busy_error_never_gets_replay_permission():
    owner=object.__new__(RequestAuthority)
    error=sqlite3.OperationalError('missing table')
    error.sqlite_errorcode=sqlite3.SQLITE_ERROR
    error.reservation_rolled_back=True
    with pytest.raises(sqlite3.OperationalError):
        owner._retry_database_error(error,operation='check_stop',stage='health_read',
            retries=0,started=time.monotonic(),deadline=time.time()+5)
    assert not getattr(error,'retryable',False)

from tests.test_crawler_authority_browser_busy import pinned
from tests.test_camoufox_direct_route import direct

@pytest.mark.parametrize('pause', [False, True])
def test_renewal_and_long_writer_wait_preserve_ticket_and_safety(pinned, monkeypatch, pause):
    import threading
    from contextlib import closing
    from tools.collection_route import CollectionRouteError
    route, client, owner = pinned
    real_time=time.time
    future_time=real_time()+275
    monkeypatch.setattr(time,'time',lambda:future_time)
    locked=threading.Event()
    failures=[]
    def hold():
        try:
            with closing(sqlite3.connect(route.safety_db_path)) as db:
                db.execute('BEGIN IMMEDIATE')
                locked.set()
                time.sleep(1.2)
                if pause:
                    db.execute("UPDATE crawler_accounts SET status='paused' WHERE id=?",(route.account_id,))
                db.commit()
        except BaseException as exc:failures.append(exc)
    thread=threading.Thread(target=hold)
    thread.start()
    assert locked.wait(2)
    if pause:
        with pytest.raises(CollectionRouteError):client.call('browser_validate')
    else:
        assert client.call('browser_validate') is None
        assert owner.session._lease_ack['lease_version']==2
        assert client.call('browser_validate') is None
    thread.join(3)
    assert not thread.is_alive() and not failures
    with closing(sqlite3.connect(route.scheduler_db_path)) as db:
        assert db.execute('SELECT count(*) FROM route_lease_commands').fetchone()[0]==(0 if pause else 1)
