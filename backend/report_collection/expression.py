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


def express_post_lookup(original):
    """Explain search coverage without selecting a post for the user."""
    data = deepcopy(original)
    filters = data.get('query_filters')
    if not isinstance(filters, dict):
        data['post_lookup'] = {'state':'unknown', 'meaning':'旧结果未记录查询条件，不能据此确认目标或匹配唯一性。'}
        return data
    targeted = bool(filters.get('text_contains') or filters.get('post_ref'))
    complete = data.get('coverage', {}).get('complete') is True
    count = data['total_count']
    state = ('directory' if not targeted else 'incomplete_search' if not complete else
             'no_filtered_match' if count == 0 else 'single_filtered_match' if count == 1 else
             'multiple_filtered_matches')
    meanings = {
        'directory':'这是目录读取，尚未按用户指向检索。可在相同报告范围用 text_contains 缩小查找；目录预览不能证明目标不存在或唯一。',
        'incomplete_search':'查询覆盖不完整，不能确认全范围匹配数或唯一性。',
        'no_filtered_match':'当前字面条件没有命中；不等于相关主题或用户目标不存在。可核对用词及报告范围。',
        'single_filtered_match':'当前条件完整命中一条报告帖子记录。核对标题、来源及正文是否符合用户指向后，可用其 post_ref 查询评论；简称与完整标题不逐字相同不单独构成歧义。',
        'multiple_filtered_matches':'当前条件命中多条报告帖子记录；核对候选来源和内容，必要时缩小条件或请用户选择，不能擅自取首项。',
    }
    data['post_lookup'] = {
        'state':state, 'filtered_record_count':count,
        'count_unit':'report_post_records_not_deduplicated_original_posts',
        'query_coverage_complete':complete,
        'page_covers_all_matches':(data.get('has_more') is False and
            data.get('next_offset') == len(data.get('items', [])) == count),
        'text_match_basis':'literal_substring_of_post_record_including_title_body_source_and_assessment',
        'meaning':meanings[state],
    }
    return data


def express_result(original):
    if original.get('kind') == 'report_posts':
        return express_post_lookup(original)
    if original.get('kind') in {'config_diff','report_comparison','report_overview'}:
        return express_configuration(original)
    if original.get('kind') not in ('accounts', 'comments', 'account_activity') or 'items' not in original:
        return original
    if original.get('kind') == 'account_activity' and 'query_statistics' not in original:
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
    if 'query_statistics' in data:
        # Preserve raw fields for stored results and delivery consumers. The model
        # receives one filtered summary and a separate whole-report reference.
        for key in ('unique_evidence_count', 'comment_counts'):
            data.pop(key, None)
        totals = data.pop('report_totals', [])
        data['report_context'] = {
            'basis': 'entire_frozen_reports_before_query_filters',
            'meaning': '整份报告参考统计，包含其他账号及其他风险等级；不代表本次筛选命中数量。仅作总体对照。',
            'available': bool(totals),
            'reports': totals,
        }
        for account in data['items'] if data['kind'] == 'accounts' else []:
            for key in ('unique_comment_count', 'unique_post_count', 'observation_count', 'report_statistics'):
                account.pop(key, None)
            account['roles_in_filtered_result'] = account.pop('roles', [])
    if data['kind'] == 'account_activity':
        data['total_count_unit'] = 'report_observations'
        if 'query_statistics' in data:
            data['timestamp_coverage'] = {'unknown_timestamps': data.pop('statistics', {}).get('unknown_timestamps')}
        return data
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
