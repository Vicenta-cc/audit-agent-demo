from types import SimpleNamespace
import pytest
from backend.hermes_runtime.adapter import turn_system_context


def test_each_turn_gets_new_facts_without_changing_stable_cache_or_history():
    agent=SimpleNamespace(ephemeral_system_prompt='existing constraint',_cached_system_prompt='stable rules')
    history=[{'role':'user','content':'旧问题'}]
    for count in (0,100,200):
        with turn_system_context(agent,f'confirmed={count}'):
            assert agent.ephemeral_system_prompt==f'existing constraint\n\nconfirmed={count}'
            assert agent._cached_system_prompt=='stable rules'
        assert agent.ephemeral_system_prompt=='existing constraint'
    assert history==[{'role':'user','content':'旧问题'}]


def test_exception_restores_context_and_does_not_affect_other_session():
    first=SimpleNamespace(ephemeral_system_prompt='')
    second=SimpleNamespace(ephemeral_system_prompt='second')
    with pytest.raises(RuntimeError),turn_system_context(first,'private first facts'):
        assert second.ephemeral_system_prompt=='second'
        raise RuntimeError('provider failed')
    assert first.ephemeral_system_prompt==''
    with turn_system_context(first,'next turn'):
        assert 'private first facts' not in first.ephemeral_system_prompt
