"""Render manually reviewed full-chain comparison; never infer answer grades."""
import argparse
from collections import Counter
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from statistics import median

ARMS=dict(original='原格式＋原提示',data='改格式',semantic='94字事实提示',brief='90字聚焦提示',
          data_semantic='改格式＋事实提示',data_brief='改格式＋聚焦提示')
DATASETS=dict(identity='三报告身份与连续追问',config='多帖子、冻结配置与因果分析',paging='27条评论、角色切换与权限')


def main():
    global ARMS
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);args=p.parse_args();root=args.directory
    load=lambda name:json.loads((root/name).read_text())
    analysis_comparison=load('plan.json').get('arms')==['selected_baseline','analysis_tools']
    if analysis_comparison:
        ARMS=dict(selected_baseline='已选基线：格式＋聚焦＋事实提示',analysis_tools='相同基线＋新增分析工具能力')
    review=load('manual-review.json');judgments=review['records'];summary=load('summary.json')
    jobs={f'{d}-{r}':load(f'{d}-{r}/evidence.json') for d in DATASETS for r in [1,2]}
    verification=dict(turns=0,model_calls=0,tool_calls=0,tool_rejections=[],
        requested_models=set(),thinking_disabled=True,private_or_live_markers_exposed=False)
    rows=[]
    for name,job in jobs.items():
        assert job['status']=='COMPLETE_PENDING_WHOLE_ANSWER_REVIEW', (name,job['status'])
        assert all(job['auth_checks'].values()), name
        for call in job['calls']:
            request=load(name+'/'+call['request_file'])
            assert request['extra_body']['thinking']=={'type':'disabled'}
            assert request['model']==job['model']
            verification['requested_models'].add(request['model'])
            verification['model_calls']+=1
            raw=json.dumps(request,ensure_ascii=False)
            assert not any(marker in raw for marker in ['PRIVATE-A-CANARY','PRIVATE-D-CANARY','LIVE-V9-MUST-NOT-REPLACE-FROZEN'])
        for turn in job['turns']:
            key=f'{name}/{turn["arm"]}/{turn["case"]}'
            assert key in judgments,key
            j=judgments[key];assert not j['quote'] or j['quote'] in turn['result']['answer']
            rows.append(dict(key=key,job=name,**turn,review=j))
            verification['turns']+=1
            for call in turn['business_calls']:
                verification['tool_calls']+=1
                response=json.loads(call['response'])
                if response.get('ok') is False:
                    verification['tool_rejections'].append(dict(key=key,name=call['name'],response=response))
    assert set(judgments)=={r['key'] for r in rows}
    assert len(rows)==(48 if analysis_comparison else 144), 'Do not publish a partial run as the planned comparison'
    verification['requested_models']=sorted(verification['requested_models'])
    (root/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2))
    e=lambda value:escape(str(value))
    def pre(value):return '<pre>'+e(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2))+'</pre>'
    def details(title,body):return '<details><summary>'+e(title)+'</summary>'+body+'</details>'
    def grade(j):
        return 'concern' if j['status']=='pass' and j.get('notes') else j['status']
    names=dict(pass_='未发现错误',concern='有疑点',fail='明确错误',partial='漏答',incomplete='未完成')
    names['pass']=names.pop('pass_')
    out=['''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>多报告多样问答：六组完整链路对照</title><style>
    body{font:16px/1.7 system-ui,sans-serif;color:#152334;background:#f3f6fa;margin:0}main{max-width:1200px;margin:auto;padding:30px}
    h1{font-size:30px}h2{margin-top:36px}section,details{background:#fff;border:1px solid #d8e0ea;border-radius:10px;padding:16px;margin:12px 0}
    summary{font-weight:600;cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.7 ui-monospace,monospace}table{border-collapse:collapse;width:100%;background:white;font-size:14px}td,th{padding:9px;border:1px solid #d9e1ec;text-align:left}th{background:#e8eef7}
    .fail{color:#9c2f22}.concern,.partial{color:#7b580f}.pass{color:#17643e}.incomplete{color:#61558a}a{color:#1654a1}.small{font-size:14px;color:#536174}blockquote{border-left:3px solid #bc5b48;margin:12px 0;padding:8px 16px}</style><main>
    <h1>多报告、多样问答：六组完整链路对照</h1>''']
    out.append('<section>'+''.join('<p>'+e(s)+'</p>' for s in summary['conclusions'])+'</section>')
    if analysis_comparison:
        out[0]=out[0].replace('多报告多样问答：六组完整链路对照','分析工具补齐：固定提示词完整链路对照').replace('多报告、多样问答：六组完整链路对照','分析工具补齐：固定提示词完整链路对照')
        out.append('<p>沿用上次三套合成报告及12类原问题，每种工具配置重复2次。每个会话连续完成4问；两组均固定为949f3d6的格式及301字报告提示，不更换模型或开启思考。真实 Hermes / DeepSeek、实际只读工具、本地鉴权接口，共48轮。仅新增正文筛选、报告组合统计及样本比较能力。</p>')
    else:
        out.append('<p>三套新合成报告，12类问题，每组六种配置各重复2次。每个会话连续完成4问，使用真实 Hermes / DeepSeek、实际只读工具和本地鉴权 HTTP 接口。没有把回答边界单独抽出来重放。</p>')
    out.append('<h2>整段答案与任务完成度</h2><p>只要表格、补充或结尾有明确错误，整段就失败；漏答子问题单列。存在实质歧义、无依据能力承诺或其他表达问题的答案单列，不包装成全对。数据不是生产准确率。</p>')
    out.append('<table><tr><th>方案</th><th>未发现错误</th><th>有疑点</th><th>明确错误</th><th>漏答</th><th>未完成</th><th>总轮数</th><th>中位耗时</th><th>模型调用</th></tr>')
    for arm,label in ARMS.items():
        selected=[r for r in rows if r['arm']==arm];counts=Counter(grade(r['review']) for r in selected)
        cells=[label,*[counts[s] for s in ['pass','concern','fail','partial','incomplete']],len(selected),
               f'{median(r["seconds"] for r in selected):.1f}秒',sum(r['model_calls'] for r in selected)]
        out.append('<tr>'+''.join('<td>'+e(v)+'</td>' for v in cells)+'</tr>')
    out.append('</table><p class="small">耗时是本地 HTTP 轮次端到端耗时（含模型自主工具调用）。两进程并发、有供应商波动，不能据此作精确性能排名。</p>')
    out.append('<h2>按问题类型看明确错误</h2><p>每格分母为8轮。漏答和未完成不混作事实错误；仍须结合上表看任务是否完成，不能把它们算作答对。</p><table><tr><th>方案</th>'+''.join('<th>'+e(label)+'</th>' for label in DATASETS.values())+'<th>重复1 / 重复2</th></tr>')
    for arm,label in ARMS.items():
        selected=[r for r in rows if r['arm']==arm]
        cells=[label]
        cells.extend(str(sum(r['review']['status']=='fail' for r in selected if r['job'].startswith(d+'-')))+'/8' for d in DATASETS)
        cells.append(' / '.join(str(sum(r['review']['status']=='fail' for r in selected if r['job'].endswith('-'+str(repeat))))+'/12' for repeat in [1,2]))
        out.append('<tr>'+''.join('<td>'+e(v)+'</td>' for v in cells)+'</tr>')
    out.append('</table>')
    for dataset,label in DATASETS.items():
        spec=jobs[dataset+'-1']['fixture'];out.append('<h2>'+e(label)+'</h2><table><tr><th>问题</th>'+''.join('<th>'+e(n)+'</th>' for n in ARMS.values())+'</tr>')
        for case,question,rubric in spec['questions']:
            out.append('<tr><td>'+e(question)+'</td>')
            for arm in ARMS:
                links=[]
                for repeat in [1,2]:
                    key=f'{dataset}-{repeat}/{arm}/{case}'
                    if key not in judgments:links.append('缺失');continue
                    g=grade(judgments[key]);links.append(f'<a class="{g}" href="#{e(key)}">{repeat}：{names[g]}</a>')
                out.append('<td>'+'<br>'.join(links)+'</td>')
            out.append('</tr>')
        out.append('</table>')
        out.append(details('原始报告数据与预先固定的期望答案',pre(spec)))
        for row in [r for r in rows if r['job'].startswith(dataset+'-')]:
            j=row['review'];g=grade(j)
            body='<p><b>用户：</b>'+e(row['question'])+'</p><p><b>判定：</b>'+e(j['reason'])+'</p>'
            if j['quote']:body+='<blockquote>'+e(j['quote'])+'</blockquote>'
            body+=''.join('<p class="concern">'+e(n)+'</p>' for n in j.get('notes',[]))
            body+='<h3>完整回答</h3>'+pre(row['result']['answer'])
            body+=details('实际业务工具调用与结果',pre(row['business_calls']))
            body+=details('对应的预定判分依据',pre(row['rubric']))
            out.append(f'<div id="{e(row["key"])}">'+details(row['key']+' · '+names[g],body)+'</div>')
    out.append('<h2>实验边界与证据</h2>'+''.join('<p>'+e(s)+'</p>' for s in summary['limitations']))
    out.append(details('请求参数、权限标记和工具错误核对',pre(verification)))
    out.append(details('模型调用前登记的计划',pre(load('plan.json'))))
    out.append(details('提示原文',pre(jobs['identity-1']['prompts'])))
    for name,job in jobs.items():
        out.append(f'<p><a href="{e(name)}/evidence.json">{e(name)} 完整证据 JSON</a> · '+e(job['status'])+'</p>')
    out.append('</main></html>')
    target=root/('分析工具补齐对照.html' if analysis_comparison else '多报告多样问答六组对照.html');target.write_text(''.join(out))
    manifest={str(p.relative_to(root)):sha256(p.read_bytes()).hexdigest() for p in root.glob('*/request-*.json')}
    manifest.update({str(p.relative_to(root)):sha256(p.read_bytes()).hexdigest() for p in root.glob('*/evidence.json')})
    (root/'evidence-manifest.json').write_text(json.dumps(manifest,indent=2))
    print(target)


if __name__=='__main__':main()
