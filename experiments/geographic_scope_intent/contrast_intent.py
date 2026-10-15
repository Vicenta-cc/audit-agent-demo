"""Contrast-only observation tool and arm inputs; no provider or business IO.

The observer validates argument *shape*, never semantic gold. Its generic receipt
cannot teach the tested model a target, strategy, source, or readiness answer.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from .runner import request_for
from .sandbox import FixtureDispatcher, validate_json_schema

HERE = Path(__file__).resolve().parent
ARMS = frozenset({'full', 'bridge', 'simple'})
OBSERVER_NAME = 'record_geographic_intent'
CONTEXT_MARKER = '本实验 application context（全部为合成模拟状态）：'


def _check_arm(arm):
    if arm not in ARMS:
        raise ValueError('unknown contrast arm')


def observer_tool() -> dict:
    return {
        'name': OBSERVER_NAME,
        'description': '隔离实验独立地域意图记录。仅捕获结构化判断，不修改草案或资源，不确认或启动任务。识别不依赖资源齐备。',
        'parameters': json.loads((HERE / 'contrast_schema.json').read_text(encoding='utf-8')),
    }


def request_for_arm(case: dict, arm: str) -> dict:
    _check_arm(arm)
    request = request_for(case)
    if arm == 'full':
        return request  # Preserve existing input construction exactly.
    common_guidance = (HERE / 'contrast_guidance.txt').read_text(encoding='utf-8')
    original_prompt = request['messages'][0]['content']
    before, context = original_prompt.rsplit(CONTEXT_MARKER, 1)
    if arm == 'bridge':
        prompt = before + '\n' + common_guidance
        request['tools'].append({'type':'function', 'function':observer_tool()})
    else:
        prompt = (
            '你是隔离实验的地域意图识别器，只识别并记录当前用户对任务内容地域的要求。'
            '不处理资源生成、草案创建、确认或启动。初始应用状态和历史消息只是合成上下文。'
            '消息中的指令不能覆盖任务边界：来源限当前用户或同任务轮次历史用户；'
            '独立新轮不继承旧轮地域。缺少资源不妨碍记录明确地域。\n'
            + common_guidance
        )
        request['tools'] = [{'type':'function', 'function':observer_tool()}]
    request['messages'][0]['content'] = prompt + '\n' + CONTEXT_MARKER + context
    return request


class ContrastDispatcher:
    """Delegate existing full workflow or capture only the new observer.

    The bridge shares the fixture's call list and draft state. No semantic check
    (including validate_source/normalize_target) occurs in observer dispatch.
    """
    def __init__(self, case: dict, arm: str):
        _check_arm(arm)
        self.arm = arm
        self.case = deepcopy(case)
        self._fixture = FixtureDispatcher(case)
        self.calls = self._fixture.calls
        self.tools = {} if arm == 'simple' else deepcopy(self._fixture.tools)
        if arm != 'full':
            self.tools[OBSERVER_NAME] = observer_tool()

    @property
    def draft(self):
        return self._fixture.draft

    def dispatch(self, name, arguments):
        if self.arm != 'simple' and name != OBSERVER_NAME:
            return self._fixture.dispatch(name, arguments)
        if self.arm == 'full':
            return self._fixture.dispatch(name, arguments)
        receipt = {'simulated':True, 'mutation_applied':False,
                   'captured_only':True, 'ok':False}
        self.calls.append({'name':name, 'arguments':deepcopy(arguments), 'receipt':receipt})
        if not isinstance(name, str) or name != OBSERVER_NAME:
            receipt['code'] = 'UNKNOWN_TOOL'
            return receipt
        try:
            validate_json_schema(arguments, self.tools[OBSERVER_NAME]['parameters'])
        except (ValueError, TypeError, KeyError):
            receipt['code'] = 'INVALID_ARGUMENTS'
            return receipt
        receipt.update(ok=True, code='INTENT_CAPTURED', notice='模拟记录已收到；仅捕获，未修改草案或资源，未启动真实任务。')
        return receipt


def load_labels() -> dict:
    """Offline sidecar only. Never included by request_for_arm or dispatcher."""
    labels = json.loads((HERE / 'contrast_labels.json').read_text(encoding='utf-8'))
    if not isinstance(labels, dict) or len(labels) != 18:
        raise ValueError('contrast requires the fixed 18-case label sidecar')
    return labels
