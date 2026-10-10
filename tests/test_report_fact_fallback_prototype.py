from copy import deepcopy

import pytest

from scripts.report_fact_fallback_prototype import comment_facts, role_facts


def comments():
    rows = [dict(evidence_key='original-1', text='same', comment_ref=f'local-{label}',
                 account_ref='', source={'label': label}, risk_level=risk,
                 audit_status='completed') for label, risk in [('A', 'low'), ('B', 'high')]]
    return dict(kind='comments', items=rows, total_count=2, has_more=False, next_offset=2)


def test_distinguishes_original_identity_assessments_and_unknown_account():
    facts = comment_facts(comments())
    assert facts['original_comment_count'] == 1
    assert facts['observation_count'] == facts['assessed_observation_count'] == 2
    rows = facts['groups'][0]['observations']
    assert [r['risk_level'] for r in rows] == ['low', 'high']
    assert all(r['identity_status'] == 'unknown' and r['account_ref'] is None for r in rows)
    assert all('reason' not in r for r in rows)  # the response does not establish why


def test_identical_text_without_identity_is_not_merged_or_counted_as_unique():
    data = comments()
    for row in data['items']:
        row['evidence_key'] = ''
    facts = comment_facts(data)
    assert len(facts['groups']) == 2
    assert facts['original_comment_count'] is None


def test_last_page_and_pending_assessment_are_not_full_result_or_completed_judgment():
    data = comments()
    data.update(total_count=4, next_offset=4)  # last two rows, has_more=False
    data['items'][1]['audit_status'] = 'pending'
    facts = comment_facts(data)
    assert facts['coverage'] == 'visible_page_only'
    assert facts['assessed_observation_count'] == 1 and facts['observation_count'] == 2


def test_filters_and_preview_coverage_are_preserved_readback_does_not_invent_filters():
    call = dict(name='query_conversation_reports', arguments={'risk_level': 'high'}, response={
        'ok': True, 'data': dict(kind='accounts', sources=[{'label': 'B'}], total_count=1,
            next_offset=1, has_more=False, items=[dict(roles=['comment_author'], observations_truncated=True)])})
    facts = role_facts(call)
    assert facts['risk_filter'] == 'high' and facts['post_authors_on_page'] == 0
    assert facts['coverage'] == 'complete_query' and not facts['observation_previews_complete']
    read = deepcopy(call)
    read['name'] = 'read_conversation_result'
    read['arguments'] = {'result_ref': 'old'}
    with pytest.raises(ValueError, match='original'):
        role_facts(read)
