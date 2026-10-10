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
