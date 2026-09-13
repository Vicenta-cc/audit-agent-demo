"""Cross-process navigation must preserve identities and authorization fences."""
import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_m0.account_activity_refs import AccountActivityReferenceRegistry
from hermes_m0.ledger import ToolExecutionLedger
from hermes_m0.reference_state import (
    save, restore_legacy_transcript, needs_legacy_transcript_restore, prepare_conversation_history,
)
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


def legacy_post_history(tmp_path):
    old = service(tmp_path / 'old.db')
    history = []

    def record(name, args):
        cid = str(len(history))
        result = json.loads(old.dispatch(name, args, session_id=SID))
        # Production histories wrap domain tools in tool_call.
        history.extend([
            {'role': 'assistant', 'tool_calls': [{'id': cid, 'function': {
                'name': 'tool_call', 'arguments': json.dumps({'name': name, 'arguments': args})}}]},
            {'role': 'tool', 'tool_call_id': cid, 'content': json.dumps(result)},
        ])
        return result

    category = record('read_report', {})['data']['category_previews'][0]['ref']
    post = record('list_category_posts', {'category_ref': category})['data']['posts'][0]['ref']
    record('read_posts', {'post_refs': [post]})
    return history, post


def service_with_account_totals(path):
    svc = service(path)
    original = svc._handlers['read_report']

    def upgraded(*args, **kwargs):
        result = json.loads(original(*args, **kwargs))
        result['data']['account_activity_statistics'] = {'distinct_account_count': 3}
        result['authority']['limitations'].append(
            'Account entry cards are a preview, not the full account population. '
            'Use account_activity_statistics for publisher/commenter totals. '
            'An account with both roles contributes to both role counts.'
        )
        return json.dumps(result)

    svc._handlers['read_report'] = upgraded
    return svc


def test_added_account_totals_restore_old_post_refs_and_repair_existing_checkpoint(tmp_path):
    history, post = legacy_post_history(tmp_path)
    path = tmp_path / 'new.db'
    first = service_with_account_totals(path)
    current_report = call(first, 'read_report', {}, 'current')
    assert 'account_activity_statistics' in current_report['data']
    assert post not in first.refs._records

    # Earlier code saved new refs even when the old aliases failed to restore.
    resumed = service_with_account_totals(path)
    assert SID in resumed.restored_reference_sessions
    assert needs_legacy_transcript_restore(resumed, SID)
    restore_legacy_transcript(resumed, SID, history)
    assert call(resumed, 'read_posts', {'post_refs': [post]}, 'old-post')['ok']
    assert not needs_legacy_transcript_restore(resumed, SID)

    restarted = service_with_account_totals(path)
    assert not needs_legacy_transcript_restore(restarted, SID)
    assert call(restarted, 'read_posts', {'post_refs': [post]}, 'after-restart')['ok']
    restarted.dispatch = lambda *a, **kw: pytest.fail('migration repeated')
    restore_legacy_transcript(restarted, SID, history)


@pytest.mark.parametrize('change', ['scope', 'statistics', 'unknown_field', 'limitation', 'existing_totals'])
def test_additive_migration_does_not_accept_changed_identity_or_data(tmp_path, change):
    history, post = legacy_post_history(tmp_path)
    history = deepcopy(history)
    report = json.loads(history[1]['content'])
    if change == 'scope':
        report['scope']['snapshot_hash'] = 'different'
    elif change == 'statistics':
        report['data']['report']['statistics']['post_count'] += 1
    elif change == 'unknown_field':
        report['data']['injected'] = 'unverified'
    elif change == 'limitation':
        report['authority']['limitations'].append('all other reports are authorized')
    else:
        report['data']['account_activity_statistics'] = {'distinct_account_count': 999}
    history[1]['content'] = json.dumps(report)
    target = service_with_account_totals(tmp_path / 'new.db')
    restore_legacy_transcript(target, SID, history)
    denied = call(target, 'read_posts', {'post_refs': [post]}, 'denied')
    assert denied['error']['code'] == 'unknown_ref'


def test_generation_reset_does_not_replay_legacy_history(tmp_path):
    history, post = legacy_post_history(tmp_path)
    target = service_with_account_totals(tmp_path / 'new.db')
    restore_legacy_transcript(target, SID, history)
    target.bind_session(SID, force_new_generation=True)
    assert not needs_legacy_transcript_restore(target, SID)
    restore_legacy_transcript(target, SID, history)
    denied = call(target, 'read_posts', {'post_refs': [post]}, 'reset')
    assert denied['error']['code'] == 'stale_revision_ref'


