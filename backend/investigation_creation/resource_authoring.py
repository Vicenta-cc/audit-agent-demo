from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

import requests
from pydantic import BaseModel, ValidationError

from backend.audit_agent.config import settings
from backend.resource_management.contracts import LexiconContent
from backend.rulesets.contracts import RuleSetContent


ResourceKind = Literal["ruleset", "lexicon"]


RULESET_AUTHORING_INSTRUCTIONS = """
你是内容安全审核规则的结构化编写器。根据用户当前请求生成一份临时审核规则候选内容。
只返回符合给定 JSON Schema 的对象，不输出解释、Markdown 或工具调用文本。

要求：
- 规则仅是当前会话候选，不代表正式发布、采用或启动调查。
- hit_condition 与 adjudication_notes 的必要条件必须一致；独立条件用“任一”明确表达。
- 用于最终结论的规则必须显式包含 fusion_audit；按证据实际承载方式选择其他阶段。
- general_exemptions 和 rule_exemptions 是强豁免，命中后必须消除对应风险。
- 认证、蓝V、官方账号、实名或机构声誉本身不能作为豁免。
- 不把语言、民族、地域、职业或其他身份本身视为风险；正常讨论和具体行为批评应被保护。
- categories 和 rules 只描述可命中的风险类型；纯粹的正常内容保护、禁止推断原则或不命中边界，
  应写入 general_exemptions、rule_exemptions 或 adjudication_notes，不能伪装成独立风险规则。
- category_id、rule_id、exemption_id 使用稳定、简洁的小写英文数字标识。
- 没有真实来源文件时 source_mappings 使用空数组，不能编造来源。
""".strip()


LEXICON_AUTHORING_INSTRUCTIONS = """
你是公开平台内容安全调查的结构化检索词库编写器。根据用户当前请求生成一份临时黑话库内容。
只返回符合给定 JSON Schema 的对象，不输出解释、Markdown 或工具调用文本。

要求：
- 内容仅用于当前会话编辑副本，不代表正式保存、发布或启动调查。
- 主词 main 是供用户理解和归类的稳定主题或发现意图标签，不是为了伪装而写的搜索候选；
  它应当语义清楚、可长期复用。实际平台搜索表达放在 variant，variant 的 parent_id 必须指向
  同一词库中语义最匹配的主词 id。
- 实际检索优先使用启用变体，因此每个保留的主题主词必须 enabled=true，并至少有一个启用变体。
- 先根据用户目标在内部判断发现模式：隐蔽交易、圈层暗语、公开议题讨论或明确对象查询。
  隐蔽交易和圈层暗语可以优先寻找隐语；公开议题或明确对象查询不要强迫生成黑话。
- 对需要发现隐蔽表达的任务，先在内部把候选区分为 coded_expression 和 behavioral_expression，
  但不要在最终 JSON 中增加这两个字段。只有 coded_expression 可以作为启用变体；直接描述行为、
  交易条件、服务方式或联系方式的 behavioral_expression 即使与目标高度相关，也不能冒充黑话。
- coded_expression 的核心不是对目标描述得多准确，而是字面意义与隐藏意义有多不一致。它必须具有
  较高语义距离和词面不透明度：目标圈层可结合语境识别特殊含义，普通用户脱离上下文后不能轻易
  从词面推断真实指向。“委婉但仍能直接理解目标行为”的表达不属于高质量 coded_expression。
- 可以优先考虑正常词借代、对象借代、人物或角色代称、物品借代、数字代码、字母缩写、谐音、
  错写、拆写和圈层内部简称等机制，但必须确认其具有真实、稳定的圈层用法。品牌、人物、物品或
  代码尤其容易被编造；没有可靠把握时必须舍弃，不能因为形式隐晦就臆造含义。
- “服务、上门、可约、包夜、私聊、定位、看图、接单、价格”等直接描述行为、交易或联系方式的
  表达，以及跨行业通用引流动作，均属于 behavioral_expression。除非它与一个可靠的高隐蔽核心
  表达组成真实、自然且可独立搜索的固定说法，否则不能作为启用变体。
- 对隐蔽型任务，先在内部构造更大的候选池并逐项反向排除：若候选可被大量正常行业自然使用、
  只是主题的直白或委婉近义表达、只是通用引流动作，或无法确认真实圈层用法，就不能进入最终结果。
  只有同时满足圈层辨识度、字面隐蔽性、独立可搜索性和较低正常噪声的候选才可启用。
- 生成主词时先按用户的调查目标划分少量语义主题，再为每个主题选择真实可搜索的变体；
  不要把某个变体换一种说法后同时充当主词，也不要为凑层级创建含义重叠的主词。
- 默认生成 1 至 3 个主题主词，并且整个词库最多生成 5 个启用变体词；变体必须按独立搜索价值
  从高到低排列。质量门槛优先于数量，可靠候选不足时允许只生成 1 至 4 个，不能用直白表达、
  通用动作、停用变体、标签或低价值候选凑数。
- 不把用户输入的主题名称、风险分类名称或审核结论直接改写成搜索词，除非真实内容通常就会
  使用该表达。搜索词必须更接近目标发布者或讨论者实际会写出的语言。
- 对每个候选按五项通用标准判断：目标相关性、独立可搜索性、正常内容噪声、与其他候选的
  增量召回价值，以及其表达机制是否合理。只有综合价值最高的候选才能启用。
- 可以使用谐音、拼音或字母缩写、圈层简称、场景伪装、交易暗示和引流钩子等机制，但只在
  当前目标确实适用时使用；不要机械地为每个领域凑齐这些类型。
- 单独搜索主要命中正常内容的宽泛表达不能启用。若某个语言线索只有与另一项领域线索组合后
  才有价值，应输出自然连续的完整组合表达，而不是把泛化组成部分拆成独立词条。
- 不生成必须依赖系统不支持的布尔组合查询才能成立的词；也不要因为表达足够隐晦就忽略噪声。
- 不确定某个候选是否真实存在或是否能带来目标召回时，宁可少生成，也不能编造或用近义词凑数。
- 每个变体的 note 简短说明其表达机制、适用语境或主要噪声风险，不能只重复 term。
- 用户明确指定唯一或固定搜索词且未要求扩展时，不增加其他实际搜索词。
- 每个词条使用稳定且唯一的 id；variant 之外的词条 parent_id 必须为空字符串。
""".strip()


