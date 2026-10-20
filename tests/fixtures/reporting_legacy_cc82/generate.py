import dataclasses,gzip,hashlib,json,sqlite3,subprocess,sys,tempfile,types
from pathlib import Path
from contextlib import closing
from tests.test_pass_report import seed_audit
from backend.reporting.sqlite_snapshot import sqlite_snapshot
from hermes_m0.account_corpus import build_account_fixture
out=Path('tests/fixtures/reporting_legacy_cc82')
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    source,_=seed_audit(root,verdicts=[('pass','none'),('review','low'),('review','medium'),('review','high')])
    with closing(sqlite3.connect(source.db_path)) as db:
        db.execute("UPDATE jobs SET crawl_mode='creator',creator_id='stable-author' WHERE id='new-search-task'")
        for row in db.execute('SELECT id,result_json FROM audit_results').fetchall():
            payload=json.loads(row[1]);payload['content_title']='合成审核样本'
            payload['evidence_items']=[{'evidence_id':'ocr-review','primary_modality':'ocr','source':'video:1/frame:review','ocr_text':'相关内容需要复核'}]
            db.execute('UPDATE audit_results SET result_json=? WHERE id=?',(json.dumps(payload),row[0]))
        db.commit()
    # Make an independent consistent source, so the archived fixture contains
    # exactly the byte image hashed by the historical production builder.
    with sqlite_snapshot(source.db_path) as (frozen,_):
        build_account_fixture(source_db=frozen,output_path=out/'account-corpus.json.gz',task_ids=['new-search-task'])
        code=subprocess.check_output(['git','show','cc82d62d9595f2908ac5e5a13e5895d64cccec23:backend/reporting/integration_source.py'],text=True)
        module=types.ModuleType('legacy_cc82_integration_source');sys.modules[module.__name__]=module
        exec(compile(code,'cc82d62:backend/reporting/integration_source.py','exec'),module.__dict__)
        old=module.build_immutable_snapshot(module.CanonicalReportSource(frozen,root/'outputs'),'new-search-task')
        data=frozen.read_bytes()
        assert hashlib.sha256(data).hexdigest()==old.source_db_sha256
        (out/'source.sqlite3.gz').write_bytes(gzip.compress(data,mtime=0))
        (out/'snapshot.json').write_text(json.dumps(dataclasses.asdict(old),ensure_ascii=False,indent=2)+'\n')
        (out/'provenance.json').write_text(json.dumps({'synthetic':True,'builder_commit':'cc82d62d9595f2908ac5e5a13e5895d64cccec23','builder_path':'backend/reporting/integration_source.py','source_sha256':old.source_db_sha256,'snapshot_hash':old.snapshot_hash,'note':'Historical build_immutable_snapshot executed unmodified in isolated Python process against synthetic data. Source DB is standalone DELETE journal image; no production records.'},indent=2)+'\n')
