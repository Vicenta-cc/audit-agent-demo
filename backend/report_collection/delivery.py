"""Deterministic, opt-in delivery of authorized query results, not prose validation."""
import html
import json
import re
from contextlib import contextmanager
from functools import wraps
from types import SimpleNamespace
from urllib.parse import urlsplit

RISK={'none':'无风险','low':'低风险','medium':'中风险','high':'高风险'}
ROLE={'any':'发帖者与评论者','comment_author':'评论作者','post_author':'发帖者','both':'同时发帖和评论的账号'}


def cell(value):
    # Evidence is literal data, including Markdown/HTML and embedded instructions.
    value=html.escape(str(value), quote=True).replace('\n',' ').replace('\r',' ')
    return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])',r'\\\1',value)


def link(label, url):
    """Preserve existing destinations, never turn arbitrary evidence into code."""
    if not isinstance(url,str) or not url or any(ord(c)<32 for c in url):return ''
    try:parts=urlsplit(url)
    except ValueError:return ''
    if not ((parts.scheme in {'http','https'} and parts.netloc) or
            (not parts.scheme and not parts.netloc and url.startswith(('/tasks/','/investigation/')))):
        return ''
    target=url.replace(' ','%20').replace('<','%3C').replace('>','%3E').replace('|','%7C')
    return '['+cell(label)+'](<'+target+'>)'


def source_links(row):
    values=row.get('links',{})
    pairs=values.items() if isinstance(values,dict) else ((v['label'],v['url']) for v in values)
    return ' · '.join(filter(None,(link(label,url) for label,url in pairs)))


def prepare_delivery(service,args,session,turn,sources):
    from .batches import prepare
    return prepare(service,args,session,turn,sources)


