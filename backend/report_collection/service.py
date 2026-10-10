"""Persist membership, turn snapshots and immutable query results separately.

All report access goes through the injected ownership policy, including replays.
No model argument can supply a database path, owner or conversation identity.
"""
from collections import defaultdict
from contextlib import contextmanager
from hashlib import sha256
import json
import re
from pathlib import Path
import sqlite3
from uuid import uuid4

from backend.hermes_runtime.report_snapshot import published_report_snapshot
from hermes_m0.real_report_repository import PublishedReportRepository


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def reference(kind, value):
    return kind + '_' + sha256(encoded(value).encode()).hexdigest()[:24]


class ReportCollectionService:
    def __init__(self, *, conversation, report_store, can_read, path):
        self.conversation = conversation
        self.report_store = report_store
        self.can_read = can_read
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS collection_sources (
              session TEXT, ref TEXT PRIMARY KEY, version TEXT, label TEXT, identity TEXT NOT NULL,
              UNIQUE(session,version), UNIQUE(session,label));
            CREATE TABLE IF NOT EXISTS collection_turns (
              turn TEXT PRIMARY KEY, session TEXT, sources TEXT NOT NULL, selected TEXT, focus TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS collection_results (
              ref TEXT PRIMARY KEY, turn TEXT, session TEXT, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS collection_calls (
              id INTEGER PRIMARY KEY, session TEXT, turn TEXT, name TEXT, arguments TEXT, response TEXT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def active(self, session):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM collection_sources WHERE session=?', (session,)).fetchone() is not None

    def authorize(self, source, principal):
        # Owner policy is evaluated on the live catalog, before loading snapshots.
        version = self.report_store.get_version(source['version'])
        if not version: raise PermissionError('报告不可访问。')
        report = self.report_store.get_report(version['report_id'])
        item = {**version, 'task_id': report['task_id']}
        if version['status'] != 'published' or not self.can_read(principal, item):
            raise PermissionError('报告不可访问。')
        if source.get('identity'):
            identity=json.loads(source['identity'])
            snapshot=self.report_store.get_source_snapshot(source['version'])
            if (version['content_hash']!=identity['content_hash'] or not snapshot
                    or snapshot['snapshot_hash']!=identity['snapshot_hash']):
                raise PermissionError('冻结报告身份已变化。')
        return item

    def register(self, session, principal, versions):
        self.conversation.get_session(session, principal=principal)
        if not self.active(session):
            _, _, turns = self.conversation.store.conversation_snapshot(session, include_tool_messages=False)
            if turns:
                raise ValueError('第一阶段请在新建的空会话中注册报告，避免改变已有创建会话的执行模式。')
        if not versions or len(versions) > 20:
            raise ValueError('每个会话支持 1–20 份报告。')
        prepared = []
        for v in versions:
            s = {'session': session, 'ref': 'report_' + uuid4().hex, 'version': v['report_version_id'], 'label': v['label']}
            version = self.authorize(s, principal)
            snapshot = self.report_store.get_source_snapshot(s['version'])
            if not snapshot: raise ValueError('报告缺少冻结快照。')
            config = snapshot['statistics'].get('source_configuration', {})
            run_store = self.conversation.tool_service.application_service.store
            with run_store._connect() as db:
                row = db.execute('''SELECT confirmed_configuration_json FROM investigation_runs
                    WHERE report_version_id=? AND owner_principal=? AND status='PUBLISHED' ''',
                    (s['version'], principal.id)).fetchone()
            # Actual execution snapshot wins over canonical source's summary.
            if row: config = {**config, **json.loads(row[0])}
            s['identity'] = encoded({'content_hash':version['content_hash'],
                'snapshot_hash':snapshot['snapshot_hash'], 'config':business_config(config)})
            prepared.append(s)
        with self.connect() as db:
            # Registration is atomic and idempotent; existing labels cannot move.
            db.execute('BEGIN IMMEDIATE')
            for s in prepared:
                old = db.execute('SELECT * FROM collection_sources WHERE session=? AND version=?', (session,s['version'])).fetchone()
                if old:
                    if old['label'] != s['label']: raise ValueError('报告名称已固定。')
                    continue
                db.execute('INSERT INTO collection_sources VALUES(:session,:ref,:version,:label,:identity)', s)
            if db.execute('SELECT count(*) FROM collection_sources WHERE session=?',(session,)).fetchone()[0] > 20:
                raise ValueError('最多支持 20 份报告。')
        return self.members(session, principal)

    def members(self, session, principal):
        self.conversation.get_session(session, principal=principal)
        with self.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM collection_sources WHERE session=? ORDER BY rowid', (session,))]
        for row in rows:
            self.authorize(row, principal)
        return rows

    def freeze(self, session, turn, principal, sources=None):
        sources = self.members(session, principal) if sources is None else sources
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO collection_turns(turn,session,sources) VALUES(?,?,?)', (turn,session,encoded(sources)))

    def sources(self, session, turn):
        t = self.conversation.store.get_turn(turn)
        principal = self.conversation.principal_for_session(session)
        self.conversation.get_session(session, principal=principal)
        if t.session_id != session or t.status != 'running':
            raise PermissionError('查询不属于正在运行的会话轮次。')
        with self.connect() as db:
            row = db.execute('SELECT sources FROM collection_turns WHERE session=? AND turn=?',(session,turn)).fetchone()
        if not row: raise PermissionError('本轮报告范围尚未冻结。')
        sources = json.loads(row['sources'])
        for s in sources: self.authorize(s, principal)
        return sources

    def validate_history(self, session):
        # Fail closed before inference if any previously disclosed report is revoked.
        principal = self.conversation.principal_for_session(session)
        with self.connect() as db:
            rows = db.execute('SELECT sources FROM collection_turns WHERE session=?',(session,)).fetchall()
        for row in rows:
            for source in json.loads(row['sources']): self.authorize(source, principal)

    def result(self, ref, session, sources):
        with self.connect() as db:
            row = db.execute('SELECT payload,turn FROM collection_results WHERE ref=? AND session=?',(ref,session)).fetchone()
        if not row: raise PermissionError('结果引用不属于当前会话。')
        if self.conversation.store.get_turn(row['turn']).status not in {'running','completed'}:
            raise PermissionError('结果来自未完成的轮次。')
        data = json.loads(row['payload'])
        allowed = {s['ref']:s for s in sources}
        if any(s['ref'] not in allowed or s != allowed[s['ref']] for s in data['sources']):
            raise PermissionError('结果超出本轮报告范围。')
        return data

    def previous(self, session, sources):
        with self.connect() as db:
            rows = db.execute("SELECT focus,turn FROM collection_turns WHERE session=? AND focus<>'' ORDER BY rowid DESC",(session,)).fetchall()
        for row in rows:
            if self.conversation.store.get_turn(row['turn']).status == 'completed':
                return self.result(row['focus'],session,sources)
        return None

    def focus(self, session, turn, ref):
        with self.connect() as db:
            db.execute('UPDATE collection_turns SET focus=? WHERE session=? AND turn=?',(ref,session,turn))

    def select(self, session, turn, args):
        sources = self.sources(session,turn)
        if args.get('report_refs'):
            by_ref = {s['ref']:s for s in sources}
            if any(r not in by_ref for r in args['report_refs']): raise PermissionError('报告超出本轮会话范围。')
            selected = [by_ref[r] for r in dict.fromkeys(args['report_refs'])]
        elif args.get('scope') == 'all':
            selected = sources
        else:
            previous = self.previous(session,sources)
            selected = previous['sources'] if previous else sources
        if not selected: raise ValueError('会话中没有可查询报告。')
        self.pin_selection(session, turn, selected)
        return selected

    def pin_selection(self, session, turn, selected):
        # Once a tool has read data, that turn cannot broaden its query range.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT selected FROM collection_turns WHERE session=? AND turn=?', (session,turn)).fetchone()
            if row['selected'] is not None:
                allowed = {s['ref'] for s in json.loads(row['selected'])}
                if not {s['ref'] for s in selected} <= allowed:
                    raise PermissionError('本轮已限定查询范围，不能扩大；请在下一轮明确查询更多报告。')
            db.execute('UPDATE collection_turns SET selected=? WHERE session=? AND turn=?', (encoded(selected),session,turn))

    def repository(self, source):
        principal = self.conversation.principal_for_session(source['session'])
        version = self.authorize(source, principal)
        snapshot = self.report_store.get_source_snapshot(source['version'])
        pinned = json.loads(source['identity'])
        if version['content_hash'] != pinned['content_hash'] or snapshot['snapshot_hash'] != pinned['snapshot_hash']:
            raise PermissionError('冻结报告身份已变化。')
        identities = (source['version'],pinned['content_hash'],pinned['snapshot_hash'])
        path,digest = published_report_snapshot(self.report_store.db_path, ledger_path=self.path,
            session_id=source['session'],identities=identities)
        repo = PublishedReportRepository.load(path, report_version_id=source['version'],
            expected_database_sha256=digest,expected_content_hash=version['content_hash'],expected_snapshot_hash=snapshot['snapshot_hash'])
        return repo, pinned['config']

    def observations(self, sources):
        rows = []
        for s in sources:
            repo,_ = self.repository(s)
            for post in repo.ordered_posts():
                payload=repo.snapshot_payloads[post.id]
                platform='douyin' if payload.get('platform') in {'dy','douyin'} else payload.get('platform','')
                author=(payload.get('raw_content_payload') or {}).get('author') or {}
                stable=str(author.get('sec_uid') or '').strip() if platform=='douyin' else ''
                finding=repo.finding_for_post(post.id)
                post_ref=reference('post',[s['version'],post.id])
                source={'report_ref':s['ref'],'label':s['label'],'report_version_id':s['version']}
                rows.append({'kind':'post_author','comment_ref':'','post_ref':post_ref,
                    'evidence_key':reference('evidence',[platform,post.id]),
                    'account_ref':reference('account',[platform,stable]) if stable else '',
                    'account_name':str(author.get('nickname') or ''),
                    'text':payload.get('display_title') or payload.get('title') or '',
                    'risk_level':finding.risk_level,'audit_status':'completed',
                    'post_id':post.id,'source':source})
                for c in repo.comments_for_federation(post.id):
                    stable = c.author_source_key if c.identity_consistent and c.platform in {'dy','douyin'} else ''
                    rows.append({'kind':'comment_author','post_ref':post_ref,
                        'comment_ref':reference('comment',[s['version'],post.id,c.id]),
                        'evidence_key':reference('evidence',[c.platform,post.id,c.id]),
                        'account_ref':reference('account',[platform,stable]) if stable else '',
                        'account_name':c.author_display_name, 'text':c.text, 'risk_level':c.risk_level,
                        'audit_status':c.audit_status, 'post_id':post.id,
                        'source':{'report_ref':s['ref'],'label':s['label'],'report_version_id':s['version']}})
            if len(rows)>100000: raise ValueError('报告内容超过第一阶段查询上限，未返回部分结果冒充全量。')
        return rows

    def save(self, session,turn,sources,kind,items,limit=20,offset=0,**extra):
        ref='result_'+uuid4().hex
        data={'result_ref':ref,'sources':sources,'kind':kind,'items':items,**extra}
        with self.connect() as db:
            db.execute('INSERT INTO collection_results VALUES(?,?,?,?)',(ref,turn,session,encoded(data)))
        self.focus(session,turn,ref)
        return self.page(data,limit,offset)

    @staticmethod
    def page(data,limit,offset):
        items=data['items']; end=min(len(items),offset+limit)
        return {**data, 'sources':[public_source(s) for s in data['sources']], 'items':items[offset:end],'total_count':len(items),'has_more':end<len(items),'next_offset':end,
            'scope_note':'仅本次列出的冻结报告；数量为本结果全集，分页不改变结果。'}

    def validate_answer(self, session, turn, answer):
        """Validate opaque evidence citations, not the semantics of prose claims."""
        with self.connect() as db:
            pinned=db.execute('SELECT selected FROM collection_turns WHERE session=? AND turn=?',(session,turn)).fetchone()
            calls=db.execute('SELECT name,response FROM collection_calls WHERE session=? AND turn=?',(session,turn)).fetchall()
        if not pinned or not pinned['selected']:
            return  # A refusal or request for clarification can have no data read.
        selected=json.loads(pinned['selected'])
        allowed_reports={s['ref'] for s in selected}
        versions={s['version'] for s in selected}
        visible=[]
        for call in calls:
            response=json.loads(call['response'])
            if not response.get('ok') or call['name']=='list_conversation_reports': continue
            data=response.get('data',{})
            if {s['report_ref'] for s in data.get('sources',[])} <= allowed_reports:
                visible.append(data)
        pattern=r'(?:comment|account|report|post)_[0-9a-f]{24,32}'
        allowed=set(re.findall(pattern,encoded(visible))) | allowed_reports
        used=set(re.findall(pattern,answer))
        used_versions=set(re.findall(r'report-version:[0-9a-f]{32}',answer))
        if used-allowed or used_versions-versions:
            raise ValueError('答复引用了本轮查询范围之外或未读取的证据。请仅依据本轮工具结果重答，不补充其他报告的历史评论或风险对照。')

    def record_call(self, name, args, session, turn, response):
        with self.connect() as db:
            db.execute('INSERT INTO collection_calls(session,turn,name,arguments,response) VALUES(?,?,?,?,?)',
                (session,turn,name,encoded(args),encoded(response)))

    def execute(self,name,args,session,turn):
        sources=self.sources(session,turn)
        if name=='list_conversation_reports':
            previous=self.previous(session,sources)
            return {'reports':[public_source(s) for s in sources],'previous_result_ref':previous['result_ref'] if previous else '',
                'inherited_report_refs':[s['ref'] for s in previous['sources']] if previous else []}
        if name=='read_conversation_result':
            data = self.result(args['result_ref'],session,sources)
            self.pin_selection(session,turn,data['sources'])
            self.focus(session,turn,data['result_ref'])
            return self.page(data,args['limit'],args['offset'])
        selected=self.select(session,turn,args)
        if name=='compare_conversation_config':
            if len(selected)!=2: raise ValueError('请选择两份报告进行配置比较。')
            configs=[self.repository(s)[1] for s in selected]
            changes=[{'field':k,'before':configs[0][k],'after':configs[1][k],
                'known': configs[0][k] is not None and configs[1][k] is not None,
                **list_difference(configs[0][k],configs[1][k])}
                for k in configs[0] if configs[0][k]!=configs[1][k] or configs[0][k] is None]
            return self.save(session,turn,selected,'config_diff',changes,configs=configs,
                warning='缺失字段为未知，不能视为未使用；配置差异不能单独证明风险变化的因果关系。')
        rows=self.observations(selected)
        if args['kind']=='comments': rows=[r for r in rows if r['kind']=='comment_author']
        parent_ref = args.get('from_result_ref')
        if not parent_ref and args.get('scope') == 'inherit' and not args.get('report_refs'):
            previous = self.previous(session,sources)
            if previous and previous['kind'] in {'accounts','comments'}:
                parent_ref = previous['result_ref']
        if parent_ref:
            previous=self.result(parent_ref,session,sources)
            # A followup can narrow a previous result, never add unseen accounts/comments.
            if previous['kind']=='comments':
                refs={r['comment_ref'] for r in previous['items']}; rows=[r for r in rows if r['comment_ref'] in refs]
            elif previous['kind']=='accounts':
                refs={r['account_ref'] for r in previous['items']}; rows=[r for r in rows if r['account_ref'] in refs]
            else: raise ValueError('此结果不包含账号或评论。')
        if args.get('account_ref'): rows=[r for r in rows if r['account_ref']==args['account_ref']]
        if args.get('risk_level'): rows=[r for r in rows if r['audit_status']=='completed' and r['risk_level']==args['risk_level']]
        if args['kind']=='comments':
            items=rows
        else:
            accounts=defaultdict(list)
            for row in rows:
                if row['account_ref']: accounts[row['account_ref']].append(row)
            items=[]
            for ref,observations in sorted(accounts.items()):
                refs={r['source']['report_ref'] for r in observations}
                if args['common_only'] and len(refs)!=len(selected): continue
                items.append({'account_ref':ref,'names':sorted({r['account_name'] for r in observations}),
                    'unique_comment_count':len({r['evidence_key'] for r in observations if r['kind']=='comment_author'}),
                    'unique_post_count':len({r['evidence_key'] for r in observations if r['kind']=='post_author'}),
                    'roles':sorted({r['kind'] for r in observations}),
                    'observation_count':len(observations),
                    'observations':observations[:20], 'observations_truncated':len(observations)>20,
                    'report_statistics':[{'report_ref':s['ref'],'label':s['label'],
                        'comment_count':sum(r['source']['report_ref']==s['ref'] and r['kind']=='comment_author' for r in observations),
                        'post_count':sum(r['source']['report_ref']==s['ref'] and r['kind']=='post_author' for r in observations),
                        'risk_levels':sorted({r['risk_level'] for r in observations if r['source']['report_ref']==s['ref']})} for s in selected]})
        return self.save(session,turn,selected,args['kind'],items,args['limit'],
            parent_result_ref=parent_ref or '',
            unique_evidence_count=len({r['evidence_key'] for r in rows}),
            unresolved_identity_comments=sum(not r['account_ref'] and r['kind']=='comment_author' for r in rows),
            identity_scope='按抖音 sec_uid 关联冻结报告中的发帖者和评论者；其他平台或缺失稳定 ID 不合并。')


def business_config(config):
    execution=config.get('execution') or {}
    audit=config.get('audit_config') or {}
    rule=execution.get('rule_snapshot') or audit.get('rule_snapshot') or config.get('rule_snapshot') or {}
    # Only explicit business fields, never provider/account settings or prompts.
    return {'keywords':config.get('resolved_search_terms'),
        'ruleset_revision':config.get('ruleset_revision') or rule.get('ruleset_ref'),
        'rules':rule.get('decision_rules',rule.get('rules')),
        'exemptions':rule.get('general_exemptions',rule.get('exemptions')),
        'thresholds':rule.get('thresholds')}


def public_source(s):
    return {'report_ref':s['ref'],'label':s['label'],'report_version_id':s['version']}


def list_difference(before, after):
    if not isinstance(before,list) or not isinstance(after,list): return {}
    left={encoded(x):x for x in before}; right={encoded(x):x for x in after}
    return {'added':[right[k] for k in sorted(right.keys()-left.keys())],
            'removed':[left[k] for k in sorted(left.keys()-right.keys())]}
