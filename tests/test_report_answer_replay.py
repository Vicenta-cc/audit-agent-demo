from copy import deepcopy

import pytest

from scripts.report_answer_replay import without_old_answers


def test_only_prior_prose_is_removed_not_evidence_tools_or_current_turn():
    request = dict(model='example', tools=[{'x': 1}], messages=[
        {'role': 'system', 'content': 'unchanged'},
        {'role': 'user', 'content': 'earlier'},
        {'role': 'assistant', 'content': 'plan', 'tool_calls': [{'id': 't'}]},
        {'role': 'tool', 'content': 'original evidence', 'tool_call_id': 't'},
        {'role': 'assistant', 'content': 'old final answer'},
        {'role': 'user', 'content': 'current'},
        {'role': 'assistant', 'content': 'current interim explanation'},
    ])
    original = deepcopy(request)
    projected, indices = without_old_answers(request, 'current')
    assert indices == [4]
    assert projected['messages'] == request['messages'][:4] + request['messages'][5:]
    assert projected['tools'] == request['tools'] and request == original


@pytest.mark.parametrize('messages', [[], [dict(role='user', content='x')] * 2])
def test_ambiguous_boundary_is_not_guessed(messages):
    with pytest.raises(ValueError, match='exactly one'):
        without_old_answers({'messages': messages}, 'x')