def render_delivery(service,args,session,turn,sources,*,pages=None):
    refs=args.get('result_refs',[])
    labels=args.get('report_labels',[])
    if not refs and not labels: raise ValueError('请选择查询结果或需要检查的报告名称。')
    if len(set(refs))!=len(refs): raise ValueError('不能重复交付同一结果。')
    blocks=[];counts=[];references=[];displayed_labels=set()
    for index,ref in enumerate(refs):
        data=service.result(ref,session,sources)
        service.pin_selection(session,turn,data['sources'])
        if data['kind'] not in {'comments','accounts'}: raise ValueError('此结果不支持列表直出。')
        if 'query_filters' not in data: raise ValueError('旧结果缺少查询条件，请重新查询后交付。')
        all_rows=data['items']
        start=pages[index]['start'] if pages else 0
        end=pages[index]['end'] if pages else len(all_rows)
        rows=all_rows[start:end]
        complete=start==0 and end==len(all_rows)
        progress=('按读取顺序列出全部记录。' if complete else f'本批显示第 {start+1}–{end} 条，共 {len(all_rows)} 条记录。') if rows else ('无符合条件的记录。' if not all_rows else f'此列表本批未新增记录；已交付 {start}/{len(all_rows)} 条。')
        filters=data['query_filters']
        scope=['报告：'+'、'.join(cell(s['label']) for s in data['sources']),
            '风险：'+RISK.get(filters.get('risk_level'),'不限')]
        if data['kind']=='accounts' and filters.get('account_role','any')!='any':
            scope.append('账号范围：'+ROLE.get(filters['account_role'],'未知'))
        if filters.get('name_contains'):scope.append('昵称包含“'+cell(filters['name_contains'])+'”')
        if filters.get('sort_by') not in {None,'source_order'}:
            scope.append('排序：'+{'comment_count':'去重评论数','post_count':'去重帖子数','observation_count':'收录数'}.get(filters['sort_by'],'未知')+'降序')
        if filters.get('top_n'):scope.append('前'+str(filters['top_n'])+'名及并列')
        if filters.get('account_ref'):scope.append('仅统计指定账号')
        if filters.get('text_exact') is not None:scope.append('评论原文为“'+cell(filters['text_exact'])+'”')
        if filters.get('parent_result_ref'):scope.append('仅统计此前选定的内容')
        if filters.get('common_only'):scope.append('仅统计所有所选报告都出现的账号')
        # Display aliases disambiguate identical names without disclosing internal
        # IDs. Full identities and immutable sources remain in structured metadata.
        identities={}
        for row in all_rows:
            name=(row.get('account_name') or '未署名') if data['kind']=='comments' else ('、'.join(row['names']) or '未署名')
            if row.get('account_ref'):identities.setdefault(name,set()).add(row['account_ref'])
        def author(row):
            name=(row.get('account_name') or '未署名') if data['kind']=='comments' else ('、'.join(row['names']) or '未署名')
            identity=row.get('account_ref')
            if not identity:return cell(name)+'（身份待确认）'
            ids=sorted(identities[name])
            suffix=f'（本表账号{ids.index(identity)+1}）' if len(ids)>1 else ''
            return cell(name)+suffix
        lines=[]
        if data['kind']=='comments':
            unique=len({r['evidence_key'] for r in all_rows})
            completed=sum(r['audit_status']=='completed' for r in all_rows)
            lines=[f'**评论明细 · {len(all_rows)} 条记录**', '；'.join(scope)+'。',
                f'{unique} 条原始评论，已完成 {completed} 次研判；'+progress,
                '| 序号 | 评论原文 | 作者 | 风险判断 | 来源 |',
                '| --- | --- | --- | --- | --- |']
            for i,row in enumerate(rows,start+1):
                risk=RISK.get(row['risk_level'],'待确认') if row['audit_status']=='completed' else '未完成研判'
                source=' · '.join(filter(None,(cell(row['source']['label']),source_links(row))))
                lines.append(f"| {i} | {cell(row['text'])} | {author(row)} | {risk} | {source} |")
        else:
            multiple=len(data['sources'])>1
            lines=[f'**符合条件的账号 · {len(all_rows)} 个**','；'.join(scope)+'。',progress,
                ('| 序号 | 账号 | 去重评论数 | 去重帖子数 | 各报告明细 | 来源 |' if multiple else
                 '| 序号 | 账号 | 评论数 | 帖子数 | 风险判断 | 来源 |'),
                '| --- | --- | ---: | ---: | --- | --- |']
            for i,row in enumerate(rows,start+1):
                reports='；'.join(
                    (f"{s['label']}：评论 {s['comment_count']} 条，帖子 {s['post_count']} 篇；" if multiple else '')+
                    ('、'.join(RISK.get(r,'待确认') for r in s['risk_levels']) or '暂无判断')
                    for s in row['report_statistics'])
                lines.append(f"| {i} | {author(row)} | {row['unique_comment_count']} | {row['unique_post_count']} | {cell(reports)} | {source_links(row) or '—'} |")
            unknown=data.get('unresolved_identity_comments',0)
            unknown_posts=data.get('unresolved_identity_posts',0)
            if unknown_posts:lines.append(f'另有 {unknown_posts} 条发帖记录缺少可靠作者身份，未计入账号。')
            if unknown:lines.append(f'另有 {unknown} 条评论的作者身份待确认，未计入上述账号，不能据此排除关联。')
        if any(len(ids)>1 for ids in identities.values()):
            lines.append('同名账号已分别标记；“本表账号”编号仅用于区分本张表中的不同账号。')
        references.append({'result_ref':ref,'sources':data['sources'],'query_filters':filters,
            'rows':[{'position':i,'account_ref':row.get('account_ref'),
                'comment_ref':row.get('comment_ref'),'source':row.get('source'),
                'links':row.get('links',{})} for i,row in enumerate(rows,start+1)]})
        displayed_labels.update(s['label'] for s in data['sources'])
        counts.append(len(rows));blocks.append('\n\n'.join(filter(None,lines[:3]))+'\n\n'+'\n'.join(lines[3:]))
    if labels:
        by_label={s['label']:s for s in sources}
        lines=['**报告访问情况**','']
        for label in labels:
            # The displayed list already establishes access to this report.
            # Keep the permission check and receipt, omit a duplicate prose block.
            if label in by_label and label in displayed_labels:continue
            if label in by_label:
                lines.append(f'- **{cell(label)}**：可以查看；本次仅确认访问权限，未查询内容。')
            else:
                lines.append(f'- **{cell(label)}**：不在当前会话可访问范围，未读取、未参与统计。')
        if len(lines)>2:
            lines.append('\n同名不一定是同一账号，不能只按昵称合并。')
            blocks.append('\n'.join(lines))
    text='\n\n---\n\n'.join(blocks)
    if len(text)>200000: raise ValueError('结果超过直出原型的文本容量，未交付截断结果。')
    return {'delivery':'direct','text':text,'result_refs':refs,'row_counts':counts,
            'report_labels_checked':labels,'complete':True,'references':references}