class ResourceAuthoringError(RuntimeError):
    def __init__(
        self,
        kind: str,
        safe_message: str,
        *,
        retryable: bool,
    ) -> None:
        super().__init__(safe_message)
        self.kind = kind
        self.safe_message = safe_message
        self.retryable = retryable


@dataclass(frozen=True)
class ResourceAuthoringResult:
    content: dict[str, Any]
    model: str
    usage: dict[str, int]


def resource_generation_kinds(message: str) -> tuple[ResourceKind, ...]:
    """Return explicit resource-generation intents without routing edits or saves."""

    text = re.sub(r"\s+", "", str(message or "")).lower()
    if not text:
        return ()
    generation = re.search(
        r"(?:生成|创建|新建|重新生成|重做|做一套|来一套|给我一套|帮我(?:生成|创建|写|做))",
        text,
    )
    if generation is None:
        return ()
    if "重新生成" not in text and re.search(
        r"(?:修改|编辑|调整|保存|发布|采用|启用|停用|删除|读取|查看)", text
    ):
        return ()
    kinds: list[ResourceKind] = []
    if re.search(r"(?:审核规则|规则集|规则方案|研判规则)", text):
        kinds.append("ruleset")
    if re.search(r"(?:黑话库|词库|关键词|搜索词|召回词)", text):
        kinds.append("lexicon")
    return tuple(kinds)


