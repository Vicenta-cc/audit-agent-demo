"""Exact set operations over authorized frozen observations, without attribution."""
from collections import Counter
from copy import deepcopy


def comment_counts(rows):
    return {'original_comment_count': len({r['evidence_key'] for r in rows}),
            'report_observation_count': len(rows),
            'completed_assessment_count': sum(r['audit_status']=='completed' for r in rows)}


def account_presence(accounts, sources):
    groups = {}
    for account in accounts:
        refs = {r['report_ref'] for r in account['report_statistics'] if r['comment_count'] or r['post_count']}
        selected = [s for s in sources if s['ref'] in refs]
        presence = {'report_refs':[s['ref'] for s in selected], 'labels':[s['label'] for s in selected],
                    'report_count':len(selected)}
        account['report_presence'] = presence
        key = tuple(presence['report_refs'])
        group = groups.setdefault(key, {**presence, 'account_count':0})
        group['account_count'] += 1
    return {'groups': sorted(groups.values(), key=lambda g: (-g['report_count'], g['labels'])),
            'matching_account_count':len(accounts), 'selected_report_count':len(sources),
            'coverage':'all_accounts_matching_query_before_pagination',
            'identity_basis':'stable_platform_account_id; unresolved identities excluded'}


def compare_samples(rows, sources):
    first, second = sources
    per_report = []
    by_key = {}
    for source in sources:
        report_rows = [r for r in rows if r['source']['report_ref']==source['report_ref']]
        comments = [r for r in report_rows if r['kind']=='comment_author']
        completed = [r for r in comments if r['audit_status']=='completed']
        risks = dict(Counter(r['risk_level'] for r in completed))
        per_report.append({'source':source,
            'original_post_count':len({r['evidence_key'] for r in report_rows if r['kind']=='post_author'}),
            **comment_counts(comments), 'completed_comment_risk_counts':risks,
            'high_comment_count':risks.get('high',0),
            'high_fraction_among_completed_comments':risks.get('high',0)/len(completed) if completed else None,
            'uncompleted_comment_count':len(comments)-len(completed)})
        for row in comments:
            item = by_key.setdefault(row['evidence_key'], {'original_evidence_key':row['evidence_key'],
                                                         'first_observations':[], 'second_observations':[]})
            item['first_observations' if source==first else 'second_observations'].append(deepcopy(row))
    totals = Counter()
    transitions = Counter()
    items = list(by_key.values())
    for item in items:
        a, b = item['first_observations'], item['second_observations']
        cohort = 'both' if a and b else 'only_first' if a else 'only_second'
        item['membership'] = cohort
        totals[cohort] += 1
        item['text_changed'] = (sorted(r['text'] for r in a)!=sorted(r['text'] for r in b)) if a and b else None
        transition = None
        if len(a)==len(b)==1 and a[0]['audit_status']==b[0]['audit_status']=='completed':
            transition = {'first_risk':a[0]['risk_level'], 'second_risk':b[0]['risk_level']}
            transitions[(transition['first_risk'],transition['second_risk'])] += 1
        item['risk_transition'] = transition
    return items, {'comparison_order':{'first':first,'second':second}, 'report_statistics':per_report,
        'sample_membership_counts':{k:totals[k] for k in ('both','only_first','only_second')},
        'risk_transition_counts':[{'first_risk':a,'second_risk':b,'original_comment_count':n}
                                  for (a,b),n in sorted(transitions.items())],
        'total_count_unit':'original_comments_in_union',
        'coverage':'all_frozen_comments_in_selected_reports; no risk filter; summaries precede pagination',
        'causal_evidence':{'attribution':'not_established_by_comparison',
                           'rule_hit_trace':'not_exposed_by_this_tool', 'controlled_reassessment':'not_performed'},
        'limitations':'仅比较冻结报告中的收录与判断；未收录不代表现实中消失，风险比例不代表总体发生率。同一身份的文本也可能改变。'}


def query_statistics(rows, sources, *, include_comments=True, include_posts=True):
    """Count complete filtered evidence; never derive totals from a preview."""
    def counts(part, kind):
        # Unknown identities are separate observations, not one shared original.
        keys = {row['evidence_key'] for row in part if row.get('evidence_key')}
        originals = len(keys) + sum(not row.get('evidence_key') for row in part)
        result = {'report_observations': len(part),
                  'original_comments' if kind == 'comment_author' else 'original_posts': originals}
        if kind == 'comment_author':
            result['completed_assessments'] = sum(row['audit_status'] == 'completed' for row in part)
        return result

    def duplication(part):
        # Explain the exact count difference using the same identity as totals.
        # Never infer duplicate identity from matching text or a paged preview.
        identities = {}
        for row in part:
            if row.get('evidence_key'):
                identities.setdefault(row['evidence_key'], []).append(row)
        repeated = [(key, values) for key, values in identities.items() if len(values) > 1]
        groups = []
        for key, values in repeated[:20]:
            per_report = Counter(row['source']['report_ref'] for row in values)
            groups.append({'original_evidence_key': key,
                'report_observations': len(values), 'extra_observations': len(values) - 1,
                'by_report': [{'source': {'report_ref': s['ref'], 'label': s['label'],
                                          'report_version_id': s['version']},
                               'report_observations': per_report[s['ref']]}
                              for s in sources if per_report[s['ref']]]})
        return {'repeated_original_count': len(repeated),
                'extra_observations': sum(len(values) - 1 for _, values in repeated),
                'unresolved_identity_observations': sum(not r.get('evidence_key') for r in part),
                'groups': groups, 'groups_complete': len(repeated) <= 20,
                'omitted_group_count': max(0, len(repeated) - 20)}

    result = {'basis': 'complete_filtered_result_before_pagination',
              'counting_rule': 'report_observations 为报告收录次数；original_comments/original_posts 按原始证据身份去重，不能按相同文本合并。跨报告去重合计不能由各报告去重数相加。'}
    for dimension, kind, included in [('comments', 'comment_author', include_comments),
                                       ('posts', 'post_author', include_posts)]:
        if not included:
            result[dimension] = {'status': 'not_included_in_this_query',
                                 'meaning': '本次查询未统计此类内容，不能据此断言不存在；需要查询相应内容才能判断。'}
            continue
        part = [row for row in rows if row['kind'] == kind]
        result[dimension] = {'status': 'counted', **counts(part, kind),
            'duplication': duplication(part),
            'by_report': [{'source': {'report_ref': source['ref'], 'label': source['label'],
                                      'report_version_id': source['version']},
                           **counts([row for row in part if row['source']['report_ref'] == source['ref']], kind)}
                          for source in sources]}
    return result
