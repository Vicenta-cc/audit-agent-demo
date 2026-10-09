"""Interactive provider overrides must not redirect audit clients."""
import json
import os
import subprocess
import sys
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.audit_agent.config import settings
from backend.hermes_runtime.service import HermesInvestigationAgentService
from backend.investigation_creation.conversation import InvestigationCreationConversationService


@pytest.mark.parametrize('cls', [HermesInvestigationAgentService, InvestigationCreationConversationService])
@pytest.mark.parametrize('model', ['qwen-test', 'deepseek-v4-1-flash-260910'])
def test_interactive_entrypoints_use_dedicated_provider_without_changing_audit(monkeypatch, cls, model):
    from backend.audit_agent.qwen_client import QwenClient
    monkeypatch.setattr(settings, 'conversation_api_key', 'test-chat-key')
    monkeypatch.setattr(settings, 'conversation_base_url', 'https://chat.example/v1')
    monkeypatch.setattr(settings, 'conversation_model', model)
    monkeypatch.setattr(settings, 'dashscope_api_key', 'test-audit-key')
    monkeypatch.setattr(settings, 'dashscope_base_url', 'https://audit.example/v1')
    service = object.__new__(cls)
    service._agents = {}
    service._agent_lock = RLock()
    service._answer_streamer = SimpleNamespace(agent_callbacks=lambda _: {})
    service._activity_emitter = SimpleNamespace(agent_callbacks=lambda _: {})
    service.runtime_binding = SimpleNamespace(create_agent=Mock(return_value=object()))
    service.agent_factory = None
    service.fake_runtime = False
    service._product_mode = lambda _: 'unified-report'
    service._agent('test-session')
    options = service.runtime_binding.create_agent.call_args.kwargs
    assert (options['base_url'], options['api_key'], options['model']) == (
        'https://chat.example/v1', 'test-chat-key', model)
    if model.startswith('deepseek'):
        assert options['request_overrides'] == {'extra_body': {'thinking': {'type': 'disabled'}}}
    else:
        assert 'request_overrides' not in options
    audit = QwenClient()
    assert (audit.base_url, audit.api_key) == ('https://audit.example/v1', 'test-audit-key')


def test_unconfigured_conversation_provider_keeps_existing_deployment_defaults():
    env = {k: v for k, v in os.environ.items() if not k.startswith('CONVERSATION_')}
    env.update(PYTHON_DOTENV_DISABLED='1', DASHSCOPE_API_KEY='old-key',
               DASHSCOPE_BASE_URL='https://old.example/v1', QWEN_TEXT_MODEL='old-qwen')
    result = subprocess.check_output([sys.executable, '-c',
        'from backend.audit_agent.config import settings as s; import json; '
        'print(json.dumps([s.conversation_api_key,s.conversation_base_url,s.conversation_model]))'],
        cwd=Path(__file__).resolve().parents[1], env=env, text=True)
    assert json.loads(result) == ['old-key', 'https://old.example/v1', 'old-qwen']
