"""Offline worker-to-delivery-to-analysis integration with actual lease FDs.

Crawler/model output and exit observation are fixtures; production geographic
execution, durable ingestion, queue transfer and lease ownership are exercised.
The report source is explicitly checkpointed as a static fixture; live-WAL
report visibility is a separate pre-existing production defect, not covered here.
"""
import os
import queue
import sqlite3
import threading
import time

import pytest

from test_geographic_runtime_pipeline import confirmed, runtime
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_geographic_runtime_worker import (
    AuthoredCrawler,
    test_real_worker_pipeline_audit_and_native_report as _run_worker,
)


def test_geographic_worker_borrow_survives_owner_close_until_delayed_analysis(
        confirmed, creation_stack, content, tmp_path, monkeypatch, request):
    from backend.audit_agent import crawler_route, pipeline
    from backend.audit_agent.geographic_pipeline import GeographicPipeline
    from backend.investigation_creation.adapters import R31ReportAdapter
    from backend.task_admission.resources import BorrowedRouteContext, acquire_account_handle

    monkeypatch.setattr(pipeline.settings, 'crawler_proxy_enabled', False)
    monkeypatch.setattr(pipeline.settings, 'crawler_camoufox_state_root', tmp_path / 'physical-profiles')
    monkeypatch.setattr(pipeline.settings, 'request_scheduler_db', tmp_path / 'scheduler.db')
    contract = crawler_route.route_contract()
    # Replace only the external exit probe, retaining real account/exit locks,
    # route admission, immutable route snapshot and owner/borrow implementation.
    monkeypatch.setattr(crawler_route, '_run_maintenance_probe', lambda bundle:
        contract.ProbeReceipt('service-direct', 1, 'probe', '203.0.113.9', time.time()))
    original_acquire = pipeline.acquire_route_execution
    bundles, detail_bundles, handles, observed = [], [], [], []
    closed_owner = threading.Event()
    current = []

    def acquire(*args, **kwargs):
        bundle = original_acquire(*args, **kwargs)
        bundles.append(bundle)
        handles.extend(bundle._handles)
        current[:] = [bundle]
        close = bundle.close
        def close_and_signal():
            close()
            if bundle in detail_bundles:
                # Owner closure must forbid new borrows, while the queued borrow
                # retains these same FDs until the real consumer finally drains.
                observed.append('owner_closed')
                assert bundle.pass_fds
                closed_owner.set()
        bundle.close = close_and_signal
        return bundle
    monkeypatch.setattr(pipeline, 'acquire_route_execution', acquire)

    detail = AuthoredCrawler.run_detail
    def routed_detail(self, **kwargs):
        bundle = kwargs['route_context']
        assert bundle is current[0] and bundle.account_id == kwargs['account_id']
        detail_bundles.append(bundle)
        result = detail(self, **kwargs)
        snapshot = bundle.snapshot
        receipt = contract.ProbeReceipt(snapshot.proxy_id, snapshot.config_revision,
            snapshot.context_id, snapshot.observed_exit_ip, snapshot.observed_exit_at,
            snapshot.exit_generation)
        for item in result.contents:
            item['collection_route_provenance'] = contract.collection_route_provenance(
                snapshot, receipt, observation_id='offline-detail-observation',
                source_task_id=bundle.execution_id, source_batch_id='offline-detail-batch',
                collected_at=time.time(), context_id=snapshot.context_id)
        return result
    monkeypatch.setattr(AuthoredCrawler, 'run_detail', routed_detail)

    review = GeographicPipeline.review
    def review_with_live_route(self, *args, **kwargs):
        assert detail_bundles[-1].pass_fds
        observed.append('review')
        return review(self, *args, **kwargs)
    monkeypatch.setattr(GeographicPipeline, 'review', review_with_live_route)
    progress = GeographicPipeline.progress
    def settled_with_live_route(self, stage, payload):
        if stage == 'after_settle':
            assert detail_bundles[-1].pass_fds
            observed.append('settled')
        return progress(self, stage, payload)
    monkeypatch.setattr(GeographicPipeline, 'progress', settled_with_live_route)

    original_queue = queue.Queue
    class DelayedAnalysisQueue(original_queue):
        def put(self, entry, *args, **kwargs):
            if isinstance(entry, tuple) and entry and entry[0] == 'route_entry':
                _, ref, borrowed, borrow_context = entry
                assert isinstance(borrowed, BorrowedRouteContext), 'durable source lost its route borrow'
                assert borrow_context is not None
                assert ref['item']['collection_route_provenance']['source_account_id'] == borrowed.account_id
                # Production callback is downstream of the durable SQL commit.
                with confirmed[0].ingestion_store._connect() as db:
                    assert db.execute('SELECT 1 FROM geographic_deliveries WHERE content_id=?',
                        (ref['content_id'],)).fetchone()
                assert borrowed.pass_fds
                observed.append('queued')
            return super().put(entry, *args, **kwargs)
        def get(self, *args, **kwargs):
            entry = super().get(*args, **kwargs)
            if isinstance(entry, tuple) and entry and entry[0] == 'route_entry':
                assert closed_owner.wait(10), 'geographic source owner never closed'
                borrowed = entry[2]
                for fd in borrowed.pass_fds:
                    os.fstat(fd)
                assert borrowed.pass_fds
                assert acquire_account_handle('dy', borrowed.account_id) is None
                with borrowed.borrow(account_id=borrowed.account_id) as nested:
                    assert nested.pass_fds == borrowed.pass_fds
                observed.append('consumed_after_owner_closed')
            return entry
    monkeypatch.setattr(pipeline.queue, 'Queue', DelayedAnalysisQueue)

    generate_report = R31ReportAdapter.generate
    def generate_from_static_fixture(self, *args, **kwargs):
        # This test proves route/audit lifetime through a real static report.
        # CanonicalReportSource's immutable reader does not consume live WAL;
        # do not mistake this fixture boundary for validation of that behavior.
        with sqlite3.connect(creation_stack['resource_db']) as db:
            busy, log_frames, checkpointed = db.execute(
                'PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            assert busy == 0 and log_frames == checkpointed
        observed.append('static_report_checkpoint')
        return generate_report(self, *args, **kwargs)
    monkeypatch.setattr(R31ReportAdapter, 'generate', generate_from_static_fixture)

    try:
        _run_worker('confirmed', 'disabled', request, creation_stack, tmp_path, monkeypatch)
        assert observed.index('review') < observed.index('settled') < observed.index('queued')
        assert observed.index('queued') < observed.index('owner_closed') < observed.index('consumed_after_owner_closed')
        assert observed.index('consumed_after_owner_closed') < observed.index('static_report_checkpoint')
        assert len(detail_bundles) == 1
        assert all(not bundle.pass_fds for bundle in bundles)
        assert handles and all(handle.closed for handle in handles)
        fresh = acquire_account_handle('dy', detail_bundles[0].account_id)
        assert fresh is not None
        fresh.close()
    finally:
        # Idempotent cleanup avoids assertions on a second owner close.
        for bundle in bundles:
            type(bundle).close(bundle)


@pytest.mark.parametrize('interrupt_at', ['review', 'after_settle', 'stop_after_settle'])
def test_geographic_interruption_releases_actual_detail_fds(
        confirmed, tmp_path, monkeypatch, interrupt_at):
    from contextlib import contextmanager
    from backend.audit_agent.config import settings
    from backend.task_admission.resources import ExecutionLeaseBundle, acquire_account_handle

    monkeypatch.setattr(settings, 'crawler_browser_profile_root', tmp_path / 'profiles')
    monkeypatch.setattr(settings, 'task_resource_lock_dir', tmp_path / 'locks')
    stopped = [False]
    runner, _, _ = runtime(confirmed, tmp_path, stop=lambda: stopped[0])
    bundles, handles = [], []
    @contextmanager
    def route(account_id):
        bundle = ExecutionLeaseBundle('offline-geography', 'owner', account_id)
        assert bundle.acquire()
        assert bundle.acquire_exit_capacity('203.0.113.9')
        bundles.append(bundle)
        handles.extend(bundle._handles)
        try:
            yield bundle
        finally:
            bundle.close()
    runner.route_operation = route
    if interrupt_at == 'review':
        def interrupted_review(*args):
            assert bundles[-1].pass_fds
            raise RuntimeError('offline review interrupted')
        runner.execution.review = interrupted_review
    else:
        def interrupted_progress(report):
            if report.get('stage') == 'after_settle':
                assert bundles[-1].pass_fds
                if interrupt_at == 'stop_after_settle':
                    stopped[0] = True
                else:
                    raise RuntimeError('offline settlement interrupted')
        runner.on_progress = interrupted_progress
    if interrupt_at == 'stop_after_settle':
        report, output = runner.run()
        assert report['handed_off'] == 0 and output.contents == []
    else:
        with pytest.raises(RuntimeError, match='offline .* interrupted'):
            runner.run()
    assert len(bundles) >= 2
    assert all(not bundle.pass_fds for bundle in bundles)
    assert all(handle.closed for handle in handles)
    fresh = acquire_account_handle('dy', 'test-account')
    assert fresh is not None
    fresh.close()