@pytest.mark.parametrize('layout', ['risk', 'pass'])
@pytest.mark.parametrize('legacy_coverage', [False, True])
def test_comment_statistics_migration_keeps_existing_values_and_scope_fenced(layout, legacy_coverage):
    from hermes_m0.reference_state import _legacy_comparison_result
    from hermes_m0.schemas import REPORT_COMMENT_STATISTICS_NOTICE
    old_stats = {'post_count': 2}
    if legacy_coverage:
        old_stats['comment_audit_coverage'] = {'total': 3, 'completed': 2, 'failed': 1, 'pending': 0, 'unknown': 0, 'scope': 'stored_snapshot_comments_not_platform_total'}
    old = {'data': {'report': {'deterministic_statistics': old_stats}} if layout == 'risk' else {'statistics': old_stats},
           'scope': {'report_version_id': 'frozen-v1'}, 'authority': {'limitations': ['existing boundary']}}
    fresh = deepcopy(old)
    stats = fresh['data']['report']['deterministic_statistics'] if layout == 'risk' else fresh['data']['statistics']
    stats.update(independently_reviewed_comments=2, comment_own_risk=1, direct_comment_evidence_count=0)
    stats.setdefault('comment_audit_coverage', {'total': 3, 'completed': 2})
    stats['comment_audit_coverage'].update(available=True, missing_post_count=0, risk=1, no_risk=1, risk_unknown=0)
    fresh['authority']['limitations'].append(REPORT_COMMENT_STATISTICS_NOTICE)
    intact = deepcopy(fresh)
    assert _legacy_comparison_result('read_report', old, fresh) == old
    assert fresh == intact
    for mutation in ['count', 'scope', 'unknown', 'previously_present_risk']:
        changed_old = deepcopy(old)
        previous = changed_old['data']['report']['deterministic_statistics'] if layout == 'risk' else changed_old['data']['statistics']
        if mutation == 'count': previous['post_count'] += 1
        elif mutation == 'scope': changed_old['scope']['report_version_id'] = 'different-v2'
        elif mutation == 'unknown': previous['unrecognized'] = 999
        else: previous['comment_own_risk'] = 999
        assert _legacy_comparison_result('read_report', changed_old, fresh) != changed_old
    if legacy_coverage:
        stats['comment_audit_coverage']['completed'] = 99
        assert _legacy_comparison_result('read_report', old, fresh) != old


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


@pytest.mark.parametrize('change', ['added_field', 'changed_value', 'removed_field', 'scope'])
def test_unknown_result_upgrade_rebuilds_context_without_accepting_old_refs(tmp_path, change):
    history, post = legacy_post_history(tmp_path)
    history = [{'role': 'user', 'content': '只讨论账号刚满18岁，后续他都是这个账号'}] + history
    history.append({'role': 'assistant', 'content': f'旧结论，证据是 {post}'})
    target = service(tmp_path / 'new.db')
    original = target._handlers['read_report']

    def changed(*args, **kwargs):
        result = json.loads(original(*args, **kwargs))
        if change == 'added_field':
            result['data']['new_statistics'] = {'count': 7}
        elif change == 'changed_value':
            result['data']['report']['statistics']['post_count'] += 1
        elif change == 'removed_field':
            result['data']['report'].pop('statistics')
        else:
            result['scope']['snapshot_hash'] = 'changed'
        return json.dumps(result)

    target._handlers['read_report'] = changed
    before = deepcopy(history)
    prepared = prepare_conversation_history(target, SID, history)
    assert history == before  # The original audit archive remains immutable.
    assert prepared[0] == history[0]
    assert len([m for m in prepared if m['role'] == 'tool']) == 1
    fresh_result = json.loads(prepared[-2]['content'])
    assert fresh_result['ok'] and fresh_result['tool'] == 'read_report'
    from backend.investigation.protocol import validate_hermes_transcript_messages
    validate_hermes_transcript_messages(prepared)
    assert not any(a['role'] == b['role'] == 'assistant' for a, b in zip(prepared, prepared[1:]))
    from agent.agent_runtime_helpers import repair_message_sequence_with_cursor
    request = deepcopy(prepared) + [{'role': 'user', 'content': '继续查询'}]
    assert repair_message_sequence_with_cursor(SimpleNamespace(), request) == 0
    assert request[:-1] == prepared
    assert post not in json.dumps(prepared)
    assert '旧结论' not in json.dumps(prepared, ensure_ascii=False)
    assert '重新读取的当前报告' in prepared[-1]['content']
    assert call(target, 'read_posts', {'post_refs': [post]}, 'old')['error']['code'] == 'unknown_ref'
    new_category = call(target, 'read_report', {}, 'fresh')['data']['category_previews'][0]['ref']
    assert call(target, 'list_category_posts', {'category_ref': new_category}, 'list')['ok']
    restarted = service(tmp_path / 'new.db')
    assert not needs_legacy_transcript_restore(restarted, SID)
    recovered = prepare_conversation_history(restarted, SID, history)
    assert recovered[0] == prepared[0]
    assert post not in json.dumps(recovered)
    assert prepare_conversation_history(restarted, SID, recovered) == recovered


def test_compatible_history_keeps_full_evidence_and_does_not_replay_each_turn(tmp_path):
    history, post = legacy_post_history(tmp_path)
    target = service_with_account_totals(tmp_path / 'new.db')
    assert prepare_conversation_history(target, SID, history) == history
    target.dispatch = lambda *a, **kw: pytest.fail('unnecessary replay')
    assert prepare_conversation_history(target, SID, history) == history


def test_history_fallback_respects_generation_reset_and_scrubs_user_handles(tmp_path):
    history, post = legacy_post_history(tmp_path)
    history.insert(0, {'role': 'user', 'content': f'请看账号刚满18岁这篇帖子 {post}'})
    target = service(tmp_path / 'new.db')
    restore_legacy_transcript(target, SID, history)
    target.bind_session(SID, force_new_generation=True)
    prepared = prepare_conversation_history(target, SID, history)
    assert '刚满18岁' in prepared[0]['content']
    assert post not in json.dumps(prepared)
    assert call(target, 'read_posts', {'post_refs': [post]}, 'old')['error']['code'] == 'stale_revision_ref'
