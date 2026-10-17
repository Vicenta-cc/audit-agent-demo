"""Offline name resolution and verified user provenance for draft geography.

This module validates facts and input shape; the conversation agent owns intent.
No inferred geography, suffix stripping, or natural-language intent rules live here.
Province/city compounds use complete catalog names/aliases and parent identities.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator
from . import geographic_catalog

from .errors import ConfigurationValidationError


class ScopeModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)


SelectionPolicy = Literal['local_only', 'local_first_with_supplement']


class RelevanceScopeChange(ScopeModel):
    action: Literal['set', 'clear']
    source_quote: StrictStr = Field(min_length=1, max_length=4000)
    target_text: StrictStr | None = Field(default=None, min_length=1, max_length=300)
    selection_policy: SelectionPolicy | None = None

    @field_validator('source_quote', 'target_text', mode='before')
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode='after')
    def combination(self):
        if self.action == 'set' and (not self.target_text or not self.selection_policy):
            raise ValueError('set requires target_text and selection_policy')
        if self.action == 'clear' and self.model_fields_set & {'target_text', 'selection_policy'}:
            raise ValueError('clear accepts only action and source_quote')
        return self


class RegionName(ScopeModel):
    name: StrictStr = Field(min_length=1)
    aliases: list[StrictStr] = Field(default_factory=list)


class RelevanceScope(ScopeModel):
    schema_version: Literal['geo-scope-v1'] = 'geo-scope-v1'
    target: RegionName
    province: RegionName
    topic: None = None
    selection_policy: SelectionPolicy
    content_unit: Literal['parent_post_event'] = 'parent_post_event'


class RegionCandidate(ScopeModel):
    name: StrictStr
    level: Literal['province', 'prefecture', 'county']
    kind: StrictStr
    province_name: StrictStr


class PublicRelevanceScopeState(ScopeModel):
    status: Literal['resolved', 'pending', 'cleared']
    target_text: StrictStr | None = None
    selection_policy: SelectionPolicy | None = None
    reason: StrictStr = ''
    candidates: list[RegionCandidate] = Field(default_factory=list)
    target: RegionName | None = None
    province: RegionName | None = None


class RelevanceScopeState(PublicRelevanceScopeState):
    target_level: Literal['province', 'prefecture', 'county'] | None = None
    target_code: StrictStr = ''
    province_code: StrictStr = ''
    catalog_version: StrictStr = ''
    principal: StrictStr
    session_id: StrictStr
    source_kind: Literal['conversation', 'ui_selection'] = 'conversation'
    source_operation_id: StrictStr = ''
    application_turn_id: StrictStr = ''
    source_turn_id: StrictStr = ''
    source_user_message_id: StrictStr = ''
    source_sequence: StrictInt = Field(default=0, ge=0)
    source_quote: StrictStr = ''
    task_anchor_turn_id: StrictStr = ''
    draft_id: StrictStr = ''
    copied_from_draft_id: StrictStr = ''

    @model_validator(mode='after')
    def source_identity(self):
        if self.source_kind == 'conversation':
            if not all((self.application_turn_id, self.source_turn_id,
                        self.source_user_message_id, self.source_quote,
                        self.task_anchor_turn_id, self.source_sequence)):
                raise ValueError('conversation geography requires a verified user turn')
            if self.source_operation_id:
                raise ValueError('conversation geography cannot claim a UI selection')
        elif not self.source_operation_id or any((self.application_turn_id,
                self.source_turn_id, self.source_user_message_id, self.source_quote)):
            raise ValueError('UI geography requires a selection, not a synthetic turn')
        return self


def project_state(state: RelevanceScopeState | None) -> PublicRelevanceScopeState | None:
    if state is None:
        return None
    return PublicRelevanceScopeState.model_validate({
        key: getattr(state, key) for key in PublicRelevanceScopeState.model_fields
    })


CATALOG_PATH = Path(__file__).with_name('data') / 'geographic_catalog.json'


def _reject(code, message):
    raise ConfigurationValidationError(message, code=code, details={'mutation_applied': False})


def _catalog():
    try:
        catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
        geographic_catalog.validate_packaged_catalog(catalog)
        indexed = {record['code']: record for record in catalog['records']}
        return catalog, indexed
    except (OSError, ValueError, KeyError, TypeError):
        _reject('REGION_CATALOG_UNAVAILABLE', '本地行政区名录缺失或未通过校验，暂时无法配置地域。')


def resolve_region(target_text: str) -> dict:
    """Resolve an exact name/alias, optionally qualified by its catalog province."""
    catalog, indexed = _catalog()
    text = target_text.strip()
    named = [r for r in indexed.values() if text == r['name'] or text in r['aliases']]
    parent_conflict = False
    if not named:
        # Match complete tokens, not substrings or guessed suffixes. Consider all
        # possible splits so alias collisions cannot become a first-match win.
        names = {}
        for record in indexed.values():
            for name in (record['name'], *record['aliases']):
                names.setdefault(name, []).append(record)
        qualified = {}
        for province in indexed.values():
            if province['level'] != 'province':
                continue
            for prefix in (province['name'], *province['aliases']):
                if not text.startswith(prefix):
                    continue
                for child in names.get(text[len(prefix):], []):
                    if child['level'] == 'province':
                        continue
                    if child['province_code'] == province['code']:
                        qualified[child['code']] = child
                    else:
                        parent_conflict = True
        named = list(qualified.values())
    # Only supported province/city identities participate in ambiguity. County
    # aliases do not shadow cities, and unsupported names never fall back upward.
    matches = [r for r in named if r['level'] == 'province'
        or (r['level'] == 'prefecture' and r['kind'] in {'地级市', 'prefecture_city'})]
    candidates = [RegionCandidate(name=r['name'], level=r['level'], kind=r['kind'],
        province_name=indexed[r['province_code']]['name']) for r in matches]
    base = {'catalog_version': catalog['version'], 'candidates': candidates}
    if len(matches) != 1:
        if matches:
            reason = '地域名称存在歧义，请明确选择省级地域或地级市，并提供完整名称。'
        elif named:
            reason = '当前支持省级地域和地级市；区县、街道及其他地级类型暂待解析，请明确调查范围。'
        elif parent_conflict:
            reason = '省级地域与下级地名的行政归属不一致，请核对调查范围。'
        else:
            reason = '未找到该地域，请核对名称或补充完整名称；当前支持单一省级地域或地级市。'
        return {**base, 'status': 'pending', 'reason': reason}
    record = matches[0]
    province = indexed[record['province_code']]
    return {**base, 'status': 'resolved', 'reason': '',
        'target': RegionName(name=record['name'], aliases=record['aliases']),
        'province': RegionName(name=province['name'], aliases=province['aliases']),
        'target_level': record['level'], 'target_code': record['code'], 'province_code': province['code']}


def _source(change, *, application, principal, session_id, application_turn_id, draft_id):
    conversation = application.conversation_store
    if conversation is None or not application_turn_id:
        _reject('REGION_SOURCE_INVALID', '地域配置需要当前会话的真实用户回合。')
    owner = principal.id
    with application.store._connect() as db:
        db.execute('ATTACH DATABASE ? AS conversation', (str(conversation.db_path),))
        current = db.execute('''SELECT t.*, m.sequence, m.content FROM conversation.investigation_turns t
            JOIN conversation.investigation_messages m ON m.id=t.user_message_id
            JOIN conversation.investigation_sessions s ON s.id=t.session_id
            WHERE t.id=? AND t.session_id=? AND t.status='running'
            AND s.owner_principal=? AND s.scope_type='creation' AND s.status='active'
            AND m.session_id=t.session_id AND m.turn_id=t.id AND m.role='user' ''',
            (application_turn_id, session_id, owner)).fetchone()
        if current is None:
            _reject('REGION_SOURCE_INVALID', '当前用户、会话或 application turn 不匹配。')
        from .rounds import draft_ids
        members = draft_ids(db, session_id, owner)
        if draft_id and (draft_id not in members or draft_id != members[-1]):
            _reject('REGION_SOURCE_INVALID', '目标草案不属于当前任务轮次。')
        # A committed confirmation closes a task even when later user turns read
        # its old report. Derive the boundary from stored facts, never model text.
        boundary = ''
        if members:
            placeholders = ','.join('?' for _ in members)
            row = db.execute(f'''SELECT MAX(confirmed_at) FROM investigation_drafts
                WHERE id IN ({placeholders}) AND id<>? AND confirmed_at<>'' ''',
                (*members, draft_id)).fetchone()
            boundary = row[0] or ''
        messages = db.execute('''SELECT m.*, t.id AS source_turn FROM conversation.investigation_messages m
            JOIN conversation.investigation_turns t ON t.user_message_id=m.id
            WHERE m.session_id=? AND t.session_id=m.session_id AND m.turn_id=t.id
            AND m.role='user' AND m.sequence<=? AND m.created_at>?
            ORDER BY m.sequence''', (session_id, current['sequence'], boundary)).fetchall()
        # Current explicit wording wins over repeats in earlier preparation turns.
        matching = [m for m in messages if change.source_quote in m['content']]
        now = [m for m in matching if m['source_turn'] == application_turn_id]
        matching = now or matching
        if len(matching) != 1:
            _reject('REGION_SOURCE_AMBIGUOUS' if matching else 'REGION_SOURCE_INVALID',
                '用户原话无法在本任务中唯一定位，请引用当前用户的明确要求。')
        if draft_id:
            floor = db.execute('''SELECT COALESCE(MAX(conversation_sequence), 0)
                FROM investigation_region_selections
                WHERE draft_id=? AND session_id=? AND principal=?''',
                (draft_id, session_id, owner)).fetchone()[0]
            if floor and matching[0]['sequence'] <= floor:
                _reject('REGION_SOURCE_SUPERSEDED',
                    '该地域要求早于用户最新界面选择，不能覆盖当前地域；请沿用当前配置。')
        return dict(principal=owner, session_id=session_id, application_turn_id=application_turn_id,
            source_turn_id=matching[0]['source_turn'], source_user_message_id=matching[0]['id'],
            source_sequence=matching[0]['sequence'], source_quote=change.source_quote,
            task_anchor_turn_id=messages[0]['source_turn'], draft_id=draft_id)


def prepare_change(change: RelevanceScopeChange, *, application, principal, session_id: str,
                   application_turn_id: str, draft_id: str = '') -> tuple[RelevanceScope | None, RelevanceScopeState]:
    provenance = _source(change, application=application, principal=principal, session_id=session_id,
        application_turn_id=application_turn_id, draft_id=draft_id)
    if change.action == 'clear':
        return None, RelevanceScopeState(status='cleared', **provenance)
    resolved = resolve_region(change.target_text)
    state = RelevanceScopeState(**resolved, **provenance, target_text=change.target_text,
        selection_policy=change.selection_policy)
    scope = (RelevanceScope(target=state.target, province=state.province,
        selection_policy=change.selection_policy) if state.status == 'resolved' else None)
    return scope, state


def scope_blockers(configuration, *, capability=None) -> list[dict]:
    state = getattr(configuration, 'relevance_scope_state', None)
    scope = getattr(configuration, 'relevance_scope', None)
    if state is not None and state.status == 'pending':
        return [{'code': 'RELEVANCE_SCOPE_PENDING', 'message': '调查地域待澄清，请核对名称或提供完整省级地域、地级市或直辖市名称。'}]
    if scope is None and (state is None or state.status != 'resolved'):
        return []
    available = False
    if capability is not None:
        from .geographic_runtime import IsolatedGeographicCapability
        from backend.audit_agent.config import settings
        available = type(capability) is IsolatedGeographicCapability and settings.triage_mode == 'select'
    blockers = [] if available else [{'code': 'RELEVANCE_SCOPE_EXECUTION_UNAVAILABLE',
                 'message': '地域执行能力尚未接通，当前不能启动。'}]
    if configuration.platform != 'dy' or configuration.investigation.mode != 'search':
        blockers.append({'code': 'RELEVANCE_SCOPE_MODE_UNSUPPORTED',
                         'message': '当前平台或调查模式暂不支持地域调查，当前不能启动。'})
    return blockers


def ensure_execution_ready(configuration, *, capability=None) -> None:
    blockers = scope_blockers(configuration, capability=capability)
    if blockers:
        _reject(blockers[0]['code'], blockers[0]['message'])


def bind_draft_state(configuration, draft_id: str, principal: str,
                     copied_from_draft_id: str = ''):
    state = getattr(configuration, 'relevance_scope_state', None)
    if state is None:
        return configuration
    if state.principal != principal or (state.draft_id not in ('', draft_id)
            and state.draft_id != copied_from_draft_id):
        _reject('REGION_SOURCE_INVALID', '地域来源与草案身份不匹配。')
    changes = {'draft_id': draft_id}
    if copied_from_draft_id:
        changes['copied_from_draft_id'] = copied_from_draft_id
    return configuration.model_copy(update={'relevance_scope_state': state.model_copy(update=changes)})