def pending_delivery(service,session,turn):
    from .batches import prepared_delivery
    with service.connect() as db:
        rows=db.execute("SELECT response FROM collection_calls WHERE session=? AND turn=? AND name='deliver_conversation_results' ORDER BY id",(session,turn)).fetchall()
    deliveries=[];seen=set();sources=service.sources(session,turn)
    for row in rows:
        response=json.loads(row['response'])
        if not response.get('ok'):continue
        ref=response['data']['delivery_ref']
        if ref in seen:continue
        seen.add(ref)
        deliveries.append(prepared_delivery(service,session,turn,ref,sources))
    if not deliveries:return None
    if len(deliveries)==1:return deliveries[0]
    return {'delivery':'direct','delivery_refs':[d['delivery_ref'] for d in deliveries],
            'text':'\n\n---\n\n'.join(d['text'] for d in deliveries),
            'complete':all(d['complete'] for d in deliveries),
            **{key:[value for d in deliveries for value in d[key]]
               for key in ('ranges','result_refs','row_counts','report_labels_checked','references')}}


@contextmanager
def direct_delivery_boundary(agent,service,session,turn):
    """Pinned Hermes 0.20.4 adapter: local terminal response after Agent opt-in.

    Does not scan/repair model prose, interrupt a turn or change dependency code.
    Native Hermes finishes and persists the normal tool/assistant transcript.
    Local completions are explicitly recorded and excluded from API call totals.
    """
    state={'local_completions':0}; originals={}
    if service.direct_delivery and service.agent_driven:
        if getattr(agent,'api_mode',None)!='chat_completions':
            raise ValueError('直出原型仅支持已验证的 chat_completions 适配器。')
        if service.direct_delivery_guidance:
            # Expose this already-registered tool directly; keep all other tools
            # and the normal deferred bridge. No forced tool_choice or routing.
            from .tools import schemas
            schema=next(s for s in schemas() if s['name']=='deliver_conversation_results')
            originals['tools']=agent.tools
            originals['valid_tool_names']=agent.valid_tool_names
            agent.tools=[t for t in agent.tools if t['function']['name']!=schema['name']]+[{'type':'function','function':schema}]
            agent.valid_tool_names=set(agent.valid_tool_names)|{schema['name']}
        def wrap(original):
            @wraps(original)
            def call(*args,**kwargs):
                if getattr(agent,'_interrupt_requested',False):return original(*args,**kwargs)
                delivery=pending_delivery(service,session,turn)
                if not delivery:return original(*args,**kwargs)
                state['local_completions']+=1
                service.record_call('direct_delivery_completed',{},session,turn,{'ok':True,'data':delivery})
                return SimpleNamespace(id='local-direct-delivery',model='local-renderer',object='chat.completion',
                    choices=[SimpleNamespace(index=0,finish_reason='stop',message=SimpleNamespace(
                        role='assistant',content=delivery['text'],tool_calls=None,reasoning_content=None))],
                    usage=SimpleNamespace(prompt_tokens=0,completion_tokens=0,total_tokens=0))
            return call
        for name in ('_interruptible_api_call','_interruptible_streaming_api_call'):
            original=getattr(agent,name)
            originals[name]=original;setattr(agent,name,wrap(original))
    try:yield state
    finally:
        for name,original in originals.items():setattr(agent,name,original)
