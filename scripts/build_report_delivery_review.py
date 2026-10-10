"""Render audited, complete direct-delivery acceptance; never infer semantic passes."""
import hashlib
import html
import json
from pathlib import Path
import statistics
import sys

root=Path(sys.argv[1]);plan=json.loads((root/'plan.json').read_text())
guidance=plan['arms']==['original_prompt','compact_prompt']
title='精简提示与直出选择对照' if guidance else '查询结果直接交付对照'
reviews=json.loads((root/'manual-review.json').read_text())
turns=[];requests=[];groups=[]
for dataset,repeat in plan['jobs']:
    job=f'{dataset}-{repeat}';folder=root/job
    data=json.loads((folder/'evidence.json').read_text())
    assert data['status']=='COMPLETE_PENDING_WHOLE_ANSWER_REVIEW'
    assert all(data['auth_checks'].values()) and data['thinking']=='disabled'
    for request in folder.glob('request-*.json'):
        req=json.loads(request.read_text())
        assert not any(marker in request.read_text() for marker in ('PRIVATE-A-CANARY','PRIVATE-D-CANARY','LIVE-V9-MUST-NOT-REPLACE-FROZEN'))
        assert req['extra_body']['thinking']=={'type':'disabled'}
        assert req['model']==data['model']
        system=[m for m in req['messages'] if m['role']=='system']
        arm=next(c['arm'] for c in data['calls'] if c['request_file']==request.name)
        if guidance:
            assert data['prompts'][arm] in system[0]['content']
            visible={t['function']['name'] for t in req['tools']}
            assert ('deliver_conversation_results' in visible)==(arm=='compact_prompt')
        requests.append((req['model'],hashlib.sha256(json.dumps(system,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),arm))
    for turn in data['turns']:
        key=f"{job}/{turn['arm']}/{turn['case']}";r=reviews[key]
        assert turn['status']=='completed'
        assert not any(marker in turn['result']['answer'] for marker in ('PRIVATE-A-CANARY','PRIVATE-D-CANARY','LIVE-V9-MUST-NOT-REPLACE-FROZEN'))
        if r.get('quote'):assert r['quote'] in turn['result']['answer'],key
        completions=[c for c in turn['business_calls'] if c['name']=='direct_delivery_completed']
        if completions:
            assert len(completions)==1
            assert json.loads(completions[0]['response'])['data']['text']==turn['result']['answer']
        turns.append({**turn,'job':job,'review':r,'direct':bool(completions)})
    groups.append((job,data))
assert len(turns)==plan['turns'] and len(reviews)==len(turns)
if guidance:
    assert len(set(x[1] for x in requests))==2
    assert all(len({x[1] for x in requests if x[2]==arm})==1 for arm in plan['arms'])
else:
    assert len(set(x[1] for x in requests))==1,'system prompt changed'
summary={}
for arm in plan['arms']:
    items=[t for t in turns if t['arm']==arm]
    summary[arm]={'turns':len(items),'reviews':{s:sum(t['review']['status']==s for t in items) for s in ('pass','concern','fail')},
        'direct_deliveries':sum(t['direct'] for t in items),'provider_calls':sum(t['model_calls'] for t in items),
        'median_seconds':round(statistics.median(t['seconds'] for t in items),2),
        'rejected_tools':sum(not json.loads(c['response']).get('ok') for t in items for c in t['business_calls'])}
(root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
esc=lambda s:html.escape(str(s),quote=True)
rows=''.join(f"<tr><td>{esc(arm)}</td><td>{v['reviews']['pass']}</td><td>{v['reviews']['concern']}</td><td>{v['reviews']['fail']}</td><td>{v['direct_deliveries']}</td><td>{v['provider_calls']}</td><td>{v['median_seconds']}</td></tr>" for arm,v in summary.items())
cards=[]
for job,data in groups:
    for case,question,rubric in data['fixture']['questions']:
        columns=[]
        for arm in plan['arms']:
            t=next(t for t in turns if t['job']==job and t['case']==case and t['arm']==arm)
            r=t['review'];mode='后台直接交付' if t['direct'] else '模型生成回答'
            columns.append(f"<section><h3>{esc(arm)} · {esc(mode)}</h3><p class='{r['status']}'>{esc(r['status'])}：{esc(r['reason'])}</p><p>{t['model_calls']} 次供应商调用 · {t['seconds']} 秒</p><details open><summary>完整最终回答</summary><pre>{esc(t['result']['answer'])}</pre></details><details><summary>完整工具调用与返回</summary><pre>{esc(json.dumps(t['business_calls'],ensure_ascii=False,indent=2))}</pre></details></section>")
        cards.append(f"<article id='{esc(job+'-'+case)}'><h2>{esc(job)} · {esc(question)}</h2><p>验收：{esc(rubric)}</p><div class='pair'>{''.join(columns)}</div></article>")
notes=(root/'conclusions.txt').read_text()
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>查询结果直接交付对照</title><style>
body{font:16px/1.7 system-ui,sans-serif;margin:36px auto;max-width:1500px;padding:0 24px;background:#f5f6f8;color:#182336}h1,h2,h3{line-height:1.4}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}article,header{background:white;padding:24px;border-radius:12px;margin:24px 0}section{min-width:0;border:1px solid #ddd;border-radius:8px;padding:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.7 ui-monospace,monospace}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:10px;text-align:left}.fail{color:#aa2035}.pass{color:#13754b}.concern{color:#966107}summary{cursor:pointer;font-weight:600}@media(max-width:900px){.pair{grid-template-columns:1fr}}</style><body>'''
page=page.replace('<title>查询结果直接交付对照</title>',f'<title>{title}</title>')
page+=f'<header><h1>{title}</h1><p>2026-10-10 · {len(turns)} 轮真实会话 · DeepSeek 非思考 · 独立本地环境</p><pre>{esc(notes)}</pre><table><tr><th>方案</th><th>通过</th><th>有保留</th><th>明确错误</th><th>实际直出轮数</th><th>供应商调用</th><th>每轮耗时中位数</th></tr>{rows}</table><p>“直出轮数”单列：不能把仍由模型生成的正确答案算成直出的效果。统计包含工具选错参数、未使用直出等所有已完成轮次；没有挑选重跑。小样本、合成冻结数据、非盲人工评分，不证明生产准确率或稳定提速。</p></header>'+''.join(cards)+'</body></html>'
(root/(title+'.html')).write_text(page)
(root/'verification.json').write_text(json.dumps({'turns':len(turns),'real_provider_requests':len(requests),'system_hashes':list(set(x[1] for x in requests)),'models':list(set(x[0] for x in requests)),'thinking_disabled':True,'all_turns_completed':True,'direct_text_equals_final_answer':True,'manual_review_complete':True},indent=2))
print(json.dumps(summary,ensure_ascii=False,indent=2))
