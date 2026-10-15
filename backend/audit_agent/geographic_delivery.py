"""Durable geographic handoff: approved evidence and membership commit together."""
from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path

from .ingestion import content_identity, utc_now
from .scoped_selection import digest


def verify_geographic_evidence(conn, task_id, content_id, path, payload):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    geographic_job = False
    if 'jobs' in tables and 'geographic_plan' in {row[1] for row in conn.execute('PRAGMA table_info(jobs)')}:
        job = conn.execute('SELECT geographic_plan FROM jobs WHERE id=?', (task_id,)).fetchone()
        geographic_job = bool(job and job[0] and json.loads(job[0]))
    row = (conn.execute('SELECT raw_path, raw_digest FROM geographic_deliveries WHERE job_id=? AND content_id=?',
                        (task_id, content_id)).fetchone() if 'geographic_deliveries' in tables else None)
    if geographic_job and row is None:
        raise ValueError('geographic membership has no durable delivery evidence')
    if row and (str(Path(path).resolve()) != row['raw_path'] or digest(payload) != row['raw_digest']):
        raise ValueError('geographic delivery evidence mismatch')


class GeographicDelivery:
    def __init__(self, ingestion, state_store, plan, raw_root, lease_fence=None):
        self.ingestion, self.state_store = ingestion, state_store
        self.plan = deepcopy(plan)
        self.raw_root = Path(raw_root).resolve()
        self.lease_fence = lease_fence
        state_store._validate_plan(plan)
        with ingestion._lock, ingestion._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS geographic_deliveries (
                idempotency_key TEXT PRIMARY KEY, job_id TEXT NOT NULL, content_id INTEGER NOT NULL,
                execution_id TEXT NOT NULL, attempt_id TEXT NOT NULL, binding TEXT NOT NULL,
                raw_path TEXT NOT NULL, raw_digest TEXT NOT NULL, receipt TEXT NOT NULL,
                UNIQUE(job_id, content_id), UNIQUE(execution_id, attempt_id))''')

    def deliver(self, content, *, idempotency_key, binding):
        p = self.plan
        with self.ingestion._lock, ExitStack() as stack:
            db = stack.enter_context(self.ingestion._connect())
            db.execute('BEGIN IMMEDIATE')
            if self.state_store.path == Path(self.ingestion.db_path).resolve():
                state_db = db
            else:
                state_db = stack.enter_context(self.state_store._connection())
                state_db.execute('BEGIN IMMEDIATE')
            self.state_store._fence(state_db)
            if self.lease_fence is not None and self.lease_fence(db) is False:
                raise ValueError('worker lease fence expired')
            row = state_db.execute(f'SELECT snapshot FROM {self.state_store._table} WHERE execution_id=?',
                                   (p['execution_id'],)).fetchone()
            if not row:
                raise ValueError('unknown execution')
            snap = json.loads(row[0])
            self.state_store._validate_snapshot(snap, p['execution_id'])
            a = snap['attempts'].get(binding.get('attempt_id'))
            if not a or a['status'] != 'settled' or not snap['reviews'].get(a['attempt_id'], {}).get('allowed'):
                raise ValueError('settled approved attempt required')
            capture = snap['receipts'][a['receipt_id']]
            expected = {k:a[k] for k in ('execution_id','plan_digest','query_id','platform','content_key','occurrence_id','attempt_id')}
            expected['content_digest'] = capture['content_digest']
            expected_key = digest({k:expected[k] for k in ('execution_id','plan_digest','attempt_id','content_digest')})
            if binding != expected or idempotency_key != expected_key or content != capture['content'] or digest(content) != capture['content_digest']:
                raise ValueError('delivery evidence binding mismatch')
            saved = db.execute('SELECT * FROM geographic_deliveries WHERE idempotency_key=?', (idempotency_key,)).fetchone()
            if saved:
                if json.loads(saved['binding']) != expected | p['binding'] | p['runtime_binding']:
                    raise ValueError('delivery binding conflict')
                payload = json.loads(Path(saved['raw_path']).read_text(encoding='utf-8'))
                verify_geographic_evidence(db, saved['job_id'], saved['content_id'], saved['raw_path'], payload)
                membership = db.execute('SELECT raw_item_path FROM task_contents WHERE task_id=? AND content_id=?',
                                        (saved['job_id'], saved['content_id'])).fetchone()
                if not membership or membership['raw_item_path'] != saved['raw_path']:
                    raise ValueError('delivery membership evidence mismatch')
                return json.loads(saved['receipt'])
            raw = content.get('raw_source')
            if not isinstance(raw, dict) or not isinstance(raw.get('post'), dict) or not isinstance(raw.get('comments'), list):
                raise ValueError('complete original detail evidence required')
            platform, key = a['platform'], a['content_key']
            if content_identity(raw['post'], platform) != key:
                raise ValueError('original detail identity mismatch')
            job_id = p['runtime_binding']['job_id']
            query = next(q for q in p['queries'] if q['query_id'] == a['query_id'])
            occurrence = snap['reservations'][a['occurrence_id']]['occurrence']
            review = snap['reviews'][a['attempt_id']]
            basis = dict(source_job_id=job_id, content_key=key, occurrence_id=a['occurrence_id'],
                         query_id=a['query_id'], source_keyword=query['query'], rank=occurrence['rank'],
                         risk_band=occurrence['risk']['score']['band'], risk_score=occurrence['risk']['score']['score'],
                         geo_tier=review.get('after_tier', a['tier']), initial_geo_tier=a['tier'],
                         scope_digest=p['scope_digest'], policy_digest=p['policy_digest'],
                         plan_digest=p['plan_digest'], initial_evidence_digest=occurrence['evidence_digest'],
                         detail_evidence_digest=capture['content_digest'],
                         selection_policy=p['selection_policy'], target_level=p['target_level'])
            scope = p.get('scope_context', {}).get('scope', {})
            for field, region in (('target_name', 'target'), ('province_name', 'province')):
                name = scope.get(region, {}).get('name')
                if isinstance(name, str) and name:
                    basis[field] = name
            payload = dict(task_id=job_id, platform=platform, category='', keyword=query['query'],
                           item=deepcopy(raw['post']), comments=deepcopy(raw['comments']),
                           geographic_binding=expected | p['binding'] | p['runtime_binding'],
                           selection_basis=basis)
            raw_digest = digest(payload)
            self.raw_root.mkdir(parents=True, exist_ok=True)
            path = self.raw_root / (idempotency_key + '.json')
            try:
                with path.open('x', encoding='utf-8') as handle:
                    json.dump(payload, handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                directory = os.open(self.raw_root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except FileExistsError:
                if digest(json.loads(path.read_text(encoding='utf-8'))) != raw_digest:
                    raise ValueError('immutable delivery evidence conflict')
            now = utc_now()
            item = raw['post']
            db.execute('''INSERT OR IGNORE INTO contents
                (platform,content_key,note_id,url,title,raw_item_path,analyze_status,first_seen_at,last_seen_at)
                VALUES (?,?,?,?,?,?,'queued',?,?)''',
                (platform,key,key,str(item.get('aweme_url') or ''),str(item.get('title') or item.get('desc') or ''),str(path),now,now))
            cid = db.execute('SELECT id FROM contents WHERE platform=? AND content_key=?',(platform,key)).fetchone()['id']
            if db.execute('SELECT 1 FROM task_contents WHERE task_id=? AND content_id=?',(job_id,cid)).fetchone():
                raise ValueError('existing unbound task membership')
            db.execute('INSERT INTO content_matches(content_id,task_id,category,keyword,matched_at) VALUES (?,?,?,?,?)',
                       (cid,job_id,'',query['query'],now))
            db.execute('''INSERT INTO task_contents(task_id,content_id,analyze_status,raw_item_path,result_path,audit_result_id,created_at,updated_at)
                          VALUES (?,?,'queued',?,NULL,NULL,?,?)''',(job_id,cid,str(path),now,now))
            member = dict(db.execute('SELECT id AS task_content_id, content_id, analyze_status, result_path, audit_result_id FROM task_contents WHERE task_id=? AND content_id=?',(job_id,cid)).fetchone())
            member.update(platform=platform,content_key=key)
            refs = [self.ingestion._ref_from_payload(member,payload,path)]
            receipt = expected | {'idempotency_key':idempotency_key,'refs':refs,'selection_basis':basis}
            db.execute('INSERT INTO geographic_deliveries VALUES (?,?,?,?,?,?,?,?,?)',
                       (idempotency_key,job_id,cid,p['execution_id'],a['attempt_id'],
                        json.dumps(expected | p['binding'] | p['runtime_binding'],ensure_ascii=False),
                        str(path),raw_digest,json.dumps(receipt,ensure_ascii=False)))
            # ExitStack commits ingestion only after every check succeeds. The
            # state lease lock remains held until this durable handoff commits.
            self.state_store._fence(state_db)
            if self.lease_fence is not None and self.lease_fence(db) is False:
                raise ValueError('worker lease fence expired')
            db.commit()
            return receipt


def delivery_basis_from_connection(db, job_id):
    """Read-only report source; does not initialize or migrate any tables."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='geographic_deliveries'").fetchone():
        return []
    rows = db.execute('SELECT * FROM geographic_deliveries WHERE job_id=? ORDER BY content_id', (job_id,)).fetchall()
    result = []
    for row in rows:
        payload = json.loads(Path(row['raw_path']).read_text(encoding='utf-8'))
        verify_geographic_evidence(db, job_id, row['content_id'], row['raw_path'], payload)
        membership = db.execute('SELECT raw_item_path FROM task_contents WHERE task_id=? AND content_id=?',
                                (job_id,row['content_id'])).fetchone()
        if not membership or membership['raw_item_path'] != row['raw_path']:
            raise ValueError('delivery membership evidence mismatch')
        basis = json.loads(row['receipt'])['selection_basis']
        binding = json.loads(row['binding'])
        if (basis != payload.get('selection_basis') or basis['source_job_id'] != job_id or
                basis['content_key'] != binding['content_key'] or basis['plan_digest'] != binding['plan_digest'] or
                basis['detail_evidence_digest'] != binding['content_digest'] or
                payload.get('geographic_binding') != binding):
            raise ValueError('delivery selection basis binding mismatch')
        result.append(basis)
    return result


def delivery_basis_for_job(ingestion, job_id):
    """Return only frozen selection fields for the report's source Job."""
    with ingestion._lock, ingestion._connect() as db:
        return delivery_basis_from_connection(db, job_id)