class CompanyResourceAuthoringClient:
    """Responses-API client used only for new RuleSet and Lexicon authoring."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        timeout: float | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        self.api_key = (
            settings.resource_authoring_api_key if api_key is None else api_key
        )
        self.base_url = (
            settings.resource_authoring_base_url if base_url is None else base_url
        ).strip().rstrip("/")
        self.model = (
            settings.resource_authoring_model if model is None else model
        ).strip()
        self.reasoning_effort = (
            settings.resource_authoring_reasoning_effort
            if reasoning_effort is None
            else reasoning_effort
        ).strip().lower()
        if self.reasoning_effort not in {
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
        }:
            raise ValueError("unsupported resource authoring reasoning effort")
        self.timeout = float(
            settings.resource_authoring_timeout_seconds
            if timeout is None
            else timeout
        )
        self.max_output_tokens = int(
            settings.resource_authoring_max_output_tokens
            if max_output_tokens is None
            else max_output_tokens
        )

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def generate(
        self,
        kind: ResourceKind,
        *,
        user_request: str,
    ) -> ResourceAuthoringResult:
        if not self.enabled:
            raise ResourceAuthoringError(
                "configuration",
                "公司资源生成服务尚未配置。",
                retryable=False,
            )
        model_type: type[BaseModel]
        instructions: str
        schema_name: str
        if kind == "ruleset":
            model_type = RuleSetContent
            instructions = RULESET_AUTHORING_INSTRUCTIONS
            schema_name = "ruleset_content"
        elif kind == "lexicon":
            model_type = LexiconContent
            instructions = LEXICON_AUTHORING_INSTRUCTIONS
            schema_name = "lexicon_content"
        else:
            raise ValueError("unsupported resource authoring kind")

        schema = self._strict_json_schema(model_type.model_json_schema())
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": user_request,
            "store": False,
            "max_output_tokens": self.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        if self.reasoning_effort != "none":
            payload["reasoning"] = {"effort": self.reasoning_effort}
        try:
            response = requests.post(
                f"{self.base_url}/responses",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise ResourceAuthoringError(
                "timeout", "资源生成服务响应超时，请稍后重试。", retryable=True
            ) from exc
        except requests.RequestException as exc:
            raise ResourceAuthoringError(
                "transport", "资源生成服务暂时不可达。", retryable=True
            ) from exc

        if not response.ok:
            status = int(response.status_code)
            if status in {401, 403}:
                safe = "资源生成服务鉴权失败，请检查公司 Token 配置。"
                error_kind = "authentication"
                retryable = False
            elif status == 429:
                safe = "资源生成服务当前请求过多或额度受限。"
                error_kind = "rate_limit"
                retryable = True
            elif status >= 500:
                safe = "资源生成服务暂时不可用。"
                error_kind = "server"
                retryable = True
            else:
                safe = "资源生成服务拒绝了本次请求。"
                error_kind = "request"
                retryable = False
            raise ResourceAuthoringError(error_kind, safe, retryable=retryable)

        try:
            body = response.json()
        except ValueError as exc:
            raise ResourceAuthoringError(
                "invalid_response",
                "资源生成服务返回了无法解析的结果。",
                retryable=True,
            ) from exc
        if not isinstance(body, dict):
            raise ResourceAuthoringError(
                "invalid_response",
                "资源生成服务返回了无法解析的结果。",
                retryable=True,
            )
        if str(body.get("status") or "completed") != "completed":
            reason = str((body.get("incomplete_details") or {}).get("reason") or "")
            raise ResourceAuthoringError(
                "incomplete_response",
                "资源生成内容未完整返回，请重试。",
                retryable=reason != "content_filter",
            )
        raw = self._output_text(body)
        try:
            parsed = json.loads(raw)
            validated = model_type.model_validate(parsed)
            if kind == "lexicon":
                validated = self._focus_lexicon_variants(validated)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            raise ResourceAuthoringError(
                "schema_validation",
                "资源生成结果未通过结构校验，请重试。",
                retryable=True,
            ) from exc
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return ResourceAuthoringResult(
            content=validated.model_dump(mode="json"),
            model=str(body.get("model") or self.model),
            usage={
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            },
        )

    @staticmethod
    def _focus_lexicon_variants(content: LexiconContent) -> LexiconContent:
        """Keep at most five enabled variants in model-ranked entry order."""

        enabled_variants = [
            entry
            for entry in content.entries
            if entry.kind == "variant" and entry.enabled
        ]
        if not enabled_variants:
            raise ResourceAuthoringError(
                "schema_validation",
                "资源生成结果没有可用的实际搜索变体，请重试。",
                retryable=True,
            )
        selected = enabled_variants[:5]
        selected_ids = {entry.id for entry in selected}
        parent_ids = {entry.parent_id for entry in selected}
        body = content.model_dump(mode="json")
        entries: list[dict[str, Any]] = []
        for entry in content.entries:
            if entry.kind == "main" and entry.id in parent_ids:
                main = entry.model_dump(mode="json")
                main["enabled"] = True
                entries.append(main)
            elif entry.kind == "variant" and entry.id in selected_ids:
                entries.append(entry.model_dump(mode="json"))
        body["entries"] = entries
        return LexiconContent.model_validate(body)

    @staticmethod
    def _output_text(body: dict[str, Any]) -> str:
        direct = body.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        texts: list[str] = []
        for item in body.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if not isinstance(content, dict):
                    continue
                if content.get("type") == "refusal":
                    raise ResourceAuthoringError(
                        "refusal", "资源生成服务拒绝了本次内容生成。", retryable=False
                    )
                if content.get("type") == "output_text" and isinstance(
                    content.get("text"), str
                ):
                    texts.append(content["text"])
        output = "".join(texts).strip()
        if not output:
            raise ResourceAuthoringError(
                "empty_response", "资源生成服务没有返回有效内容。", retryable=True
            )
        return output

    @classmethod
    def _strict_json_schema(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [cls._strict_json_schema(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {
            key: cls._strict_json_schema(item)
            for key, item in value.items()
            if key != "default"
        }
        if result.get("type") == "object" or "properties" in result:
            properties = result.get("properties") or {}
            result["additionalProperties"] = False
            result["required"] = list(properties)
        return result
