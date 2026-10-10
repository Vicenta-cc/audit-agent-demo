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
    refs=args.get('result_refs',[])
    labels=args.get('report_labels',[])
    if not refs and not labels: raise ValueError('请选择查询结果或需要检查的报告名称。')
    if len(set(refs))!=len(refs): raise ValueError('不能重复交付同一结果。')
    blocks=[];counts=[];references=[]
    for ref in refs:
        data=service.result(ref,session,sources)
        if data['kind'] not in {'comments','accounts'}: raise ValueError('此结果不支持列表直出。')
        if 'query_filters' not in data: raise ValueError('旧结果缺少查询条件，请重新查询后交付。')
        rows=data['items']
        if len(rows)>1000: raise ValueError('完整列表超过直出原型的 1000 行上限；未交付部分结果。')
        filters=data['query_filters']
        scope=['报告：'+'、'.join(cell(s['label']) for s in data['sources']),
            '风险：'+RISK.get(filters.get('risk_level'),'不限')]
        if data['kind']=='accounts':scope.append('账号范围：'+ROLE.get(filters.get('account_role','any'),'未知'))
        if filters.get('account_ref'):scope.append('仅统计指定账号')
        if filters.get('text_exact') is not None:scope.append('评论原文为“'+cell(filters['text_exact'])+'”')
        if filters.get('parent_result_ref'):scope.append('仅统计此前选定的内容')
        if filters.get('common_only'):scope.append('仅统计所有所选报告都出现的账号')
        # Display aliases disambiguate identical names without disclosing internal
        # IDs. Full identities and immutable sources remain in structured metadata.
        identities={}
        for row in rows:
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
            unique=len({r['evidence_key'] for r in rows})
            completed=sum(r['audit_status']=='completed' for r in rows)
            lines=[f'**评论列表 · 共 {len(rows)} 条收录**', '；'.join(scope)+'。',
                f'对应 {unique} 条原始评论，已完成研判 {completed} 次。以下按读取顺序完整列出。',
                '| 序号 | 评论原文 | 作者 | 风险判断 | 来源 |',
                '| --- | --- | --- | --- | --- |']
            for i,row in enumerate(rows,1):
                risk=RISK.get(row['risk_level'],'待确认') if row['audit_status']=='completed' else '未完成研判'
                source=' · '.join(filter(None,(cell(row['source']['label']),source_links(row))))
                lines.append(f"| {i} | {cell(row['text'])} | {author(row)} | {risk} | {source} |")
        else:
            lines=[f'**账号列表 · 共 {len(rows)} 个账号**','；'.join(scope)+'。',
                '数量及风险判断仅限上述范围。',
                '| 序号 | 账号 | 评论数 | 帖子数 | 各报告记录及风险 | 来源 |',
                '| --- | --- | ---: | ---: | --- | --- |']
            for i,row in enumerate(rows,1):
                reports='；'.join(f"{s['label']}：评论 {s['comment_count']} 条，帖子 {s['post_count']} 篇；{ '、'.join(RISK.get(r,'待确认') for r in s['risk_levels']) or '暂无判断'}" for s in row['report_statistics'])
                lines.append(f"| {i} | {author(row)} | {row['unique_comment_count']} | {row['unique_post_count']} | {cell(reports)} | {source_links(row) or '—'} |")
            unknown=data.get('unresolved_identity_comments',0)
            if unknown:lines.append(f'另有 {unknown} 条评论的作者身份待确认，未计入上述账号，不能据此排除关联。')
        if any(len(ids)>1 for ids in identities.values()):
            lines.append('同名账号已分别标记；“本表账号”编号仅用于区分本张表中的不同账号。')
        references.append({'result_ref':ref,'sources':data['sources'],'query_filters':filters,
            'rows':[{'position':i,'account_ref':row.get('account_ref'),
                'comment_ref':row.get('comment_ref'),'source':row.get('source'),
                'links':row.get('links',{})} for i,row in enumerate(rows,1)]})
        counts.append(len(rows));blocks.append('\n\n'.join(lines[:3])+'\n\n'+'\n'.join(lines[3:]))
    if labels:
        by_label={s['label']:s for s in sources}
        lines=['**报告访问情况**','']
        for label in labels:
            if label in by_label:
                lines.append(f'- **{cell(label)}**：当前会话可以查看。本项仅核实访问权限，未读取或统计报告内容。')
            else:
                lines.append(f'- **{cell(label)}**：当前会话无权读取，未读取、未参与统计。')
        lines.append('\n仅凭昵称相同不能合并账号；需要可靠的平台账号身份。')
        blocks.append('\n'.join(lines))
    text='\n\n---\n\n'.join(blocks)
    if len(text)>200000: raise ValueError('结果超过直出原型的文本容量，未交付截断结果。')
    return {'delivery':'direct','text':text,'result_refs':refs,'row_counts':counts,
            'report_labels_checked':labels,'complete':True,'references':references}


def pending_delivery(service,session,turn):
    with service.connect() as db:
        rows=db.execute("SELECT arguments,response FROM collection_calls WHERE session=? AND turn=? AND name='deliver_conversation_results' ORDER BY id DESC",(session,turn)).fetchall()
    for row in rows:
        if json.loads(row['response']).get('ok'):
            # Resolve durable refs again, including live permission check at delivery.
            return prepare_delivery(service,json.loads(row['arguments']),session,turn,service.sources(session,turn))
    return None


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
