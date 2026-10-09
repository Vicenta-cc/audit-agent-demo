"""Canonical audit -> immutable snapshot -> authorized report tools; no provider."""
import hashlib
import json
import sqlite3
import pytest
from test_pass_report import seed_audit, R31ReportRuntime
from hermes_m0.real_report_repository import _evidence_times
from hermes_m0.runtime import configure_real_report_runtime


@pytest.mark.parametrize('payload, expected', [
    ({}, (None,None)),
    ({'timestamp_start':0,'timestamp_end':0}, (0,0)),
    ({'timestamp_start':12.5,'timestamp_end':14}, (12.5,14)),
    ({'timestamp_start':2}, (2,None)),
    ({'timestamp_start':9,'timestamp_end':2}, (None,None)),
    ({'timestamp_start':-1,'timestamp_end':float('inf')}, (None,None)),
    ({'timestamp_start':True,'timestamp_end':'04:15'}, (None,None)),
])
def test_missing_or_invalid_frozen_times_are_not_invented(payload,expected):
    assert _evidence_times(payload)==expected


def test_new_frozen_times_reach_tools_and_live_changes_cannot_replace_them(tmp_path):
    source, store = seed_audit(tmp_path,decision='review',risk='medium')
    with sqlite3.connect(store.db_path) as db:
        row = db.execute('SELECT id,result_json FROM audit_results').fetchone()
        result=json.loads(row[1]);result['evidence_items']=[
            {'evidence_id':'ocr:price','primary_modality':'ocr','source':'video:1/frame:f0001',
             'ocr_text':'公示价格80元','start':0,'end':2.5,'reason':'价格证据'},
            {'evidence_id':'asr:price','primary_modality':'asr','source':'video:1/asr:1',
             'text':'口播价格100元','start':20,'end':24,'reason':'口播证据'},
        ]
        db.execute('UPDATE audit_results SET result_json=? WHERE id=?',(json.dumps(result),row[0]))
    published=R31ReportRuntime(store).generate('new-search-task',source=source,checkpoint_path=tmp_path/'checkpoints.sqlite3')
    snapshot=store.get_source_snapshot(published.report_version_id)
    version=store.get_version(published.report_version_id)
    # The mutable audit output now disagrees with the frozen report.
    with sqlite3.connect(store.db_path) as db:
        result['evidence_items'][0]['start']=999
        db.execute('UPDATE audit_results SET result_json=? WHERE id=?',(json.dumps(result),row[0]))
    before=hashlib.sha256(store.db_path.read_bytes()).hexdigest()
    runtime=configure_real_report_runtime(store.db_path, report_version_id=published.report_version_id,
        expected_database_sha256=before,expected_content_hash=version['content_hash'],
        expected_snapshot_hash=snapshot['snapshot_hash'],ledger_path=tmp_path/'ledger.sqlite3')
    runtime.bind_session('time-test')
    overview=json.loads(runtime.dispatch('read_report',{},session_id='time-test'))
    post=overview['data']['post_previews'][0]['ref']
    directory=json.loads(runtime.dispatch('list_evidence',{'post_ref':post},session_id='time-test'))
    assert directory['ok'],directory
    details=json.loads(runtime.dispatch('read_evidence',{'evidence_refs':[
        item['ref'] for item in directory['data']['candidates']]},session_id='time-test'))
    assert details['ok'],details
    serialized=json.dumps(details,ensure_ascii=False)
    assert '"timestamp_start_seconds": 0.0' in serialized
    assert '"timestamp_end_seconds": 2.5' in serialized
    assert '"timestamp_start_seconds": 20.0' in serialized
    assert '"timestamp_end_seconds": 24.0' in serialized
    # Read the immutable repository projection used by both report entries.
    items=list(runtime.repository.fixture.evidence) if hasattr(runtime.repository,'fixture') else []
    if not items:
        items=[runtime.repository.evidence(i) for i in runtime.repository.snapshot.evidence_ids]
    values={i.type:(i.content.timestamp_start,i.content.timestamp_end) for i in items}
    assert values['ocr']==(0,2.5) and values['asr']==(20,24)
    assert hashlib.sha256(store.db_path.read_bytes()).hexdigest()==before
