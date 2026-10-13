"""Durable delivery cursors advance only with the persisted assistant answer."""
import json
from uuid import uuid4
from .service import encoded


def install(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS collection_deliveries (
        ref TEXT PRIMARY KEY,session TEXT NOT NULL,turn TEXT NOT NULL,
        signature TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'prepared',
        UNIQUE(session,turn,signature));''')


def acknowledge(service,session,turn):
    if service.conversation.store.get_turn(turn).status!='completed':return
    with service.connect() as db:
        row=db.execute("SELECT response FROM collection_calls WHERE session=? AND turn=? AND name='direct_delivery_completed' ORDER BY id DESC",(session,turn)).fetchone()
        if not row:return
        data=json.loads(row[0]).get('data',{})
        refs=data.get('delivery_refs') or [data.get('delivery_ref')]
        for ref in refs:
            if ref:db.execute("UPDATE collection_deliveries SET state='acknowledged' WHERE ref=? AND session=? AND turn=?",(ref,session,turn))


def reconcile(service,session):
    with service.connect() as db:
        turns=[r[0] for r in db.execute("SELECT DISTINCT turn FROM collection_deliveries WHERE session=? AND state='prepared'",(session,))]
    for turn in turns:acknowledge(service,session,turn)


def load(service,session,ref=''):
    reconcile(service,session)
    with service.connect() as db:
        if ref:row=db.execute('SELECT * FROM collection_deliveries WHERE session=? AND ref=?',(session,ref)).fetchone()
        else:row=db.execute("SELECT * FROM collection_deliveries WHERE session=? AND state='acknowledged' ORDER BY rowid DESC LIMIT 1",(session,)).fetchone()
    if not row:raise ValueError('没有可恢复的已交付列表；请先查询并交付，或提供已有交付引用。')
    return json.loads(row['payload']),row['state']


def prepare(service,args,session,turn,sources):
    from .delivery import render_delivery
    from .tools import Delivery
    args=Delivery.model_validate(args).model_dump()
    if args['batch_size'] is None:
        before=(load(service,session,args['delivery_ref'])[0]
                if args['mode'] in {'continue','replay'} else None)
        args['batch_size']=(before['arguments'].get('batch_size') if before else None) or 100
    signature=encoded(args)
    with service.connect() as db:
        prior=db.execute('SELECT payload FROM collection_deliveries WHERE session=? AND turn=? AND signature=?',(session,turn,signature)).fetchone()
    if prior:
        batch=json.loads(prior[0])
    else:
        mode=args.get('mode','first');refs=args.get('result_refs',[]);before=None
        if mode in {'continue','replay'}:
            before,state=load(service,session,args.get('delivery_ref',''))
            if refs and refs!=before['arguments']['result_refs']:
                raise ValueError('续页或重放不能更换结果集合，请对新查询使用首次交付。')
            if mode=='continue' and state!='acknowledged':raise ValueError('上批尚未完成交付，先重放该批，不能跳过。')
            refs=before['arguments']['result_refs']
        elif args.get('delivery_ref'):raise ValueError('首次交付不接受已有交付引用。')
        # Replaying an older batch must not rewind the continuation cursor.
        if mode=='continue':
            series=before.get('series_ref',before['delivery_ref'])
            with service.connect() as db:
                rows=db.execute("SELECT payload FROM collection_deliveries WHERE session=? AND state='acknowledged' ORDER BY rowid",(session,)).fetchall()
            for row in rows:
                candidate=json.loads(row[0])
                if candidate.get('series_ref',candidate['delivery_ref'])==series and candidate['arguments'].get('mode','first')!='replay':
                    before=candidate
        size=args.get('batch_size') or (before['arguments'].get('batch_size') if before else None) or 100
        params={**args,'result_refs':refs,'batch_size':size}
        budget=size;ranges=[]
        for index,ref in enumerate(refs):
            data=service.result(ref,session,sources)
            if mode=='replay':
                page=before['ranges'][index]
            else:
                start=before['ranges'][index]['end'] if before else 0
                end=min(len(data['items']),start+budget)
                page={'result_ref':ref,'start':start,'end':end,'total':len(data['items'])}
                budget-=end-start
            ranges.append(page)
        if mode=='continue' and not any(p['end']>p['start'] for p in ranges):
            raise ValueError('该结果已全部交付，无更多记录。需要重看请重放已有批次。')
        ref='delivery_'+uuid4().hex
        batch={'delivery_ref':ref,'series_ref':before.get('series_ref',before['delivery_ref']) if before else ref,'arguments':params,'ranges':ranges}
        # Render/validate before saving; failed preparations never consume a cursor.
        render_delivery(service,params,session,turn,sources,pages=ranges)
        with service.connect() as db:
            db.execute('INSERT OR IGNORE INTO collection_deliveries(ref,session,turn,signature,payload) VALUES(?,?,?,?,?)',
                (batch['delivery_ref'],session,turn,signature,encoded(batch)))
            batch=json.loads(db.execute('SELECT payload FROM collection_deliveries WHERE session=? AND turn=? AND signature=?',(session,turn,signature)).fetchone()[0])
    return render_batch(service,batch,session,turn,sources)


def prepared_delivery(service,session,turn,ref,sources):
    with service.connect() as db:
        row=db.execute('SELECT payload FROM collection_deliveries WHERE session=? AND turn=? AND ref=?',
                       (session,turn,ref)).fetchone()
    if not row:raise ValueError('本轮交付批次不存在，未推进游标。')
    return render_batch(service,json.loads(row[0]),session,turn,sources)


def render_batch(service,batch,session,turn,sources):
    from .delivery import render_delivery
    # Reauthorize the frozen results without preparing a second delivery identity.
    result=render_delivery(service,batch['arguments'],session,turn,sources,pages=batch['ranges'])
    result.update(delivery_ref=batch['delivery_ref'],ranges=batch['ranges'],
        complete=all(p['end']==p['total'] for p in batch['ranges']))
    if not result['complete']:result['text']+='\n\n本批已列出上述范围，列表尚未结束；回复“继续”可按原结果顺序续页。'
    explanation=batch['arguments'].get('explanation','').strip()
    if explanation:result['text']=explanation+'\n\n'+result['text']
    return result


def read(service,args,session,turn,sources):
    batch,state=load(service,session,args.get('delivery_ref',''))
    items=[]
    for page in batch['ranges']:
        data=service.result(page['result_ref'],session,sources)
        service.pin_selection(session,turn,data['sources'])
        if args.get('position'):
            if page['start']<args['position']<=page['end']:
                items.append({'result_ref':page['result_ref'],'position':args['position'],
                              'item':data['items'][args['position']-1]})
    if args.get('position') and not items:raise ValueError('该序号不在指定批次已交付范围内，不能猜测。')
    return {**{k:batch[k] for k in ('delivery_ref','ranges')},'state':state,'items':items,
        'note':'序号按各结果的完整固定顺序计算；多个子列表中同号可能对应不同记录。'}
