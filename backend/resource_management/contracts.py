from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator
from backend.rulesets.contracts import StrictModel


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
        by_parent: dict[str, list[LexiconEntry]] = {}
        for entry in self.entries:
            if entry.kind == 'variant' and entry.enabled:
                by_parent.setdefault(entry.parent_id, []).append(entry)

        terms: list[str] = []
        seen: set[str] = set()
        for entry in self.entries:
            if entry.kind != 'main' or not entry.enabled:
                continue
            # A main entry describes the topic. Its enabled variants are the
            # concrete platform queries; legacy topics without variants keep
            # their main term as a compatibility fallback.
            candidates = by_parent.get(entry.id) or [entry]
            for candidate in candidates:
                if candidate.term not in seen:
                    seen.add(candidate.term)
                    terms.append(candidate.term)
        return terms


class ReadResourceInput(StrictModel):
    kind: Literal['ruleset', 'lexicon']
    resource_id: str = ''
    query: str = ''
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class CreateLexiconInput(StrictModel):
    content: LexiconContent


class OpenResourceInput(StrictModel):
    kind: Literal['ruleset', 'lexicon']
    resource_id: str = Field(min_length=1)


class GetEditInput(StrictModel):
    edit_id: str = Field(min_length=1)


class EditChange(StrictModel):
    operation: Literal['set_metadata', 'upsert_entry', 'remove_entry', 'update_rule', 'remove_rule', 'add_rule', 'set_exemptions']
    target_id: str = ''
    values: dict = Field(default_factory=dict)


class UpdateEditInput(GetEditInput):
    expected_version: StrictInt = Field(ge=1)
    changes: list[EditChange] = Field(min_length=1, max_length=100)


class SaveResourceInput(GetEditInput):
    expected_version: StrictInt = Field(ge=1)
    mode: Literal['new', 'update', 'copy'] = 'new'
    operation_id: str = Field(min_length=1, max_length=200)


class GetSaveInput(StrictModel):
    operation_id: str = Field(min_length=1, max_length=200)
