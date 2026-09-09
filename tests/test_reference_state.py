"""Cross-process navigation must preserve identities and authorization fences."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_m0.account_activity_refs import AccountActivityReferenceRegistry
from hermes_m0.ledger import ToolExecutionLedger
from hermes_m0.reference_state import save, restore_legacy_transcript
from hermes_m0.refs import ReferenceError
from hermes_m0.report_task_service import ReportTaskInvestigationToolService
from hermes_m0.repository import InvestigationRepository

FIXTURE = Path(__file__).resolve().parents[1] / 'hermes_m0/fixtures/report_2272c3692807.json'
SID = 'session-a'


def service(path, sid=SID):
    result = ReportTaskInvestigationToolService(
        InvestigationRepository.load(FIXTURE), ledger=ToolExecutionLedger(path))
    result.bind_session(sid)
    return result


def call(svc, name, args, call_id):
    return json.loads(svc.execute_tool_call(
        session_id=SID, tool_call_id=call_id, tool_name=name, args=args,
        next_call=lambda a: svc.dispatch(name, a, session_id=SID)))


def test_report_chain_survives_reconstruction_and_cached_tool_replay(tmp_path):
    path = tmp_path / 'ledger.db'
    first = service(path)
    report = call(first, 'read_report', {}, '1')
    category = report['data']['category_previews'][0]['ref']
    second = service(path)
    assert SID in second.restored_reference_sessions
    assert call(second, 'read_report', {}, '1') == report
    listing = call(second, 'list_category_posts', {'category_ref': category}, '2')
    assert listing['ok'], listing
    post = listing['data']['posts'][0]['ref']
    third = service(path)
    details = call(third, 'read_posts', {'post_refs': [post]}, '3')
    assert details['ok'], details
    third.bind_session('session-b')
    denied = json.loads(third.dispatch('read_posts', {'post_refs': [post]}, session_id='session-b'))
    assert denied['error']['code'] == 'cross_scope_ref'


def test_generation_reset_remains_invalid_after_restart(tmp_path):
    path = tmp_path / 'ledger.db'
    first = service(path)
    token = call(first, 'read_report', {}, '1')['data']['category_previews'][0]['ref']
    first.bind_session(SID, force_new_generation=True)
    second = service(path)
    denied = call(second, 'list_category_posts', {'category_ref': token}, '2')
    assert denied['error']['code'] == 'stale_revision_ref'


def test_corruption_is_not_silently_accepted(tmp_path):
    path = tmp_path / 'ledger.db'
    call(service(path), 'read_report', {}, '1')
    with sqlite3.connect(str(path) + '.references.sqlite3') as conn:
        conn.execute("UPDATE reference_states SET payload='{}'")
    with pytest.raises(ValueError, match='integrity'):
        service(path)


def test_changed_authorization_does_not_restore_old_tokens(tmp_path):
    path = tmp_path / 'ledger.db'
    first = service(path)
    token = call(first, 'read_report', {}, '1')['data']['category_previews'][0]['ref']
    repo = InvestigationRepository.load(FIXTURE)
    repo.content_hash = 'changed-content'
    second = ReportTaskInvestigationToolService(repo, ledger=ToolExecutionLedger(path))
    second.bind_session(SID)
    assert SID not in second.restored_reference_sessions
    denied = call(second, 'list_category_posts', {'category_ref': token}, '2')
    assert denied['error']['code'] == 'unknown_ref'


def test_legacy_transcript_aliases_require_matching_authoritative_result(tmp_path):
    old = service(tmp_path / 'old.db')
    history = []
    def record(name, args):
        cid = str(len(history))
        result = json.loads(old.dispatch(name, args, session_id=SID))
        history.extend([
            {'role': 'assistant', 'tool_calls': [{'id': cid, 'function': {
                'name': name, 'arguments': json.dumps(args)}}]},
            {'role': 'tool', 'tool_call_id': cid, 'content': json.dumps(result)}])
        return result
    category = record('read_report', {})['data']['category_previews'][0]['ref']
    post = record('list_category_posts', {'category_ref': category})['data']['posts'][0]['ref']
    record('read_posts', {'post_refs': [post]})
    record('list_evidence', {'post_ref': post})
    upgraded = service(tmp_path / 'new.db')
    restore_legacy_transcript(upgraded, SID, history)
    restored = service(tmp_path / 'new.db')
    assert call(restored, 'read_posts', {'post_refs': [post]}, 'detail')['ok']
    tampered = json.loads(json.dumps(history))
    body = json.loads(tampered[1]['content'])
    body['data']['injected'] = 'not in report'
    tampered[1]['content'] = json.dumps(body)
    rejected = service(tmp_path / 'tampered.db')
    restore_legacy_transcript(rejected, SID, tampered[:2])
    assert category not in rejected.refs._records


def test_account_records_origins_and_paging_survive_with_fences(tmp_path):
    from hermes_m0.reference_state import restore
    path = tmp_path / 'ledger.db'
    def make():
        svc = service(path)
        refs = AccountActivityReferenceRegistry()
        scope = svc.refs.scope(SID)
        refs.bind(session_id=SID, task_id=scope.task_id,
                  report_version_id=scope.report_version_id, snapshot_id=scope.snapshot_id,
                  report_revision=scope.revision, snapshot_hash=scope.snapshot_hash,
                  content_hash=scope.content_hash)
        svc.account_activity = SimpleNamespace(refs=refs, authorized_report_repositories=())
        return svc
    first = make()
    refs = first.account_activity.refs
    args = dict(kind='occurrence', object_id='comment-1', parent_account_id='account-1',
                corpus_revision='corpus-1', source_tool='list_account_occurrences',
                content_state='activity_preview')
    token = refs.expose(SID, **args)
    page_args = dict(account_id='account-1', kind='comment_author',
                     comment_target_account_id=None, risk_filter=None,
                     corpus_revision='corpus-1', ordered_occurrence_hash='order-1')
    cursor = refs.issue_cursor(SID, **page_args, next_offset=20)
    save(first, SID)
    second = make()
    assert restore(second, SID)
    restored = second.account_activity.refs
    assert restored.resolve(SID, token, expected_kind='occurrence', corpus_revision='corpus-1') == refs._records[token]
    assert restored.resolve_cursor(SID, cursor, **page_args).next_offset == 20
    with pytest.raises(ReferenceError) as error:
        restored.resolve(SID, token, expected_kind='occurrence', corpus_revision='corpus-2')
    assert error.value.code == 'stale_account_data'
    with pytest.raises(ReferenceError) as error:
        restored.resolve_cursor(SID, cursor, **{**page_args, 'risk_filter': 'risk_only'})
    assert error.value.code == 'cursor_query_mismatch'
    assert restored.expose(SID, **{**args, 'object_id': 'comment-2'}) != token
