"""Offline presentation experiment over explicitly selected, saved tool calls.

No model calls, intent routing, answer scanning, production integration or new
permission policy. Input traces must already be authorized for the local viewer.
This is NOT an API for exposing arbitrary stored results to product users.
"""
import argparse
from collections import defaultdict
from hashlib import sha256
from html import escape
import json
from pathlib import Path


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def complete_page(data):
    return (data.get('has_more') is False
            and data.get('next_offset') == len(data['items']) == data.get('total_count'))


def comment_facts(data):
    if data['kind'] != 'comments':
        raise ValueError('Expected comment query result')
    groups = defaultdict(list)
    for i, row in enumerate(data['items']):
        # Missing identity must never merge equal text or equal empty keys.
        groups[row.get('evidence_key') or f'unresolved-row-{i}'].append(row)
    identified = all(row.get('evidence_key') for row in data['items'])
    return {
        'coverage': 'complete_query' if complete_page(data) else 'visible_page_only',
        'original_comment_count': len(groups) if identified else None,
        'observation_count': len(data['items']),
        'assessed_observation_count': sum(r.get('audit_status') == 'completed' for r in data['items']),
        'groups': [{'evidence_key': key if identified else rows[0].get('evidence_key'),
                    'observations': [{
                        'text': r['text'], 'comment_ref': r['comment_ref'],
                        'source': r['source'], 'risk_level': r['risk_level'],
                        'audit_status': r.get('audit_status'),
                        'account_ref': r.get('account_ref') or None,
                        'identity_status': 'resolved' if r.get('account_ref') else 'unknown',
                    } for r in rows]}
                   for key, rows in groups.items()],
    }


