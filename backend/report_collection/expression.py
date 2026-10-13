"""Model-facing data/brief candidate; raw results and statistics stay intact."""
from copy import deepcopy


def grouped(rows):
    groups = {}
    for i, original in enumerate(rows):
        row = deepcopy(original)
        key = row.get('evidence_key') or ('unresolved', i)
        row['identity_status'] = 'resolved' if row.get('account_ref') else 'unknown'
        row['source_result_position'] = i
        groups.setdefault(key, []).append(row)
    return [{'original_evidence_key': key if isinstance(key, str) else None,
             'identity_relation': 'same_original_evidence' if isinstance(key, str) else 'unresolved',
             'visible_observation_count': len(values),
             'report_observations': values} for key, values in groups.items()]


def known_configuration(config):
    return {key:({'status':'unknown','meaning':'冻结资料未记录；不能判定未设置、未使用或与另一报告相同。'}
                 if value is None else deepcopy(value))
            for key,value in config.items()}


def express_configuration(original):
    data=deepcopy(original)
    if 'configs' in data:data['configs']=[known_configuration(c) for c in data['configs']]
    changes=data.get('configuration_changes',data.get('items',[]) if data.get('kind')=='config_diff' else [])
    for change in changes:
        if not change.get('known',False):
            change['comparison_status']='unknown_cannot_determine_change'
            for side in ('before','after'):
                if change.get(side) is None:
                    change[side]={'status':'unknown','meaning':'未记录，不能当作未设置或相同。'}
    if data.get('kind')=='report_overview':
        for item in data.get('items',[]):item['configuration']=known_configuration(item['configuration'])
    return data


def express_result(original):
    if original.get('kind') in {'config_diff','report_comparison','report_overview'}:
        return express_configuration(original)
    if original.get('kind') not in ('accounts', 'comments') or 'items' not in original:
        return original
    data = deepcopy(original)
    filters = data.pop('query_filters', None)
    if filters is not None:
        filters['sources'] = deepcopy(data.get('sources', []))
    data['query_coverage'] = {
        'actual_filters': filters,
        'filters_known': filters is not None,
        'page_covers_complete_query': (data.get('has_more') is False and
            data.get('next_offset') == len(data['items']) == data.get('total_count')),
        'counts_apply_to': 'selected_reports_with_query_and_parent_result_filters',
    }
    if data['kind'] == 'comments':
        data['total_count_unit'] = 'report_observations'
        data['items'] = grouped(data['items'])
        data['items_unit'] = 'original_evidence_groups_on_returned_page'
    else:
        data['total_count_unit'] = 'accounts_matching_query'
        for account in data['items']:
            account['evidence_entities'] = grouped(account.pop('observations', []))
        data['query_coverage']['account_observation_previews_complete'] = all(
            a.get('observations_truncated') is False for a in data['items'])
    return data
