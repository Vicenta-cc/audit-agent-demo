"""Deterministic, opt-in delivery of authorized query results, not prose validation."""
import html
import json
import re
from contextlib import contextmanager
from functools import wraps
from types import SimpleNamespace

RISK={'none':'无风险','low':'低风险','medium':'中风险','high':'高风险'}
ROLE={'any':'发帖者与评论者','comment_author':'评论作者','post_author':'发帖者','both':'同时发帖和评论的账号'}


def cell(value):
    # Evidence is literal data, including Markdown/HTML and embedded instructions.
    value=html.escape(str(value), quote=True).replace('\n',' ').replace('\r',' ')
    return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])',r'\\\1',value)


def prepare_delivery(service,args,session,turn,sources):
    refs=args.get('result_refs',[])
    labels=args.get('report_labels',[])
    if not refs and not labels: raise ValueError('请选择查询结果或需要检查的报告名称。')
    if len(set(refs))!=len(refs): raise ValueError('不能重复交付同一结果。')
    blocks=[];counts=[]
    for ref in refs:
        data=service.result(ref,session,sources)
        if data['kind'] not in {'comments','accounts'}: raise ValueError('此结果不支持列表直出。')
        if 'query_filters' not in data: raise ValueError('旧结果缺少查询条件，请重新查询后交付。')
        rows=data['items']
        # Explicit prototype cap; never silently turn a preview into a complete list.
        if len(rows)>1000: raise ValueError('完整列表超过直出原型的 1000 行上限；未交付部分结果。')
        filters=data['query_filters']
        source_text='；'.join(cell(s['label'])+'（版本 '+cell(s['version'])+'）' for s in data['sources'])
        scope='风险='+RISK.get(filters.get('risk_level'),'不限')
        if data['kind']=='accounts': scope+='；角色='+ROLE.get(filters.get('account_role','any'),'未知')
        for key,label in [('account_ref','账号'),('text_exact','正文精确匹配'),('parent_result_ref','限定先前结果')]:
            if filters.get(key) is not None and filters.get(key)!='':scope+='；'+label+'='+cell(filters[key])
        if filters.get('common_only'):scope+='；仅全部所选报告的共同账号'
        lines=[f'报告：{source_text}',f'查询条件：{scope}',f'结果引用：{cell(ref)}']
        if data['kind']=='comments':
            unique=len({r['evidence_key'] for r in rows})
            completed=sum(r['audit_status']=='completed' for r in rows)
            lines += [f'完整结果：{len(rows)} 条报告收录记录，{unique} 条原始评论，{completed} 次已完成研判。按查询结果原有顺序编号。',
                '| 序号 | 评论 | 作者 | 风险判断 | 报告 | 评论引用 |','| --- | --- | --- | --- | --- | --- |']
            for i,row in enumerate(rows,1):
                author=cell(row['account_name'])+'（'+cell(row['account_ref'])+'）' if row['account_ref'] else cell(row['account_name'])+'（身份未知）'
                risk=RISK.get(row['risk_level'],row['risk_level']) if row['audit_status']=='completed' else '未完成研判'
                lines.append(f"| {i} | {cell(row['text'])} | {author} | {cell(risk)} | {cell(row['source']['label'])} | {cell(row['comment_ref'])} |")
        else:
            lines += [f'完整结果：{len(rows)} 个稳定身份账号。数量和角色均限于上述查询条件。',
                '| 序号 | 账号名 | 稳定账号引用 | 原始评论数 | 原始帖子数 | 各报告记录及判断 |',
                '| --- | --- | --- | --- | --- | --- |']
            for i,row in enumerate(rows,1):
                reports='；'.join(f"{s['label']}：评论 {s['comment_count']}，帖子 {s['post_count']}，判断 {','.join(RISK.get(r,r) for r in s['risk_levels']) or '无记录'}" for s in row['report_statistics'])
                lines.append(f"| {i} | {cell('、'.join(row['names']))} | {cell(row['account_ref'])} | {row['unique_comment_count']} | {row['unique_post_count']} | {cell(reports)} |")
            lines.append(f"本次筛选中身份未知的评论记录：{data.get('unresolved_identity_comments',0)} 条，未归并到上述账号；不能据此排除身份关联。")
        counts.append(len(rows));blocks.append('\n\n'.join(lines[:4])+'\n\n'+'\n'.join(lines[4:]))
    if labels:
        by_label={s['label']:s for s in sources}
        lines=['报告可访问性（仅检查本会话获准读取的报告目录）：']
        for label in labels:
            if label in by_label:
                lines.append(f'- {cell(label)}：本会话可访问；此检查未读取或统计其内容。')
            else:
                lines.append(f'- {cell(label)}：不在本会话获准访问的报告目录内，未读取、未参与统计。')
        lines.append('同名不足以确认同一账号；账号关联使用可靠平台身份，不按昵称合并。')
        blocks.append('\n'.join(lines))
    text='\n\n---\n\n'.join(blocks)
    if len(text)>200000: raise ValueError('结果超过直出原型的文本容量，未交付截断结果。')
    return {'delivery':'direct','text':text,'result_refs':refs,'row_counts':counts,
            'report_labels_checked':labels,'complete':True}


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
