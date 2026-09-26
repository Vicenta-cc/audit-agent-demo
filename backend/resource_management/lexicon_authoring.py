"""Semantic model output -> existing editor/storage contract, only for new generation."""
from pydantic import Field, field_validator

from backend.rulesets.contracts import StrictModel
from .contracts import LexiconContent

LEXICON_AUTHORING_VERSION = '3'


class GeneratedTerm(StrictModel):
    term: str = Field(min_length=1, max_length=500)
    note: str = Field(default='', description='简述与调查目标的关联、等级依据、正常语境噪声和待验证之处；不编造样本依据')
    platform: str = '全平台'
    match_type: str = '黑话词'
    risk_level: str = Field(default='未评估', description=(
        '逐词填写高、中、低或未评估：表示本次调查中候选表达的风险信号强弱，'
        '不是命中帖子的违规结论，也不是模型置信度。'
        '高：表达包含与本次目标直接相关的明确风险行为信号；'
        '中：有相关线索，但必须结合上下文辨别；'
        '低：主要是宽泛背景或正常语境表达，风险指向弱；'
        '未评估：缺乏依据，无法判断信号强弱。'
        '不因主题严重、身份、宗教名称或普通职业本身判高；'
        '不强制各等级都有，不继承主题等级，不以等级代替证据。'
    ))

    @field_validator('risk_level', mode='before')
    @classmethod
    def absent_risk_is_unassessed(cls, value):
        # Missing/blank ratings must not masquerade as a model assessment.
        if value is None or (isinstance(value, str) and not value.strip()):
            return '未评估'
        return value

    @field_validator('term')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('term cannot be blank')
        return value.strip()


class GeneratedTheme(GeneratedTerm):
    variants: list[GeneratedTerm] = Field(min_length=1, max_length=200,
        description='本主题下实际用于搜索的候选词；至少一项，不能只给主题名称')
    alternatives: list[GeneratedTerm] = Field(default_factory=list, max_length=200,
        description='仅用户明确要求保留停用备选时使用；不参与本次搜索，默认空')


class GeneratedLexicon(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    risk_label: str = Field(default='', max_length=200)
    description: str = Field(default='', max_length=2000)
    themes: list[GeneratedTheme] = Field(min_length=1, max_length=200,
        description='主题及其嵌套候选词；主题只用于归类，不额外参与搜索')
    tags: list[GeneratedTerm] = Field(default_factory=list, max_length=200,
        description='仅需要分类标签时提供；标签不参与搜索，默认空')

    def to_content(self) -> LexiconContent:
        entries = []
        for index, theme in enumerate(self.themes, 1):
            parent_id = f'generated-main-{index}'
            entries.append({**theme.model_dump(exclude={'variants', 'alternatives'}),
                            'id': parent_id, 'kind': 'main', 'parent_id': '', 'enabled': True})
            for group, terms, enabled in (
                ('variant', theme.variants, True), ('alternative', theme.alternatives, False),
            ):
                for number, term in enumerate(terms, 1):
                    entries.append({**term.model_dump(), 'id': f'generated-{group}-{index}-{number}',
                                    'kind': 'variant', 'parent_id': parent_id, 'enabled': enabled})
        for number, tag in enumerate(self.tags, 1):
            entries.append({**tag.model_dump(), 'id': f'generated-tag-{number}',
                            'kind': 'tag', 'parent_id': '', 'enabled': True})
        # Do not rename, deduplicate or discard invalid terms to make a result pass.
        return LexiconContent.model_validate({
            'title': self.title, 'risk_label': self.risk_label,
            'description': self.description, 'entries': entries,
        })
