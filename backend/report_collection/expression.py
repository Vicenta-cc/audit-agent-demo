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


def express_result(original):
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
