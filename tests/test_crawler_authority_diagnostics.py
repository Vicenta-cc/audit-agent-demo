"""Owner diagnostics remain useful without exposing channel or browser secrets."""
import json
import logging
import socket
import sqlite3
import threading

import pytest

from backend.audit_agent import crawler_request_authority as authority_module

SECRET = 'private-secret-cookie-password'
URL = 'https://private.invalid/' + SECRET


def exchange_failure(dispatch, operation):
    """Exercise the actual owner error handler and its private wire response."""
    owner, child = socket.socketpair()
    handle = object.__new__(authority_module.RequestAuthority)
    handle.parent, handle.child = owner, child
    handle.closed, handle.ready = threading.Event(), threading.Event()
    handle.pid = 1
    handle.ready.set()
    handle._dispatch = dispatch
    worker = threading.Thread(target=handle._serve, daemon=True)
    worker.start()
    try:
        child.settimeout(2)
        child.sendall((json.dumps({'request_id':'a'*32,'operation':operation,
            'payload':{'url':URL,'cookie':SECRET}})+'\n').encode())
        data = bytearray()
        while b'\n' not in data:
            data.extend(child.recv(4096))
        return json.loads(data)
    finally:
        handle.close()
        worker.join(timeout=2)
        assert not worker.is_alive()


@pytest.mark.parametrize('operation,expected_operation', [
    ('reserve','reserve'),
    ('unknown\noperation:'+URL,'unknown'),
])
def test_sqlite_failure_logs_owner_category_but_child_receives_only_safe_code(caplog,operation,expected_operation):
    def fail(request):
        # An actual SQL failure, wrapped with sensitive runtime context that
        # must never be printed by the owner or returned to the worker.
        with sqlite3.connect(':memory:') as db:
            try:
                db.execute('SELECT * FROM missing_private_table')
            except sqlite3.OperationalError as exc:
                raise sqlite3.OperationalError(str(exc)+' '+URL+' '+SECRET) from None
    with caplog.at_level(logging.WARNING,logger=authority_module.__name__):
        response=exchange_failure(fail,operation)
    assert response=={'request_id':'a'*32,'ok':False,'error':'PROXY_UNAVAILABLE'}
    records=[record for record in caplog.records if record.name==authority_module.__name__]
    assert len(records)==1
    assert records[0].getMessage()==(
        'CRAWLER_AUTHORITY_FAILURE operation='+expected_operation+
        ' exception_class=OperationalError category=database_operational')
    assert records[0].exc_info is None and records[0].stack_info is None
    assert not any(value in caplog.text+json.dumps(response) for value in (SECRET,URL,'missing_private_table','payload'))


@pytest.mark.parametrize('name',['Unsafe\n'+SECRET, 'A'*65])
def test_exception_class_is_character_and_length_bounded(caplog,name):
    error_class=type(name,(RuntimeError,),{})
    with caplog.at_level(logging.WARNING,logger=authority_module.__name__):
        authority_module._log_owner_failure('check_stop',error_class(URL))
    assert caplog.records[-1].getMessage()==(
        'CRAWLER_AUTHORITY_FAILURE operation=check_stop exception_class=Exception category=runtime')
    assert SECRET not in caplog.text and URL not in caplog.text and name not in caplog.text


def test_existing_allowlisted_child_error_is_unchanged(caplog):
    class SafeError(RuntimeError):
        code='PROXY_REVISION_MISMATCH'
    def fail(request):
        raise SafeError(SECRET+' '+URL)
    with caplog.at_level(logging.WARNING,logger=authority_module.__name__):
        response=exchange_failure(fail,'check_stop')
    assert response['error']=='PROXY_REVISION_MISMATCH'
    assert 'exception_class=SafeError category=runtime' in caplog.text
    assert SECRET not in caplog.text+json.dumps(response)
