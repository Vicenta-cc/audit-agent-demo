from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator, model_serializer

ApplicationStage = Literal[
    "image_evidence",
    "video_frame_evidence",
    "comment_audit",
    "fusion_audit",
]
RiskLevel = Literal["low", "medium", "high"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceMapping(StrictModel):
    source_file: str = Field(min_length=1, max_length=240)
    source_locator: str = Field(min_length=1, max_length=240)
    migrated_semantics: str = Field(min_length=1, max_length=1_000)

    @field_validator("source_file", "source_locator", "migrated_semantics", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class Exemption(StrictModel):
    enabled: StrictBool = True

    @model_serializer(mode='wrap')
    def serialize_enabled(self, handler):
        result = handler(self)
        if self.enabled:
            result.pop('enabled', None)
        return result

    exemption_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$", max_length=100)
    name: str = Field(min_length=1, max_length=160)
    condition: str = Field(min_length=1, max_length=2_000)
    source_mappings: list[SourceMapping] = Field(default_factory=list)

    @field_validator("exemption_id", "name", "condition", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class Rule(StrictModel):
    rule_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$", max_length=120)
    name: str = Field(min_length=1, max_length=200)
    hit_condition: str = Field(min_length=1, max_length=4_000)
    suggested_risk_level: RiskLevel
    rule_exemptions: list[Exemption] = Field(default_factory=list)
    application_stages: list[ApplicationStage] = Field(min_length=1)
    adjudication_notes: str = Field(min_length=1, max_length=4_000)
    enabled: StrictBool = True
    order: StrictInt = Field(ge=0, le=100_000)
    source_mappings: list[SourceMapping] = Field(default_factory=list)

    @field_validator("name", "hit_condition", "adjudication_notes", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("application_stages")
    @classmethod
    def unique_stages(cls, value: list[ApplicationStage]) -> list[ApplicationStage]:
        if len(value) != len(set(value)):
            raise ValueError("application_stages must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_exemption_uniqueness(self) -> Rule:
        exemption_ids = [item.exemption_id for item in self.rule_exemptions]
        if len(exemption_ids) != len(set(exemption_ids)):
            raise ValueError("rule exemption ids must be unique within a rule")
        return self


class RuleCategory(StrictModel):
    category_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$", max_length=100)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1_000)
    order: StrictInt = Field(ge=0, le=100_000)
    rules: list[Rule] = Field(min_length=1)

    @field_validator("name", "description", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class RuleSetContent(StrictModel):
    schema_version: Literal[0] = 0
    name: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=1, max_length=100)
    audit_goal: str = Field(min_length=1, max_length=2_000)
    general_exemptions: list[Exemption] = Field(default_factory=list)
    categories: list[RuleCategory] = Field(min_length=1)

    @field_validator("name", "domain", "audit_goal", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_identity_uniqueness(self) -> RuleSetContent:
        category_ids = [category.category_id for category in self.categories]
        if len(category_ids) != len(set(category_ids)):
            raise ValueError("category_id must be unique within a RuleSet")
        rule_ids = [rule.rule_id for category in self.categories for rule in category.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("rule_id must be unique within a RuleSet")
        exemption_ids = [item.exemption_id for item in self.general_exemptions]
        if len(exemption_ids) != len(set(exemption_ids)):
            raise ValueError("general exemption ids must be unique")
        return self
