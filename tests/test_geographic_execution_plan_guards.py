"""Deliberate forbidden attempts prove rejection, never successful side effects."""
import importlib
import socket
import sqlite3
import pytest
from experiments.geographic_execution_plan.offline_guards import OfflineGuards, OfflineAccessBlocked

@pytest.fixture
def guards(tmp_path):
    guard=OfflineGuards(experiment_root=tmp_path).install()
    yield guard
    guard.uninstall()


def test_network_blocked_before_transport(guards):
    with pytest.raises(OfflineAccessBlocked): socket.create_connection(('example.invalid',443))
    assert guards.attempts[-1]['kind']=='network' and not guards.attempts[-1]['actual_succeeded']


def test_sqlite_business_path_blocked_temp_allowed(guards,tmp_path):
    with pytest.raises(OfflineAccessBlocked): sqlite3.connect('/Users/offline-forbidden-business.sqlite3')
    with sqlite3.connect(tmp_path/'allowed.sqlite3') as conn: conn.execute('CREATE TABLE fixture (id INTEGER)')
    with sqlite3.connect(':memory:') as conn: assert conn.execute('SELECT 1').fetchone()==(1,)

@pytest.mark.parametrize('module,owner,method',[
    ('backend.audit_agent.qwen_client','QwenClient','audit_text'),
    ('backend.audit_agent.remote_inference','RemoteInferenceClient','audit_text'),
    ('backend.investigation.qwen_chat_tool_client','QwenChatToolClient','complete'),
    ('backend.reporting.qwen_report_client','QwenReportClient','generate_structured'),
    ('backend.audit_agent.crawler_adapter','MediaCrawlerAdapter','run_search'),
    ('backend.task_admission.scheduler',None,'dispatch'),
])
def test_real_entrypoints_blocked_before_body(guards,module,owner,method):
    loaded=importlib.import_module(module)
    target=getattr(loaded,owner) if owner else loaded
    with pytest.raises(OfflineAccessBlocked): getattr(target,method)(None)
    assert guards.attempts[-1]['blocked'] is True


def test_sqlite_attach_cannot_escape_roots(guards):
    with sqlite3.connect(':memory:') as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("ATTACH DATABASE '/Users/offline-forbidden-attach.sqlite3' AS forbidden")
    assert guards.attempts[-1]['kind']=='sqlite'


def test_runner_child_inherits_guards_when_bootstrapped():
    import os
    import subprocess
    import sys
    if not os.environ.get('GEO_OFFLINE_OUTPUT'):
        pytest.skip('Child bootstrap is installed by run_offline entrypoint')
    result=subprocess.run([sys.executable,'-c',
        "from experiments.geographic_execution_plan.offline_guards import ACTIVE, OfflineAccessBlocked; "
        "assert ACTIVE is not None; import sqlite3; "
        "\ntry: sqlite3.connect('/Users/offline-forbidden-child.sqlite3')"
        "\nexcept OfflineAccessBlocked: print('child guarded')"
        "\nelse: raise AssertionError('child guard absent')"],capture_output=True,text=True,check=True)
    assert result.stdout.strip()=='child guarded'


def test_sqlite_uri_paths_are_resolved_before_permission(guards,tmp_path):
    path=tmp_path/'uri.sqlite3'
    with sqlite3.connect(path.as_uri()+'?mode=rwc',uri=True) as conn:
        conn.execute('CREATE TABLE fixture(id INTEGER)')
    with pytest.raises(OfflineAccessBlocked):
        sqlite3.connect('file:///Users/offline-forbidden-uri.sqlite3?mode=ro',uri=True)

@pytest.mark.parametrize('entry',['connection','cursor'])
def test_bound_attach_validated_per_execution(guards,tmp_path,entry):
    with sqlite3.connect(':memory:') as conn:
        target=conn if entry=='connection' else conn.cursor()
        path=tmp_path/'attached.sqlite3'
        target.execute('ATTACH DATABASE ? AS fixture',(str(path),))
        target.execute('CREATE TABLE fixture.allowed (id INTEGER)')
        target.execute('DETACH DATABASE fixture')
        with pytest.raises(OfflineAccessBlocked):
            target.execute('ATTACH DATABASE ? AS fixture',('/Users/offline-forbidden-bound.sqlite3',))
        target.execute('ATTACH DATABASE :path AS fixture',{'path':str(path)})
    assert path.exists()


@pytest.mark.parametrize('entry',['connection','cursor'])
def test_attach_script_and_alias_paths(guards,tmp_path,entry):
    path=tmp_path/'script.sqlite3'
    with sqlite3.dbapi2.connect(':memory:') as conn:
        target=conn if entry=='connection' else conn.cursor()
        target.executescript("ATTACH DATABASE '"+str(path)+"' AS fixture; CREATE TABLE fixture.t(id INTEGER);")
        with pytest.raises(sqlite3.DatabaseError):
            target.executescript("ATTACH DATABASE '/Users/offline-forbidden-script.sqlite3' AS forbidden;")
        link=tmp_path/'outside-link'; link.symlink_to('/Users')
        with pytest.raises(OfflineAccessBlocked):
            conn.cursor().execute('ATTACH DATABASE ? AS linked',(str(link/'offline-forbidden-link.sqlite3'),))


def test_connection_factory_semantics_preserved(guards,tmp_path):
    from backend.resource_management.lexicon_versions import VersionedLexiconConnection
    with sqlite3.connect(tmp_path/'factory.sqlite3',factory=VersionedLexiconConnection) as conn:
        assert isinstance(conn,VersionedLexiconConnection)
        conn.execute('CREATE TABLE fixture(id INTEGER)')
        conn.execute('ATTACH DATABASE ? AS second',(str(tmp_path/'second.sqlite3'),))
    with sqlite3.connect(':memory:',5.0,0,'',True,VersionedLexiconConnection) as conn:
        assert isinstance(conn,VersionedLexiconConnection)


def test_unwrapped_parameterized_attach_fails_closed(guards,tmp_path):
    with sqlite3.connect(':memory:') as conn:
        raw_cursor=sqlite3.Connection.cursor(conn)
        with pytest.raises(sqlite3.DatabaseError):
            raw_cursor.execute('ATTACH DATABASE ? AS forbidden',('/Users/offline-forbidden-raw.sqlite3',))
