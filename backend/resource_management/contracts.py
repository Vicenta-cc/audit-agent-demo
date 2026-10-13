from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator
from backend.rulesets.contracts import StrictModel
from .generation_contracts import LexiconGenerationRequest


class ResourceError(ValueError):
    def __init__(self, message: str, *, code: str = 'RESOURCE_INVALID', details: dict | None = None):
        super().__init__(message)
        self.code, self.details = code, details or {}


class LexiconEntry(StrictModel):
    id: str = Field(default_factory=lambda: 'entry:' + uuid4().hex, min_length=1, max_length=160)
    term: str = Field(min_length=1, max_length=500)
    kind: Literal['main', 'variant', 'tag'] = 'main'
    parent_id: str = Field(default='', max_length=160)
    enabled: StrictBool = True
    platform: str = '全平台'
    match_type: str = '黑话词'
    risk_level: str = '中'
    note: str = ''

    @field_validator('term')
    @classmethod
    def clean_term(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('term cannot be blank')
        return value.strip()


class LexiconContent(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    risk_label: str = Field(default='', max_length=200)
    description: str = Field(default='', max_length=2000, description='词库整体说明，不是词条备注或风险标签')
    entries: list[LexiconEntry] = Field(default_factory=list, max_length=2000)

    @field_validator('title', mode='before')
    @classmethod
    def clean_title(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode='after')
    def coherent_entries(self):
        by_id = {e.id: e for e in self.entries}
        if len(by_id) != len(self.entries):
            raise ValueError('entry IDs must be unique')
        seen = set()
        for e in self.entries:
            if e.kind == 'variant':
                if e.parent_id not in by_id or by_id[e.parent_id].kind != 'main':
                    raise ValueError('variant must reference a main entry ID in this lexicon')
            elif e.parent_id:
                raise ValueError('only variants may have a parent_id')
            key = (e.term, e.platform, e.match_type)
            if key in seen:
                raise ValueError('duplicate term/platform/match_type')
            seen.add(key)
        return self

    def storage_dict(self) -> dict:
        # Empty descriptions retain the canonical shape of pre-description edits
        # and revisions, so upgrading does not invalidate hashes or save receipts.
        body = self.model_dump(mode='json')
        if not self.description:
            body.pop('description')
        return body

    def search_terms(self) -> list[str]:
        # Storage/draft projection stays in authoring order for compatibility.
        return list(dict.fromkeys(entry.term for entry in self._search_entries()))

    def prioritize_search_terms(self, terms: list[str]) -> list[str]:
        """Order an already selected set; never enable or add search terms."""
        ranks = {'高': 3, '高风险': 3, 'high': 3,
                 '中': 2, '中风险': 2, 'medium': 2,
                 '低': 1, '低风险': 1, 'low': 1}
        priorities: dict[str, int] = {}
        for entry in self._search_entries():
            rank = ranks.get(entry.risk_level.strip().lower(), 0)
            # A query shared by multiple enabled entries runs only once, at
            # its highest declared priority. Disabled entries cannot promote it.
            priorities[entry.term] = max(priorities.get(entry.term, 0), rank)
        return sorted(terms, key=lambda term: -priorities.get(term, 0))

    def _search_entries(self) -> list[LexiconEntry]:
        by_parent: dict[str, list[LexiconEntry]] = {}
        for entry in self.entries:
            if entry.kind == 'variant' and entry.enabled:
                by_parent.setdefault(entry.parent_id, []).append(entry)

        entries: list[LexiconEntry] = []
        for entry in self.entries:
            if entry.kind != 'main' or not entry.enabled:
                continue
            # A main entry describes the topic. Its enabled variants are the
            # concrete platform queries. Without enabled variants (including
            # all variants disabled), use the enabled main term itself.
            candidates = by_parent.get(entry.id) or [entry]
            entries.extend(candidates)
        return entries


class ReadResourceInput(StrictModel):
    kind: Literal['ruleset', 'lexicon']
    resource_id: str = ''
    query: str = ''
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class CreateLexiconInput(StrictModel):
    content: LexiconContent | None = None
    generation_request: LexiconGenerationRequest | None = None

    @model_validator(mode='after')
    def one_input(self):
        if (self.content is None) == (self.generation_request is None):
            raise ValueError('provide exactly one of content or generation_request')
        return self


class OpenResourceInput(StrictModel):
    kind: Literal['ruleset', 'lexicon']
    resource_id: str = Field(default='', description='正式资源 id；与 session_resource_key 二选一。')
    session_resource_key: str = Field(default='', description='从 read_session_resource 返回的 open_arguments 复制，按当前会话精确冻结版本建立副本；读取本身不需要打开副本。')
    revision_id: str = Field(default='', description='规则指定已发布版本；留空读取当前资源。词库不支持此字段。')
    published_version: StrictInt | None = Field(default=None, ge=1, description='规则正式发布版本号，如 v2 传 2；后台精确解析，不回退最新版。与 revision_id 同传时必须一致。不是副本 expected_version。')

    @model_validator(mode='after')
    def one_source(self):
        if bool(self.resource_id) == bool(self.session_resource_key):
            raise ValueError('resource_id 与 session_resource_key 必须且只能提供一个')
        if self.session_resource_key and (self.revision_id or self.published_version is not None):
            raise ValueError('冻结资源引用不能再指定其他版本')
        return self


class GetEditInput(StrictModel):
    edit_id: str = Field(min_length=1)


class EditChange(StrictModel):
    operation: Literal['set_metadata', 'upsert_entry', 'remove_entry', 'update_rule', 'remove_rule', 'add_rule', 'set_exemptions', 'add_exemption', 'update_exemption', 'remove_exemption']
    target_id: str = Field(default='', description='修改/删除规则传 rule_id，修改/删除豁免传 exemption_id；新增豁免省略。')
    rule_id: str = Field(default='', description='仅豁免操作：为空修改通用豁免，非空修改该规则的豁免；target_id 为豁免 ID。')
    values: dict = Field(default_factory=dict, description='只传改动字段。update_rule 示例 {"suggested_risk_level":"high"}；add_exemption 示例 {"exemption_id":"new-id","name":"名称","condition":"原文条件"}；不是完整规则正文。')

    @model_validator(mode='after')
    def exemption_scope(self):
        if self.rule_id and self.operation not in {'add_exemption', 'update_exemption', 'remove_exemption'}:
            raise ValueError('rule_id is only for individual exemption operations')
        return self


class UpdateEditInput(GetEditInput):
    expected_version: StrictInt = Field(ge=1, description='复制最新读取/修改结果 update_arguments.expected_version，不用正式发布版本，不猜测或自动加一。')
    changes: list[EditChange] = Field(min_length=1, max_length=100, description='局部操作数组，例如 [{"operation":"update_rule","target_id":"原规则ID","values":{"suggested_risk_level":"high"}}]。合法操作用 operation；无 set_rule/update_content 操作。')


class SaveResourceInput(GetEditInput):
    expected_version: StrictInt = Field(ge=1, description='当前编辑稿的并发校验版本，不是历史版本选择器。另存历史快照，先按 read_session_resource.open_arguments 建立副本，再保存该副本当前版本。')
    mode: Literal['new', 'update', 'copy'] = 'new'
    operation_id: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, min_length=1, max_length=200, description='用户指定的正式保存名称；与保存原子提交。省略沿用稿内名称，不改变原编辑稿或已采用版本。')


class GetSaveInput(StrictModel):
    operation_id: str = Field(min_length=1, max_length=200)
