"""A version change must preserve decisions, immutable evidence and completed deliveries."""
import sqlite3

from backend.audit_agent.geographic_execution_store import GeographicExecutionStore
from backend.audit_agent.geographic_status import _verified_projection
from test_geographic_runtime_pipeline import confirmed, runtime
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content


def test_completed_legacy_run_migrates_and_reopens_without_external_work(
    confirmed, tmp_path, monkeypatch
):
    with monkeypatch.context() as legacy:
        legacy.setattr(GeographicExecutionStore, 'open_execution', GeographicExecutionStore.create_legacy)
        runner, _, _ = runtime(confirmed, tmp_path)
        report, _ = runner.run()
    assert report['collected'] == report['handed_off'] == 1

    plan = runner.bridge.plan
    execution_id, digest = plan['execution_id'], plan['plan_digest']
    store = runner.store
    original = store.load(execution_id, digest)
    projected = _verified_projection(store.path, execution_id, digest)

    def deliveries():
        with sqlite3.connect(store.path) as db:
            return db.execute(
                'SELECT idempotency_key, raw_digest, receipt FROM geographic_deliveries '
                'WHERE execution_id=? ORDER BY idempotency_key', (execution_id,)
            ).fetchall()

    originals = deliveries()
    assert len(originals) == 1
    store.migrate_legacy(execution_id, digest)
    assert store.load(execution_id, digest) == original
    assert _verified_projection(store.path, execution_id, digest) == projected

    restarted, events, model = runtime(confirmed, tmp_path)
    assert restarted.run()[0] == report
    assert not model.calls
    assert not [event for event in events if event[0] in ('capture', 'detail', 'delivery')]
    assert deliveries() == originals

    # Export the current revision, including any new durable scheduling operations
    # performed after migration. Restoring only the saved pre-migration row loses them.
    after_restart = store.load(execution_id, digest)
    store.export_legacy(execution_id, digest)
    assert store.load(execution_id, digest) == after_restart
    assert _verified_projection(store.path, execution_id, digest) == projected
