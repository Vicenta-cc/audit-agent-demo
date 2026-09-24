"""Task data for the dedicated author, never authorization to save or run."""
from pydantic import Field, StrictInt, field_validator, model_validator
from backend.rulesets.contracts import StrictModel
from .keyword_profiles import KeywordProfileName


class ResourceGenerationRequest(StrictModel):
    objective: str = Field(min_length=1, max_length=4000,
                           description="完整调查主题及生成目的，保留用户限定的对象和范围")
    platform: str = Field(min_length=1, max_length=100)
    requirements: str = Field(default="", max_length=8000,
                              description="保留相关用户约束，不传完整会话、保存/启动指令或工具回执")
    requested_count: StrictInt | None = Field(default=None, ge=1, le=200,
        description="仅用户明确指定的搜索词数或规则数；不是采集量。未指定留空")
    exact_terms: list[str] | None = Field(default=None, min_length=1, max_length=200,
        description="仅词库：用户给定且不得扩展的完整原文词表；无此要求留空")

    @field_validator("objective", "platform")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("generation context must not be blank")
        return value.strip()

    @field_validator("exact_terms")
    @classmethod
    def exact_terms_valid(cls, value):
        if value is not None and (any(not term.strip() for term in value)
                                  or len(value) != len(set(value))):
            raise ValueError("exact_terms must contain nonblank unique original terms")
        return value

    @model_validator(mode="after")
    def consistent_count(self):
        if (self.exact_terms is not None and self.requested_count is not None
                and len(self.exact_terms) != self.requested_count):
            raise ValueError("exact_terms conflicts with requested_count; clarify, do not trim")
        return self


class LexiconGenerationRequest(ResourceGenerationRequest):
    keyword_profile: KeywordProfileName | None = Field(
        default=None,
        description=("可选主题指导：色情服务引流用 sexual_service_leadgen；网络赌博、金融黑产或电诈助诈用 "
                     "gambling_financial_abuse。根据完整调查目标选择，不能仅凭某个词匹配；"
                     "普通招聘、电脑性能跑分等无关主题不套模板。无匹配时省略或填null，按通用要求自主生成。"
                     "不改变调查范围；exact_terms原样词表请求不加载主题示例。"),
    )