def role_facts(call):
    args = decoded(call['arguments'])
    response = decoded(call['response'])
    if call['name'] != 'query_conversation_reports' or not response.get('ok'):
        raise ValueError('Need original successful query with its filters; no inferred readback filters')
    data = response['data']
    if data['kind'] != 'accounts':
        raise ValueError('Expected account query')
    return {
        'risk_filter': args.get('risk_level') or '未设置风险过滤',
        'common_only': args.get('common_only', False),
        'account_filter': args.get('account_ref') or None,
        'parent_result_filter': args.get('from_result_ref') or data.get('parent_result_ref') or None,
        'sources': data['sources'],
        'coverage': 'complete_query' if complete_page(data) else 'visible_page_only',
        'account_count_on_page': len(data['items']),
        'post_authors_on_page': sum('post_author' in a['roles'] for a in data['items']),
        'comment_authors_on_page': sum('comment_author' in a['roles'] for a in data['items']),
        'observation_previews_complete': not any(a.get('observations_truncated', True) for a in data['items']),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acceptance-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    old_path = args.acceptance_root / 'report-query-autonomy-20261010-paired/evidence.json'
    new_path = args.acceptance_root / 'report-context-20261010-repaired/evidence.json'
    old, new = (json.loads(p.read_text()) for p in (old_path, new_path))
    def turn(data, mode, case):
        return next(t for t in data['turns'] if t.get('mode') == mode and t['case'] == case)
    def call(t, kind):
        return next(c for c in t['business_calls'] if decoded(c['response']).get('data', {}).get('kind') == kind)

    # Fixed selections are disclosed, not sold as automatic relevance resolution.
    common = turn(old, 'agent', 'compare')
    common_call = call(common, 'accounts')
    common_data = decoded(common_call['response'])['data']
    target = next(a for a in common_data['items'] if a['unique_comment_count'] == 1)
    if target['observations_truncated']:
        raise ValueError('Cannot derive complete comment facts from a truncated account preview')
    rows = [r for r in target['observations'] if r['kind'] == 'comment_author']
    if len(rows) != sum(s['comment_count'] for s in target['report_statistics']):
        raise ValueError('Incomplete account comment observations')
    entity = comment_facts({'kind': 'comments', 'items': rows, 'total_count': len(rows),
                            'has_more': False, 'next_offset': len(rows)})
    assert entity['original_comment_count'] == 1 and entity['assessed_observation_count'] == 2

    a_turn = turn(old, 'agent', 'a_reset')
    a_call = call(a_turn, 'comments')
    a_facts = comment_facts(decoded(a_call['response'])['data'])
    unknown = [r for g in a_facts['groups'] for r in g['observations'] if r['identity_status'] == 'unknown']
    assert len(unknown) == 1
    high_turn = turn(new, 'full-1', 'filtered_accounts')
    high_call = call(high_turn, 'accounts')
    roles = role_facts(high_call)
    assert roles['risk_filter'] == 'high' and roles['post_authors_on_page'] == 0
    assert roles['coverage'] == 'complete_query'
    following = turn(old, 'agent', 'followup')
    payload = {
        'status': 'OFFLINE_PRESENTATION_ONLY_NOT_ANSWER_QUALITY_ACCEPTANCE',
        'new_model_calls': 0, 'system_prompt_changes': 0, 'production_changes': False,
        'selection': 'Three manually selected historical tool calls, not live automatic scope selection.',
        'input_sha256': {str(p): sha256(p.read_bytes()).hexdigest() for p in (old_path, new_path)},
        'cases': [
            {'id': 'same_comment', 'facts': entity, 'source_call': common_call,
             'source_turn': common['result']['turn_id'], 'old_answer': following['result']['answer'],
             'review': '事实区为一条评论两次研判；历史回答说两条不同评论，矛盾仍未解决。'},
            {'id': 'unknown_identity', 'facts': unknown, 'source_call': a_call,
             'source_turn': a_turn['result']['turn_id'], 'old_answer': following['result']['answer'],
             'review': '身份未知只能表示无法确认或排除；历史回答说不是该账号所发，错误仍未解决。'},
            {'id': 'filtered_roles', 'facts': roles, 'source_call': high_call,
             'source_turn': high_turn['result']['turn_id'], 'old_answer': high_turn['result']['answer'],
             'review': '此条使用修正版中已正确回答的控制案例，不能伪称新方案纠正了一次错误。'},
        ],
        'limitations': [
            'Unknown identity reasons are not in the old response; no reason was invented.',
            'No semantic answer validation, regeneration or forced scope inheritance.',
            'Historical common-account unique_evidence_count=5 is a known tool bug and is not used.',
            'Facts and free prose can contradict; such an answer remains unacceptable.',
            'No new end-to-end conversation or user-experience acceptance was performed.',
        ],
    }
    (args.output / 'evidence.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    def table(headers, rows):
        return '<table><tr>' + ''.join('<th>'+escape(h)+'</th>' for h in headers) + '</tr>' + ''.join(
            '<tr>'+''.join('<td>'+escape(str(c))+'</td>' for c in row)+'</tr>' for row in rows)+'</table>'
    def history(i):
        case = payload['cases'][i]
        return '<p class="review">人工复核：'+escape(case['review'])+'</p><details><summary>查看原始 DeepSeek 回答（未修改）</summary><pre>'+escape(case['old_answer'])+'</pre></details>'
    def source(rows):
        return '<details><summary>证据来源和冻结版本</summary>'+table(['报告','版本','评论引用'],[
            [r['source']['label'],r['source']['report_version_id'],r['comment_ref']] for r in rows])+'</details>'
    observations = [r for g in entity['groups'] for r in g['observations']]
    markup = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>事实直出 · 三例原型</title><style>
body{margin:0;background:#f5f5f2;color:#25323a;font:16px/1.7 system-ui,-apple-system,"PingFang SC",sans-serif}main{max-width:940px;margin:40px auto;padding:0 24px 60px}h1{font-size:32px;line-height:1.3}h2{font-size:23px}article,.intro{background:white;border:1px solid #d8dedc;border-radius:12px;padding:24px;margin:24px 0}.tag{font-size:14px;color:#547363}.metric{font-size:25px;color:#174e3d;font-weight:650}.review{border-left:4px solid #bb8137;padding:10px 15px;background:#fff6e8}table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:10px;text-align:left;border-bottom:1px solid #e4e8e5;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px}summary{cursor:pointer;margin:14px 0;color:#35594c}a{color:#286351}.muted{font-size:14px;color:#697571}</style><main>
<p class="tag">本地离线原型 · 2026-10-10 · 没有新模型调用</p><h1>关键事实由程序展示，Agent 保留自由分析</h1>
<div class="intro"><p>这三个事实区来自保存的真实工具响应。数字、身份状态和来源没有交给模型重写；原来的 DeepSeek 回答保留供对照。</p><p><strong>这只能验证事实展示，尚未解决自由回答出错。</strong>正确表格旁边出现错误正文，整条回答仍然不合格。</p><p class="muted">案例由人工固定选定，不代表已经实现自动选择本题证据。没有开启思考模式、修改提示词、接入前端、改变查询流程或云端部署。</p></div>
<article><p class="tag">01 · 同一评论的多次研判</p><h2>原始内容与报告判断分开显示</h2>'''
    markup += f'<p class="metric">{entity["original_comment_count"]} 条原始评论 · {entity["assessed_observation_count"]} 次已完成研判</p>'
    markup += table(['报告','评论内容','该报告的风险'], [[r['source']['label'],r['text'],r['risk_level']] for r in observations])
    markup += '<p>两条观察记录共享工具确认的同一证据键；报告内引用不同，不等于原始评论不同。</p>'+source(observations)+history(0)+'</article>'
    markup += '<article><p class="tag">02 · 身份未知</p><h2>未知保持为未知</h2>'
    markup += table(['评论','身份状态','能够得出的结论'],[[r['text'],'未解析','无法确认，也无法排除属于目标账号'] for r in unknown])
    markup += '<p>旧工具响应没有给出身份未解析的具体原因，本原型不补造原因。</p>'+source(unknown)+history(1)+'</article>'
    markup += '<article><p class="tag">03 · 过滤条件与结果覆盖</p><h2>筛选结果中的零，不代表报告中不存在</h2>'
    markup += '<p>这次实际查询：'+escape(' + '.join(s['label'] for s in roles['sources']))+' · 风险筛选：'+escape(roles['risk_filter'])+'</p>'
    markup += table(['统计对象','当前筛选结果'],[['评论者账号',roles['comment_authors_on_page']],['发帖者账号',roles['post_authors_on_page']]])
    markup += '<p>本次已返回所选条件下的全部账号。零个发帖者只适用于 high 筛选，不能用于判断整份报告没有发帖者。</p>'+history(2)+'</article>'
    markup += '<div class="intro"><h2>这次可以确认什么</h2><p>事实区可以在零额外模型调用的情况下提供可核对的数据；它没有证明模型理解更准确，也没有解决正文与事实区矛盾。</p><p>如果继续采用这个方向，名单、计数、身份状态和来源应成为程序直接生成的主要结果。开放式分析仍由 DeepSeek 完成，查询顺序和跨报告探索继续由 Agent 自主选择。生产接入需要另验授权、证据选择、流式展示以及无新查询的连续追问。</p><p><a href="evidence.json">查看请求参数、响应、计算结果与人工复核</a></p></div></main></html>'
    (args.output / '事实直出三例原型.html').write_text(markup)
    print(args.output / '事实直出三例原型.html')


if __name__ == '__main__':
    main()
