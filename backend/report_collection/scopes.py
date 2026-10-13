"""Explicit authorized-history sources, separate from conversation membership."""
import json
from .service import business_config, encoded, reference


def install(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS collection_history_sources (
      session TEXT, ref TEXT, payload TEXT NOT NULL, PRIMARY KEY(session,ref));
    CREATE TABLE IF NOT EXISTS collection_history_turns (
      session TEXT, turn TEXT, sources TEXT NOT NULL, PRIMARY KEY(session,turn));
    CREATE TABLE IF NOT EXISTS collection_history_barriers (
      session TEXT PRIMARY KEY, invalid_sources TEXT NOT NULL,
      blocked_through_turn TEXT NOT NULL);
    ''')


def history_sources(service, session, turn):
    # Verify running turn/owner without interpreting model-supplied identities.
    current = service.sources(session, turn)
    principal = service.conversation.principal_for_session(session)
    with service.connect() as db:
        frozen = db.execute('SELECT sources FROM collection_history_turns WHERE session=? AND turn=?', (session,turn)).fetchone()
    if frozen:
        sources = json.loads(frozen['sources'])
        for source in sources: service.authorize(source, principal)
        return sources
    with service.report_store._connect() as db:
        rows = db.execute('''SELECT v.id,v.report_id,v.status,v.content_hash,v.title,v.version_number,v.published_at,r.task_id
          FROM report_versions v JOIN reports r ON r.id=v.report_id
          WHERE v.status='published' ORDER BY v.version_number DESC,v.published_at DESC,v.id DESC''').fetchall()
    by_version = {s['version']:s for s in current}
    seen=set(); sources=[]
    for row in rows:
        item=dict(row)
        if item['task_id'] in seen or not service.can_read(principal,item): continue
        seen.add(item['task_id'])
        if item['id'] in by_version:
            source=dict(by_version[item['id']])
        else:
            snapshot=service.report_store.get_source_snapshot(item['id'])
            if not snapshot: raise ValueError('授权历史中有报告缺少冻结快照，未将部分结果当作全部。')
            config=snapshot['statistics'].get('source_configuration',{})
            run_store=service.conversation.tool_service.application_service.store
            with run_store._connect() as db:
                run=db.execute("SELECT confirmed_configuration_json FROM investigation_runs WHERE report_version_id=? AND owner_principal=? AND status='PUBLISHED'",(item['id'],principal.id)).fetchone()
            if run: config={**config,**json.loads(run[0])}
            source={'session':session,'ref':reference('history_report',[session,item['id']]),
                    'version':item['id'],'label':str(item['title'] or '历史报告'),
                    'identity':encoded({'content_hash':item['content_hash'],'snapshot_hash':snapshot['snapshot_hash'],'config':business_config(config)})}
        # Explicit domain prevents old results from silently expanding membership.
        source['domain']='authorized_history'
        sources.append(source)
    sources.sort(key=lambda s:(s['label'],s['version']))
    with service.connect() as db:
        db.execute('INSERT OR IGNORE INTO collection_history_turns VALUES(?,?,?)',(session,turn,encoded(sources)))
        for source in sources:
            db.execute('INSERT OR IGNORE INTO collection_history_sources VALUES(?,?,?)',(session,source['ref'],encoded(source)))
        sources=json.loads(db.execute('SELECT sources FROM collection_history_turns WHERE session=? AND turn=?',(session,turn)).fetchone()[0])
    for source in sources: service.authorize(source,principal)
    return sources


def validate_result_sources(service, session, data, current):
    principal=service.conversation.principal_for_session(session)
    allowed={s['ref']:s for s in current}
    for source in data['sources']:
        if source.get('domain')=='authorized_history':
            with service.connect() as db:
                row=db.execute('SELECT payload FROM collection_history_sources WHERE session=? AND ref=?',(session,source['ref'])).fetchone()
            if not row or json.loads(row[0])!=source: raise PermissionError('历史来源引用失效。')
        elif source['ref'] not in allowed or source!=allowed[source['ref']]:
            raise PermissionError('结果超出本轮报告范围。')
        service.authorize(source,principal)


def authorized_history_for_model(service, session, history):
    """Cut the model context once per newly revoked source, not on every turn.

    Transcripts contain paraphrases without per-sentence provenance. Until a clean
    turn completes, keep rejecting the old transcript (also across failed turns
    and restarts). Successful subsequent transcripts are built from this cleared
    view. Durable user messages and source authorization records stay untouched.
    """
    principal=service.conversation.principal_for_session(session)
    with service.connect() as db:
        rows=db.execute('SELECT payload FROM collection_history_sources WHERE session=?',(session,)).fetchall()
    invalid=[]
    for row in rows:
        try:service.authorize(json.loads(row[0]),principal)
        except (ValueError,PermissionError):invalid.append(row[0])
    store=service.conversation.store
    with store._connect() as db:
        last=db.execute('''SELECT h.turn_id FROM investigation_hermes_transcripts h
          JOIN investigation_turns t ON t.id=h.turn_id
          WHERE h.session_id=? AND t.status='completed'
          ORDER BY t.completed_at DESC,h.turn_id DESC LIMIT 1''',(session,)).fetchone()
    last_id=last[0] if last else ''
    with service.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        state=db.execute('SELECT * FROM collection_history_barriers WHERE session=?',(session,)).fetchone()
        previous=set(json.loads(state['invalid_sources'])) if state else set()
        blocked=state['blocked_through_turn'] if state else None
        if set(invalid)-previous:blocked=last_id
        if blocked is not None:
            db.execute('INSERT OR REPLACE INTO collection_history_barriers VALUES(?,?,?)',
                       (session,encoded(sorted(invalid)),blocked))
    return [] if blocked is not None and last_id==blocked else history
