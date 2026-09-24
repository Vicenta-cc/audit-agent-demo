"""Semantic model output -> existing editor/storage contract, only for new generation."""
from pydantic import Field, field_validator

from backend.rulesets.contracts import StrictModel
from .contracts import LexiconContent

LEXICON_AUTHORING_VERSION = '2'


class GeneratedTerm(StrictModel):
    term: str = Field(min_length=1, max_length=500)
    note: str = ''
    platform: str = '全平台'
    match_type: str = '黑话词'
    risk_level: str = '中'

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
