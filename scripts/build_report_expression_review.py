"""Publish the manually reviewed small expression/prompt experiment locally."""
import argparse
from hashlib import sha256
from html import escape
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output
    evidence = json.loads((root / 'evidence.json').read_text())
    continuations = json.loads((root / 'continuations.json').read_text())
    by_parent = {r['parent_id']: r for r in continuations}
    arm_names = dict(baseline='原样', data='仅调整工具结果', prompt='仅增加 94 字提示', both='工具结果 + 短提示')
    case_names = dict(count='同一评论与多次研判', unknown='身份未知', filter='高风险筛选与发帖者')
    notes = {
        'count-data-1': ['模型继续要求账号聚合查询；本轮回放未完成答案，不计对错。'],
        'count-data-2': ['计数正确；但结尾说身份未知评论“不属于本账号”，把未关联说成排除归属。'],
        'count-both-2': ['计数正确；但结尾说身份未知评论“那不属于本账号”，仍有错误的确定性排除。'],
        'count-prompt-2': ['开头答 1 条评论、2 次研判；结尾又因报告内帖子引用不同，说“不宜直接断言它是同一条线上评论实体”。这与工具稳定证据身份的语义冲突。'],
        'filter-data-1': ['筛选结论正确，但用户要求只看 B，答案仍加入“A 中同一内容判为 low”。'],
        'unknown-baseline-1': ['核心回答正确；“缺失稳定 ID”被当成具体未解析原因，工具未提供该原因字段。'],
        'unknown-baseline-2': ['核心回答正确；“该评论因缺失稳定账号 ID 而未解析身份”超出工具明确提供的状态。'],
        'unknown-data-1': ['核心回答正确；从没有 account_ref 直接推导缺失原始稳定 ID，原因依据不足。'],
        'unknown-data-2': ['核心回答正确；结尾“目前资料中缺失”可理解为当前工具未给身份信息。仍不能将空 account_ref 推广为原始平台 ID 必然缺失。'],
        'unknown-both-1': ['核心回答正确；“该条因缺失稳定 ID”仍把一般关联规则当成单条原因。'],
        'unknown-both-2': ['核心回答正确；“该评论本身就是这样落库的”没有数据库证据，identity_status 实际来自实验投影；另有未解析原因的过度推断。'],
        'unknown-prompt-1': ['核心回答正确；“当前结果中不存在可用身份信息”与工具一致。不能据此断言原始数据的缺失原因。'],
        'unknown-prompt-2': ['核心回答正确；“unknown 这条因缺稳定 ID 而身份不明”未由逐条原因字段支持。'],
    }
    rows = []
    for initial in evidence['records']:
        final = by_parent.get(initial['id'], initial)
        completed = final['status'] == 'completed'
        rows.append(dict(id=initial['id'], arm=initial['arm'], case=initial['case'],
            completed=completed, core_conclusion_correct=completed and initial['id'] != 'count-prompt-2',
            notes=notes.get(initial['id'], ['本例核心事实与身份口径正确；小样本不证明稳定改善。']),
            initial=initial, continuation=by_parent.get(initial['id']), final=final))
    summary = []
    for arm in arm_names:
        rs = [r for r in rows if r['arm'] == arm]
        calls = [r['initial'] for r in rs] + [r['continuation'] for r in rs if r['continuation']]
        summary.append(dict(arm=arm, completed=sum(r['completed'] for r in rs), total=len(rs),
            core_correct=sum(r['core_conclusion_correct'] for r in rs),
            api_calls=len(calls), sum_request_seconds=round(sum(c['seconds'] for c in calls), 3),
            final_answer_characters=sum(len(r['final'].get('answer', '')) for r in rs if r['completed'])))
    payload = dict(decision='NO_ROLLOUT_NO_RELIABLE_IMPROVEMENT_SHOWN',
        reviewer='Primary assistant manual review; not a blind independent evaluation.',
        valid_initial_calls=len(evidence['records']), requested_tool_continuations=len(continuations),
        summary=summary, rows=rows,
        limitations=[
            '三个固定合成问题，每组各重复两次，不是独立的生产问题样本。',
            '本轮原样组也正确回答了过去失败的计数问题，不能证明改动带来改善。',
            '身份未知和风险筛选使用的是先前已答对的控制案例，不能称为修好三次失败。',
            '已知账号统计程序错误在四组统一修正，旧回答文字保持原样。',
            '六次续答提供与模型请求完全匹配的完整存档响应，没有执行真实服务查询。',
            '一份答案仍在请求另一个工具，因此未完成，不能当作事实错误。',
            '没有进行本轮单报告回归、完整会话、浏览器、分页或多轮创建验收。',
            '首批因 Hermes 调用包装解析有误而作废，未混入修正版统计。',
            '初始请求并行、续答顺序执行，不据此宣称延迟改善。'
        ])
    (root / 'manual-review.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    table = '<table><tr><th>组别</th><th>已完成回答</th><th>核心问题答对*</th><th>模型请求（含继续查工具）</th></tr>'
    for s in summary:
        table += f'<tr><td>{arm_names[s["arm"]]}</td><td>{s["completed"]}/{s["total"]}</td><td>{s["core_correct"]}/{s["total"]}</td><td>{s["api_calls"]}</td></tr>'
    table += '</table>'
    markup = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>工具表达与短提示 · 实验结果</title><style>
body{margin:0;background:#f4f5f3;color:#24313a;font:16px/1.7 system-ui,-apple-system,"PingFang SC",sans-serif}main{max-width:1050px;margin:40px auto;padding:0 24px 60px}section,article{background:#fff;border:1px solid #d9dfdc;border-radius:12px;padding:24px;margin:20px 0}h1{font-size:30px;line-height:1.3}h2{font-size:22px}table{width:100%;border-collapse:collapse}td,th{text-align:left;vertical-align:top;border-bottom:1px solid #ddd;padding:10px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px}summary{cursor:pointer;color:#315e51;padding:10px 0}.note{border-left:4px solid #b38135;padding:10px 16px;background:#fff6e7}.muted{font-size:14px;color:#64716d}a{color:#266454}code{overflow-wrap:anywhere}</style><main>
<p class="muted">2026-10-10 · 本地独立工作区 · DeepSeek 非思考模式 · 无云端变更</p>
<h1>工具表达与短提示：未证明可靠改善</h1>
<section><p>四组分别保留原样、仅调整工具结果、仅增加短提示、同时调整。修正版完成 24 次初始请求，另有 6 次根据模型自主工具请求的存档结果续答。总计 23 份最终答案，1 份继续要求工具，未完成。</p>
<p><strong>原样组在这轮也答对了三类核心问题，所以不能把其他组答对归功于改动。</strong>新增方案仍出现“开头计数正确，后文错误排除未知账号”、因报告内引用不同而怀疑同一实体，以及只看 B 时附带 A 的判断。</p>'''
    markup += table + '<p class="muted">* 核心问题答对不等于整段答案合格。身份未解析原因的依据不足、范围外补充等另列于逐条复核；这不是上线通过率。</p></section>'
    markup += '<section><h2>具体改动</h2><ul><li>评论按已有 evidence_key 分组，保留全部原始行及其顺序索引、来源、报告内引用。</li><li>结果带实际过滤条件及分页/样例完整性；读回保留原查询条件。</li><li>逐条标记已解析或未知；不新增未知原因。</li></ul><p>仅提示组追加以下 94 字；同时调整组使用同样文字：</p><pre>' + escape(evidence['prompt']) + '</pre><p>没有改历史内容、工具能力、报告权限或查询流程。没有开启思考、答案校验或失败重答。</p></section>'
    markup += '<section><h2>本轮限制与决定</h2><ul>' + ''.join('<li>'+escape(x)+'</li>' for x in payload['limitations']) + '</ul><p class="note">保留实验代码，暂不接入产品。不能据此宣称模型能力不足已被证明，也不能宣称此方案修好了三类错误。</p><p><a href="manual-review.json">逐条人工复核与统计</a> · <a href="evidence.json">初始对照证据</a> · <a href="continuations.json">模型要求读回后的续答</a></p></section>'
    for row in sorted(rows, key=lambda r: (r['case'], r['arm'], r['id'])):
        markup += '<article><h2>'+escape(case_names[row['case']]+' · '+arm_names[row['arm']]+' · '+row['id'])+'</h2><p>'+escape(row['initial']['question'])+'</p><p class="note">'+'<br>'.join(escape(n) for n in row['notes'])+'</p>'
        markup += '<details><summary>查看未经修改的最终输出</summary><pre>'+escape(row['final'].get('answer',''))+'</pre></details>'
        if row['continuation']:
            markup += '<details><summary>查看它先发起的工具请求</summary><pre>'+escape(json.dumps(row['initial']['tool_calls'],ensure_ascii=False,indent=2))+'</pre></details>'
        markup += '</article>'
    markup += '</main></html>'
    target = root / '工具表达与短提示对照.html'
    target.write_text(markup)
    files = ['scripts/report_expression_trial.py', 'scripts/report_expression_continue.py',
             'scripts/build_report_expression_review.py', 'tests/test_report_expression_trial.py']
    repo = Path(__file__).resolve().parents[1]
    manifest = {name: sha256((repo/name).read_bytes()).hexdigest() for name in files}
    (root / 'code-manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(summary, ensure_ascii=False))
    print(target)


if __name__ == '__main__':
    main()
