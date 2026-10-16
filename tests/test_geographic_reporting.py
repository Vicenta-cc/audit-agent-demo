from copy import deepcopy
import json
import sqlite3

import pytest
from tests.test_pass_report import seed_audit, legacy_pass_runtime
from backend.reporting.integration_source import build_immutable_snapshot


def test_geographic_snapshot_freezes_basis_and_keeps_risk_counts(tmp_path):
    source, store = seed_audit(tmp_path)
    original = build_immutable_snapshot(source, 'new-search-task')
    context = {'coverage': {'source_job_id': 'new-search-task', 'candidate_occurrences': 9,
                            'collected': 2, 'handed_off': 1, 'collected_not_delivered': 1},
               'selection_basis': {'source_job_id': 'new-search-task', 'content_key': '10001',
                                   'geo_tier': 'local', 'risk_band': 'weak', 'detail_review': 'same_tier_eligible'}}
    source.geographic_context = lambda row: deepcopy(context)
    frozen = build_immutable_snapshot(source, 'new-search-task')
    assert frozen.posts[0].payload['selection_basis'] == context['selection_basis']
    assert frozen.statistics['geographic_coverage'] == [context['coverage']]
    assert frozen.statistics['risk_level'] == original.statistics['risk_level']
    assert frozen.statistics['canonical_posts'] == original.statistics['canonical_posts']
    assert frozen.snapshot_hash != original.snapshot_hash
    result = legacy_pass_runtime(store).generate('new-search-task', source=source,
        model_client=None, checkpoint_path=tmp_path / 'checkpoint.sqlite3')
    context['selection_basis']['geo_tier'] = 'conflict'
    with sqlite3.connect(store.db_path) as db:
        payload = json.loads(db.execute('SELECT payload_json FROM report_snapshot_posts WHERE report_version_id=?',
                                       (result.report_version_id,)).fetchone()[0])
    assert payload['selection_basis']['geo_tier'] == 'local'
    view = store.get_presentation_projection(result.report_version_id)
    assert view['statistics']['geographic_coverage'][0]['candidate_occurrences'] == 9
    assert view['statistics']['selection_basis'][0]['geo_tier'] == 'local'
    detail = store.get_presentation_post_detail(result.report_version_id, post_ref='post-001')
    assert detail['selection_basis']['geo_tier'] == 'local'


def test_non_geographic_snapshot_omits_new_fields(tmp_path):
    source, _ = seed_audit(tmp_path)
    snapshot = build_immutable_snapshot(source, 'new-search-task')
    assert 'geographic_coverage' not in snapshot.statistics
    assert 'selection_basis' not in snapshot.posts[0].payload


@pytest.mark.parametrize('geographic', [False, True])
def test_published_report_reader_verifies_geographic_and_legacy_snapshots(tmp_path, geographic):
    from hermes_m0.real_report_repository import _load_report_graph, PublishedReportLoadError
    source, store = seed_audit(tmp_path)
    if geographic:
        source.geographic_context = lambda row: {
            'coverage': {'source_job_id': 'new-search-task', 'handed_off': 1},
            'selection_basis': {'source_job_id': 'new-search-task', 'content_key': '10001', 'geo_tier': 'local'},
        }
    result = legacy_pass_runtime(store).generate('new-search-task', source=source,
        model_client=None, checkpoint_path=tmp_path / 'reader-checkpoint.sqlite3')
    with sqlite3.connect(store.db_path) as db:
        db.row_factory = sqlite3.Row
        content_hash = db.execute('SELECT content_hash FROM report_versions WHERE id=?',
                                  (result.report_version_id,)).fetchone()[0]
        row = db.execute('SELECT snapshot_hash,statistics_json FROM report_source_snapshots WHERE report_version_id=?',
                         (result.report_version_id,)).fetchone()
        expected = dict(report_version_id=result.report_version_id, expected_content_hash=content_hash,
                        expected_snapshot_hash=row['snapshot_hash'])
        # Exercise the same complete graph/hash verifier used by report chat.
        assert _load_report_graph(db, **expected)
        if geographic:
            statistics = json.loads(row['statistics_json'])
            statistics['geographic_coverage'][0]['handed_off'] = 99
            # Simulate an externally tampered archive, bypassing its normal
            # immutable-write guard only in this disposable fixture database.
            db.execute('DROP TRIGGER report_snapshots_no_update')
            db.execute('UPDATE report_source_snapshots SET statistics_json=? WHERE report_version_id=?',
                       (json.dumps(statistics), result.report_version_id))
            with pytest.raises(PublishedReportLoadError, match='Snapshot hash does not match frozen payloads'):
                _load_report_graph(db, **expected)


def test_geographic_report_refuses_missing_delivery(tmp_path):
    source, _ = seed_audit(tmp_path)
    with sqlite3.connect(source.db_path) as db:
        db.execute('UPDATE jobs SET control=? WHERE id=?',
                   (json.dumps({'geographic_selection': {'execution_id': 'missing'}}), 'new-search-task'))
    with sqlite3.connect(source.db_path) as db:
        assert db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    with pytest.raises(ValueError, match='geographic'):
        build_immutable_snapshot(source, 'new-search-task')


from test_geographic_runtime_pipeline import confirmed, runtime, creation_stack, content


def test_real_delivery_context_is_readonly_source_bound_and_tamper_checked(confirmed, tmp_path):
    from backend.reporting.integration_source import CanonicalReportSource
    runner, _, _ = runtime(confirmed, tmp_path)
    report, _ = runner.run()
    adapter, cfg = confirmed
    job_id = cfg['_verified_geographic_binding']['job_id']
    with sqlite3.connect(adapter.job_store.db_path) as db:
        db.execute('UPDATE jobs SET control=? WHERE id=?',
                   (json.dumps({'geographic_selection': report}), job_id))
    with sqlite3.connect(adapter.job_store.db_path) as db:
        assert db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    source = CanonicalReportSource(adapter.job_store.db_path, tmp_path)
    context = source.geographic_context({'job_id': job_id, 'content_key': 'same'})
    assert context['coverage']['handed_off'] == 1
    basis = context['selection_basis']
    assert basis['source_job_id'] == job_id and basis['content_key'] == 'same'
    assert basis['geo_tier'] == basis['initial_geo_tier'] == 'local'
    assert basis['initial_evidence_digest'] != basis['detail_evidence_digest']
    with pytest.raises(ValueError, match='exactly one'):
        source.geographic_context({'job_id': job_id, 'content_key': 'other'})
    with sqlite3.connect(adapter.job_store.db_path) as db:
        row = db.execute('SELECT idempotency_key,receipt FROM geographic_deliveries').fetchone()
        receipt = json.loads(row[1]); receipt['selection_basis']['geo_tier'] = 'conflict'
        db.execute('UPDATE geographic_deliveries SET receipt=? WHERE idempotency_key=?',
                   (json.dumps(receipt), row[0]))
    with sqlite3.connect(adapter.job_store.db_path) as db:
        assert db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
    with pytest.raises(ValueError, match='basis binding mismatch'):
        source.geographic_context({'job_id': job_id, 'content_key': 'same'})
