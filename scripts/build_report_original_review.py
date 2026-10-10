"""Render original-scene evidence and manually reviewed FULL answers, offline."""
import argparse
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from statistics import median

ARMS = dict(original='原数据＋原提示', data='分组数据＋原提示', semantic='原数据＋事实语义短提示',
            brief='原数据＋聚焦回答短提示', data_semantic='分组数据＋事实语义短提示',
            data_brief='分组数据＋聚焦回答短提示')
SCENES = dict(followup='连续追问：刚才那条评论是谁发的？', publisher='高风险筛选后：这些评论分别是谁发的？')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = args.directory
    load = lambda name: json.loads((root/name).read_text())
    batches = {p: load(p+'.json') for p in ('baseline','compare','confirm')}
    review = load('whole-answer-review.json')
    judgments = {r['id']: r for r in review['records']}
    all_records = [r for b in batches.values() for r in b['records']]
    assert set(judgments) == {r['id'] for r in all_records}
    for r in all_records:
        for finding in judgments[r['id']].get('findings', []):
            assert finding['quote'] in r['answer'], (r['id'],finding['quote'])
    def e(value):
        return escape(str(value))
    def pre(value):
        return '<pre>'+e(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2))+'</pre>'
    def detail(title, content):
        return '<details><summary>'+e(title)+'</summary>'+content+'</details>'
    out = ['''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>原始失败场景：完整回答对照</title><style>
    body{font:16px/1.7 system-ui,sans-serif;color:#172333;background:#f4f6fa;margin:0}main{max-width:1100px;margin:auto;padding:32px}
    h1{font-size:30px}h2{font-size:23px;margin-top:38px}h3{font-size:19px}section,details,.box{background:white;border:1px solid #dce2eb;border-radius:10px;padding:18px;margin:14px 0}
    details details{background:#f8fafc}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.7 ui-monospace,monospace}
    table{border-collapse:collapse;width:100%;background:white;font-size:14px}td,th{padding:11px;border:1px solid #dce2eb;text-align:left}th{background:#eaf0f7}
    .bad{border-left:5px solid #b94436}.note{color:#63521c}.small{font-size:14px;color:#526071}a{color:#1655a3}blockquote{border-left:3px solid #bd5e48;margin:12px 0;padding-left:16px}
    </style><main><h1>原始失败场景：完整回答对照</h1>''']
    out.append('<div class="box">'+''.join('<p>'+e(p)+'</p>' for p in review['conclusions'])+'</div>')
    out.append('<p>同一非思考模型、完整历史、原问题、相同工具能力。首轮复现 6 次；六组首批对照 36 次；保持方案不变追加 60 次，共 '+str(len(all_records))+' 次调用。全部原始请求、完整回答及人工判断保存在此目录。</p>')
    out.append('<p class="note">下表“明确错误”检查整段正文、表格、补充说明和结尾；“另有表达疑点”不冒充正确，也不硬判为已证实事实错误。未发现明确错误 ≠ 已证明稳定正确。每个场景每组仅 8 次，同一合成资料重复，不代表生产准确率。</p>')
    compared = batches['compare']['records'] + batches['confirm']['records']
    for scene,label in SCENES.items():
        out.append('<h2>'+e(label)+'</h2><table><tr><th>方案</th><th>首批明确错误</th><th>追加明确错误</th><th>合计明确错误</th><th>另有表达疑点</th><th>未完成</th><th>中位耗时</th></tr>')
        for arm,name in ARMS.items():
            rows=[r for r in compared if r['scene']==scene and r['arm']==arm]
            fail=lambda rs:sum(judgments[r['id']]['status']=='whole_answer_fail' for r in rs)
            first=[r for r in rows if r['id'].startswith('compare-')]
            second=[r for r in rows if r['id'].startswith('confirm-')]
            concerns=sum(bool(judgments[r['id']].get('concerns')) and judgments[r['id']]['status']!='whole_answer_fail' for r in rows)
            incomplete=sum(judgments[r['id']]['status']=='incomplete' for r in rows)
            cells=[name,f'{fail(first)}/{len(first)}',f'{fail(second)}/{len(second)}',f'{fail(rows)}/{len(rows)}',concerns,incomplete,f'{median(r["seconds"] for r in rows):.2f} 秒']
            out.append('<tr>'+''.join('<td>'+e(c)+'</td>' for c in cells)+'</tr>')
        out.append('</table>')
    out.append('<h2>先复现，再比较</h2>'+pre(load('baseline-review.json')))
    out.append('<h2>具体错在哪里</h2>')
    for r in all_records:
        j=judgments[r['id']]
        if j['status']!='whole_answer_fail':continue
        out.append('<section class="bad"><h3>'+e(r['id'])+'</h3>')
        for f in j['findings']:
            out.append('<blockquote>'+e(f['quote'])+'</blockquote><p>'+e(f['reason'])+'</p>')
        out.append(detail('查看这次完整回答',pre(r['answer']))+'</section>')
    out.append('<h2>原始现场完整展开</h2><p>原问题未改写；前文旧回答、工具报错、查询顺序与结果均保留。仅移除最后一条待复现的错误答案，再让模型生成。</p>')
    for scene,label in SCENES.items():
        s=load('scene-'+scene+'.json')
        inner='<p>来源：'+e(s['source_directory'])+'<br>原轮次：'+e(s['turn_id'])+'</p>'
        inner+=detail('当时的完整错误回答',pre(s['historical_answer']))
        for i,m in enumerate(s['transcript'][:-1]):
            inner+=detail(f'{i+1}. {m["role"]}',pre(m))
        out.append(detail(label,inner))
    out.append('<h2>六组到底改了什么</h2><p>数据只有两种：原结构、按已有 evidence_key 分组并补充实际过滤范围与身份状态的结构。提示有三种：原提示、追加 94 字事实语义提示、追加 90 字聚焦回答提示。六组为两者完整组合；没有改动历史用户和助手文字，没有强制工具顺序或二次重答。</p>')
    out.append(detail('两段追加提示词原文',pre(batches['compare']['prompts'])))
    before=load('request-compare-followup-original-1.json')
    after=load('request-compare-followup-data-1.json')
    diffs=[]
    for i,(a,b) in enumerate(zip(before['messages'],after['messages'])):
        if a['role']=='tool' and a!=b:
            diffs.append(detail(f'工具结果 {i}：原格式 / 分组格式', '<h3>原格式</h3>'+pre(a['content'])+'<h3>分组格式</h3>'+pre(b['content'])))
    out.append(detail('数据格式逐项对照（不是只展示摘要）',''.join(diffs)))
    out.append('<h2>全部回答与逐段判断</h2>')
    for phase,batch in batches.items():
        content=''
        for r in sorted(batch['records'],key=lambda x:x['id']):
            j=judgments[r['id']]
            content+=detail(r['id']+' · '+j['status'],pre(j)+pre(r['answer']))
        out.append(detail(phase+' · '+str(len(batch['records']))+' 次',content))
    out.append('<h2>方法与限制</h2>'+''.join('<p>'+e(p)+'</p>' for p in review['limitations']))
    out.append(detail('预先登记的整段评判标准',pre(load('rubric.json'))))
    out.append(detail('请求重建核验',pre(load('scene-followup.json')['reconstruction'])))
    out.append('<p class="small">模型：'+e(batches['compare']['model'])+'；thinking=disabled；无自动重试；并发 2；未修改产品代码、未推送、未改云端。耗时只包含回答阶段的一次模型调用，不是完整会话端到端耗时。</p></main></html>')
    path=root/'原始失败场景完整对照.html'
    path.write_text(''.join(out))
    manifest={str(p.name):sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob('*.json'))
              if p.name != 'evidence-manifest.json'}
    (root/'evidence-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(path)


if __name__=='__main__':
    main()
