from copy import deepcopy
import json
import pytest
from audit_agent.ingestion import IngestionStore
from audit_agent.scoped_selection import digest
from test_geographic_runtime_store import runtime_plan
from test_scoped_selection_store import stored_occurrence, complete_query


def setup_delivery(tmp_path, monkeypatch):
    from audit_agent.geographic_execution_store import GeographicExecutionStore
    from audit_agent.geographic_delivery import GeographicDelivery
    p=runtime_plan()
    o=stored_occurrence(p,'one')
    s=GeographicExecutionStore(tmp_path/'jobs.db',p['binding']|p['runtime_binding'],p['plan_digest'],lambda db:True)
    s.create(p); complete_query(s,[o])
    monkeypatch.setattr('audit_agent.scoped_selection_store.select_next_batch',lambda *args:dict(status='ready',picks=[o],query_id='q',tier='local'))
    s.reserve_next('e','reserve'); a=s.begin_attempt('e','one','attempt','account')
    c=deepcopy(o['candidate']); c['raw_source']={'post':{'aweme_id':'one','desc':'current'},'comments':[]}
    b={k:a[k] for k in ('execution_id','plan_digest','query_id','platform','content_key','occurrence_id','attempt_id')}
    b['content_digest']=digest(c)
    s.record_receipt('e',a['attempt_id'],'receipt',b|{'content':c});s.settle('e',a['attempt_id'],'settle')
    s.record_review('e',a['attempt_id'],'review',{'allowed':True})
    ing=IngestionStore(tmp_path/'ingestion.sqlite')
    delivery=GeographicDelivery(ing,s,p,tmp_path/'raw')
    key=digest({k:b[k] for k in ('execution_id','plan_digest','attempt_id','content_digest')})
    return delivery,ing,s,c,b,key


def test_current_raw_survives_shared_old_and_retry(tmp_path,monkeypatch):
    d,ing,s,c,b,key=setup_delivery(tmp_path,monkeypatch)
    batch=tmp_path/'old.json';batch.write_text(json.dumps({'platform':'dy','task_id':'old-job','items':[{'aweme_id':'one','desc':'OLD'}]}))
    ing.ingest_batch(batch,tmp_path/'oldraw')
    r=d.deliver(c,idempotency_key=key,binding=b)
    assert r['refs'][0]['item']['desc']=='current'
    assert d.deliver(c,idempotency_key=key,binding=b)==r
    assert ing.pending_for_task('job')[0]['item']['desc']=='current'
    assert ing.validated_refs_for_task('job')[0]['item']['desc']=='current'
    assert ing.validated_refs_for_task('old-job')[0]['item']['desc']=='OLD'
    path=r['refs'][0]['raw_item_path']
    payload=json.loads(open(path).read());payload['item']['desc']='tampered'
    open(path,'w').write(json.dumps(payload))
    with pytest.raises(ValueError,match='evidence'):
        ing.pending_for_task('job')
    with pytest.raises(ValueError,match='evidence'):
        d.deliver(c,idempotency_key=key,binding=b)


def test_delivery_rejects_wrong_binding_and_expired_lease(tmp_path,monkeypatch):
    d,ing,s,c,b,key=setup_delivery(tmp_path,monkeypatch)
    with pytest.raises(ValueError):d.deliver(c,idempotency_key=key,binding=b|{'attempt_id':'other'})
    s.lease_fence=lambda db:False
    with pytest.raises(ValueError,match='lease'):d.deliver(c,idempotency_key=key,binding=b)
    assert ing.pending_for_task('job')==[]


def test_frozen_report_basis_is_bound_to_raw_evidence(tmp_path,monkeypatch):
    from audit_agent.geographic_delivery import delivery_basis_for_job
    d,ing,s,c,b,key=setup_delivery(tmp_path,monkeypatch)
    receipt=d.deliver(c,idempotency_key=key,binding=b)
    assert delivery_basis_for_job(ing,'job')==[receipt['selection_basis']]
    assert delivery_basis_for_job(ing,'other')==[]
    with ing._connect() as db:
        saved=json.loads(db.execute('SELECT receipt FROM geographic_deliveries').fetchone()[0])
        saved['selection_basis']['source_job_id']='other'
        db.execute('UPDATE geographic_deliveries SET receipt=?',(json.dumps(saved),))
    with pytest.raises(ValueError,match='basis'):
        delivery_basis_for_job(ing,'job')


def test_new_process_recovers_only_current_evidence(tmp_path,monkeypatch):
    import os
    from pathlib import Path
    import subprocess
    import sys
    d,ing,s,c,b,key=setup_delivery(tmp_path,monkeypatch)
    receipt=d.deliver(c,idempotency_key=key,binding=b)
    result=subprocess.run([sys.executable,'-m','experiments.geographic_runtime_integration.replay_delivery',str(ing.db_path)],
                          capture_output=True,text=True,check=True,env=dict(os.environ))
    recovered=json.loads(result.stdout)
    assert recovered['refs'][0]['item']['desc']=='current'
    assert recovered['basis']==[receipt['selection_basis']]
    # Durable ingestion can be reconciled before P4's mark_delivery was saved.
    assert s.load('e',s.expected_plan_digest)['deliveries']=={}
    assert d.deliver(c,idempotency_key=key,binding=b)==receipt


def test_ledger_failure_rolls_back_membership_and_retry_reuses_evidence(tmp_path,monkeypatch):
    import sqlite3
    d,ing,s,c,b,key=setup_delivery(tmp_path,monkeypatch)
    with ing._connect() as db:
        db.execute("CREATE TRIGGER fail_delivery BEFORE INSERT ON geographic_deliveries BEGIN SELECT RAISE(ABORT,'injected crash'); END")
    with pytest.raises(sqlite3.IntegrityError,match='injected crash'):
        d.deliver(c,idempotency_key=key,binding=b)
    assert ing.selection_membership_for_task('job')==[]
    with ing._connect() as db:db.execute('DROP TRIGGER fail_delivery')
    receipt=d.deliver(c,idempotency_key=key,binding=b)
    assert len(ing.selection_membership_for_task('job'))==1
    assert receipt['refs'][0]['item']['desc']=='current'


def test_lease_expiring_before_delivery_commit_rolls_back(tmp_path,monkeypatch):
    d,ing,s,c,b,key=setup_delivery(tmp_path,monkeypatch)
    checks=[]
    def fence(db):
        checks.append(True)
        return len(checks)==1
    s.lease_fence=fence
    with pytest.raises(ValueError,match='lease'):
        d.deliver(c,idempotency_key=key,binding=b)
    assert ing.selection_membership_for_task('job')==[]


def test_formal_membership_without_ledger_cannot_resume_legacy_raw(tmp_path):
    ing=IngestionStore(tmp_path/'legacy.sqlite')
    batch=tmp_path/'old.json';batch.write_text(json.dumps({'platform':'dy','task_id':'job','items':[{'aweme_id':'one','desc':'unapproved'}]}))
    ing.ingest_batch(batch,tmp_path/'oldraw')
    assert len(ing.pending_for_task('job'))==1
    with ing._connect() as db:
        db.execute('CREATE TABLE jobs(id TEXT, geographic_plan TEXT)')
        db.execute('INSERT INTO jobs VALUES (?,?)',('job',json.dumps({'schema_version':'confirmed'})))
    for load in (ing.pending_for_task,ing.refs_for_task,ing.validated_refs_for_task):
        with pytest.raises(ValueError,match='no durable delivery'):
            load('job')
