"""Semantic output for new rules; existing imports and edits retain storage IDs."""
from copy import deepcopy

from pydantic import Field, StrictBool, field_validator

from backend.rulesets.contracts import (
    ApplicationStage, Exemption, RiskLevel, Rule, RuleCategory, RuleSetContent,
    SourceMapping, StrictModel,
)

RULESET_AUTHORING_VERSION = '1'


class AuthoringModel(StrictModel):
    @field_validator('*', mode='before')
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class GeneratedExemption(AuthoringModel):
    name: str = deepcopy(Exemption.model_fields['name'])
    condition: str = deepcopy(Exemption.model_fields['condition'])
    enabled: StrictBool = True
    source_mappings: list[SourceMapping] = Field(default_factory=list)


class GeneratedRule(AuthoringModel):
    name: str = deepcopy(Rule.model_fields['name'])
    hit_condition: str = deepcopy(Rule.model_fields['hit_condition'])
    suggested_risk_level: RiskLevel
    rule_exemptions: list[GeneratedExemption] = Field(default_factory=list)
    application_stages: list[ApplicationStage] = deepcopy(Rule.model_fields['application_stages'])
    adjudication_notes: str = deepcopy(Rule.model_fields['adjudication_notes'])
    enabled: StrictBool = True
    source_mappings: list[SourceMapping] = Field(default_factory=list)


class GeneratedCategory(AuthoringModel):
    name: str = deepcopy(RuleCategory.model_fields['name'])
    description: str = deepcopy(RuleCategory.model_fields['description'])
    rules: list[GeneratedRule] = Field(min_length=1)


class GeneratedRuleSet(AuthoringModel):
    name: str = deepcopy(RuleSetContent.model_fields['name'])
    domain: str = deepcopy(RuleSetContent.model_fields['domain'])
    audit_goal: str = deepcopy(RuleSetContent.model_fields['audit_goal'])
    general_exemptions: list[GeneratedExemption] = Field(default_factory=list)
    categories: list[GeneratedCategory] = Field(min_length=1)

    @classmethod
    def from_content(cls, content: RuleSetContent) -> 'GeneratedRuleSet':
        """Project already validated legacy output into new-generation semantics."""
        body = content.model_dump(mode='json')
        body.pop('schema_version')
        for exemption in body['general_exemptions']:
            exemption.pop('exemption_id')
        for category in body['categories']:
            category.pop('category_id')
            category.pop('order')
            for rule in category['rules']:
                rule.pop('rule_id')
                rule.pop('order')
                for exemption in rule['rule_exemptions']:
                    exemption.pop('exemption_id')
        return cls.model_validate(body)

    def to_content(self) -> RuleSetContent:
        body = self.model_dump(mode='json')
        for index, exemption in enumerate(body['general_exemptions']):
            exemption['exemption_id'] = f'generated-general-exemption-{index + 1}'
        for category_index, category in enumerate(body['categories']):
            category['category_id'] = f'generated-category-{category_index + 1}'
            category['order'] = category_index
            for rule_index, rule in enumerate(category['rules']):
                rule['rule_id'] = f'generated-rule-{category_index + 1}-{rule_index + 1}'
                rule['order'] = rule_index
                for index, exemption in enumerate(rule['rule_exemptions']):
                    exemption['exemption_id'] = f"{rule['rule_id']}-exemption-{index + 1}"
        # Keep all existing business validation, including stage uniqueness.
        return RuleSetContent.model_validate(body)
