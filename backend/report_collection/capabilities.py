"""Read-only projections of existing, authorized frozen report repositories."""
from collections import defaultdict
from .service import reference, public_source


def account_exists(service, ref, session, sources, rows):
    if any(row['account_ref']==ref for row in rows): return
    # A known account can have zero occurrences in a newly narrowed report.
    with service.connect() as db:
        refs=[r[0] for r in db.execute('SELECT ref FROM collection_results WHERE session=?',(session,))]
    for result_ref in refs:
        try: data=service.result(result_ref,session,sources)
        except (PermissionError,ValueError): continue
        if any(item.get('account_ref')==ref for item in data['items']): return
    raise ValueError('账号引用未得到授权结果确认；请先查询账号候选，不能将无效引用解释为零活动。')


def activity(service,args,session,turn,selected):
    rows=service.observations(selected)
    ref=args['account_ref']; account_exists(service,ref,session,service.sources(session,turn),rows)
    rows=[r for r in rows if r['account_ref']==ref]
    if args.get('activity_kind')!='all':
        rows=[r for r in rows if r['kind']==args['activity_kind']]
    if args.get('target_account_ref'):
        target=args['target_account_ref']
        account_exists(service,target,session,service.sources(session,turn),service.observations(selected))
        rows=[r for r in rows if r.get('parent_author_ref')==target]
    rows.sort(key=lambda r:(r.get('published_at') or '',r['source']['report_version_id'],r.get('comment_ref') or r['post_ref']),reverse=True)
    relations=defaultdict(list)
    for row in rows:
        if row['kind']=='comment_author' and row.get('parent_author_ref'):
            relations[row['parent_author_ref']].append(row)
    interactions=[{'target_account_ref':target,'names':sorted({r['parent_author_name'] for r in values}),
        'original_comment_count':len({r['evidence_key'] for r in values}),
        'report_observation_count':len(values),'parent_post_count':len({r['post_id'] for r in values})}
        for target,values in sorted(relations.items())]
    from .analysis import query_statistics
    statistics = query_statistics(rows, selected,
        include_comments=args.get('activity_kind') != 'post_author',
        include_posts=args.get('activity_kind') != 'comment_author' and not args.get('target_account_ref'))
    return service.save(session,turn,selected,'account_activity',rows,args['limit'],
        query_statistics=statistics,
        account_ref=ref,scope_kind='authorized_history' if args['scope']=='authorized_history' else ('single_report' if len(selected)==1 else 'conversation_reports'),
        statistics={'original_comments':len({r['evidence_key'] for r in rows if r['kind']=='comment_author'}),
                    'original_posts':len({r['evidence_key'] for r in rows if r['kind']=='post_author'}),
                    'report_observations':len(rows),'unknown_timestamps':sum(not r.get('published_at') for r in rows)},
        interactions=interactions,query_filters=args,
        limitation='仅授权冻结报告中可验证的活动。评论某作者的帖子不等于直接回复该作者，也不证明双方认识或协同；未收录不代表未发生。')


def content(service,args,session,turn,selected):
    kind=args['kind']; items=[]; matched_refs=set()
    supported={
        'overview':{'text_contains'},
        'findings':{'finding_ref','text_contains'},
        'posts':{'post_ref','finding_ref','risk_level','text_contains'},
        'evidence':{'post_ref','finding_ref','evidence_ref','evidence_type','risk_level','text_contains'},
    }
    filters={'post_ref','finding_ref','evidence_ref','evidence_type','risk_level','text_contains'}
    ignored=sorted(key for key in filters-supported[kind] if args.get(key))
    if ignored:
        raise ValueError(f'{kind} 不支持筛选 {", ".join(ignored)}；未返回未筛选数据。请选用支持该条件的查询类型。')
    for source in selected:
        repo,config=service.repository(source)
        src=public_source(source)
        groups=repo.ordered_investigation_findings()
        group_refs={g.id:reference('finding',[source['version'],g.id]) for g in groups}
        if kind=='overview':
            items.append({'source':src,'overview':repo.report_overview,
                'statistics':repo.report.statistics.model_dump(mode='json'),
                'comment_statistics':repo.report_comment_statistics(),'configuration':config,
                'template_kind':repo.template_kind,'finding_count':len(groups)})
            continue
        if kind=='findings':
            for group in groups:
                gref=group_refs[group.id]; matched_refs.add(gref)
                if args['finding_ref'] and args['finding_ref']!=gref:continue
                items.append({'source':src,'finding_ref':gref,'title':group.title,'statement':group.statement,
                    'boundary_notes':list(group.boundary_notes),
                    'members':[{'post_ref':reference('post',[source['version'],m.post_id]),
                        'representative':m.is_representative,
                        'evidence_refs':[reference('evidence_item',[source['version'],eid]) for eid in m.evidence_ids]}
                        for m in group.memberships]})
            continue
        for post in repo.ordered_posts():
            pref=reference('post',[source['version'],post.id]);matched_refs.add(pref)
            if args['post_ref'] and args['post_ref']!=pref:continue
            membership=[g for g in groups if any(m.post_id==post.id for m in g.memberships)]
            matched_refs.update(group_refs[g.id] for g in membership)
            if args['finding_ref'] and args['finding_ref'] not in [group_refs[g.id] for g in membership]:continue
            finding=repo.finding_for_post(post.id)
            if args['risk_level'] and finding.risk_level!=args['risk_level']:continue
            links={'原帖':post.source.url}
            if kind=='posts':
                items.append({'source':src,'post_ref':pref,'title':post.title,'body':post.body,
                    'author':post.author.model_dump(mode='json'),'published_at':post.source.published_at,
                    'content':repo.post_content(post.id),'assessment':repo.finding_detail_for_post(post.id),
                    'risk_level':finding.risk_level,'decision':finding.decision,
                    'finding_refs':[group_refs[g.id] for g in membership],
                    'evidence_count':len(repo.evidence_for_post(post.id)),'links':links})
            else:
                for evidence in repo.evidence_for_post(post.id):
                    eref=reference('evidence_item',[source['version'],evidence.id]);matched_refs.add(eref)
                    if args['evidence_ref'] and args['evidence_ref']!=eref:continue
                    if args['evidence_type'] and evidence.type!=args['evidence_type']:continue
                    c=evidence.content
                    # Expose text/locators, not private local asset paths.
                    items.append({'source':src,'post_ref':pref,'evidence_ref':eref,'type':evidence.type,
                        'original_text':c.original_text,'translated_text':c.translated_text,'summary':c.summary,
                        'timestamp_start':c.timestamp_start,'timestamp_end':c.timestamp_end,
                        'locator':evidence.source.locator,'availability':evidence.source.availability,'links':links})
    for key in ('post_ref','finding_ref','evidence_ref'):
        if args[key] and args[key] not in matched_refs:
            raise ValueError('内容引用不在本次授权范围，请从报告内容目录中选择。')
    if args['text_contains']:
        import json
        term=args['text_contains'].casefold()
        items=[r for r in items if term in json.dumps(r,ensure_ascii=False).casefold()]
    return service.save(session,turn,selected,'report_'+kind,items,args['limit'],query_filters=args,
        search_method='literal_substring' if args['text_contains'] else 'frozen_order',
        limitations='主题归纳由模型依据正文及证据判断；文本筛选仅做字面匹配。既有发现不代表所有可能主题。证据文本与时间点来自冻结解析，不能据此声称重新观看原始视频或图片。')
