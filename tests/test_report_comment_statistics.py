"""Comment audit populations are distinct from selected report evidence."""
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.reporting.comment_statistics import snapshot_comment_coverage
from backend.reporting.presentation_projection import build_presentation_projection
from backend.reporting.runtime import R31ReportRuntime
from hermes_m0.runtime import configure_real_report_runtime
from test_pass_report import seed_audit


def comment(identifier, status='completed', risk='none', **extra):
    return dict(comment_id=identifier, audit_status=status, risk_level=risk, **extra)


def test_legacy_raw_and_new_compact_share_counts_without_double_counting():
    rows = [comment('r1', risk='high'), comment('r2', risk='medium'), comment('normal'),
            comment('failed', 'failed', 'high'), comment('pending', 'pending', 'unknown'),
            comment('queued', 'queued', 'none'), comment('unknown', '', '')]
    expected = snapshot_comment_coverage([{'comments': rows}])
    assert expected == snapshot_comment_coverage([{'raw_content_payload': {'comments': rows}}])
    assert expected == snapshot_comment_coverage([{'comments': rows, 'raw_content_payload': {'comments': rows}}])
    assert expected['total'] == 7
    assert [expected[k] for k in ('completed', 'failed', 'pending', 'unknown', 'risk', 'no_risk')] == [3, 1, 2, 1, 2, 1]
    assert sum(expected[k] for k in ('completed', 'failed', 'pending', 'unknown')) == expected['total']


def test_raw_and_compact_union_is_per_post_and_does_not_need_an_account():
    coverage = snapshot_comment_coverage([
        {'comments': [comment('same'), comment('compact', risk='low')],
         'raw_content_payload': {'comments': [comment('same', risk='high'), comment('raw')]}},
        {'comments': [comment('same')]},
    ])
    assert coverage['total'] == coverage['completed'] == 4
    assert coverage['risk'] == coverage['no_risk'] == 2


def test_absent_comment_snapshot_is_unknown_but_empty_list_is_zero():
    absent = snapshot_comment_coverage([{}, {'comments': [comment('c')]}])
    assert not absent['available'] and absent['missing_post_count'] == 1
    assert absent['total'] is absent['completed'] is absent['risk'] is None
    empty = snapshot_comment_coverage([{'comments': []}])
    assert empty['available'] and empty['total'] == empty['risk'] == 0


def test_completed_unknown_risk_is_not_a_safe_comment():
    coverage = snapshot_comment_coverage([{'comments': [comment('c', risk='unavailable')]}])
    assert coverage['completed'] == coverage['risk_unknown'] == 1
    assert coverage['risk'] == coverage['no_risk'] == 0


@pytest.mark.parametrize('value', [{}, [None], [{'audit_status': 'completed'}]])
def test_malformed_comment_records_are_not_silently_reported_as_zero(value):
    with pytest.raises(ValueError):
        snapshot_comment_coverage([{'comments': value}])


def test_page_counts_all_frozen_comments_independent_of_account_cards_and_evidence():
    snapshot = SimpleNamespace(posts=[SimpleNamespace(payload={'comments': [comment('r1', risk='high'), comment('r2', risk='low'), comment('n')], 'platform': 'dy'})])
    # Deliberately include a conflicting old aggregate: the frozen rows win.
    document = {'posts': [], 'standalone_risk_posts': [], 'audit_findings': [], 'investigation_findings': [], 'ordered_sections': [], 'report_metadata': {}, 'statistics': {}, 'source_coverage': {'comments': {'completed': 999}},
                'evidence': [{'evidence_ref': 'e1', 'evidence_type': 'comment', 'support_type': 'direct'},
                             {'evidence_ref': 'e2', 'evidence_type': 'comment', 'support_type': 'counter_evidence'}]}
    view = build_presentation_projection(document, snapshot=snapshot, account_projection=None)
    stats = view['statistics']
    assert stats['independently_reviewed_comments'] == 3
    assert stats['comment_own_risk'] == 2
    assert stats['direct_comment_evidence_count'] == 1
    assert stats['comment_audit_coverage']['total'] == 3


@pytest.mark.parametrize('risk', ['none', 'medium'])
def test_published_page_and_read_report_share_coverage_without_changing_frozen_content(tmp_path, risk):
    rows = [comment('c1', risk=risk), comment('c2'), comment('c3', 'failed', 'unavailable')]
    source, store = seed_audit(tmp_path, decision='pass' if risk == 'none' else 'review', risk=risk, comments=rows)
    published = R31ReportRuntime(store).generate('new-search-task', source=source, checkpoint_path=tmp_path/'checkpoints.sqlite3')
    version = store.get_version(published.report_version_id)
    page = store.get_presentation_projection(published.report_version_id)
    with store._connect() as conn:
        frozen_hash = conn.execute('SELECT snapshot_hash FROM report_source_snapshots WHERE report_version_id=?', (published.report_version_id,)).fetchone()[0]
    digest = sha256(store.db_path.read_bytes()).hexdigest()
    service = configure_real_report_runtime(store.db_path, report_version_id=published.report_version_id,
        expected_database_sha256=digest, expected_content_hash=version['content_hash'], expected_snapshot_hash=frozen_hash,
        ledger_path=tmp_path/'tools.sqlite3')
    service.bind_session('stats')
    reply = json.loads(service.dispatch('read_report', {}, session_id='stats'))
    assert reply['ok'], reply
    data = reply['data']
    stats = data['statistics'] if risk == 'none' else data['report']['deterministic_statistics']
    for key in ('comment_audit_coverage', 'independently_reviewed_comments', 'comment_own_risk', 'direct_comment_evidence_count'):
        assert stats[key] == page['statistics'][key]
    assert stats['comment_audit_coverage']['total'] == 3
    assert stats['independently_reviewed_comments'] == 2
    assert stats['comment_audit_coverage']['failed'] == 1
    assert stats['comment_own_risk'] == (0 if risk == 'none' else 1)
    assert sha256(store.db_path.read_bytes()).hexdigest() == digest
    assert not json.loads(service.dispatch('read_report', {'report_id': 'another'}, session_id='stats'))['ok']
