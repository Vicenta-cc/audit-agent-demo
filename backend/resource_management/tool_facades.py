"""Small discovery contracts over the unchanged resource command lifecycle."""
from copy import deepcopy
from typing import Literal

from pydantic import model_validator

from backend.rulesets.contracts import RuleSetContent, StrictModel
from .contracts import LexiconContent
from .generation_contracts import LexiconGenerationRequest, ResourceGenerationRequest


class GenerateRuleSetInput(StrictModel):
    generation_request: ResourceGenerationRequest


class GenerateLexiconInput(StrictModel):
    generation_request: LexiconGenerationRequest


class ImportResourceInput(StrictModel):
    kind: Literal['ruleset', 'lexicon']
    content: RuleSetContent | LexiconContent

    @model_validator(mode='after')
    def matching_kind(self):
        expected = RuleSetContent if self.kind == 'ruleset' else LexiconContent
        if not isinstance(self.content, expected):
            raise ValueError('content must match the selected resource kind')
        return self


GENERATION_INPUTS = {
    'create_ruleset_proposal': GenerateRuleSetInput,
    'create_lexicon_edit': GenerateLexiconInput,
}
GENERATION_DESCRIPTIONS = {
    'create_ruleset_proposal': (
        '生成全新的独立规则，传 generation_request：objective、platform、requirements；'
        '仅用户指定数量时传 requested_count。修改已有规则用 update_resource_edit。'
        '返回完整规则、read_arguments、update_arguments 和 resource_ref；展示后经用户采用可用于调查草案。'
        '生成不保存、不采用、不启动。生成失败时按错误修正，不改用导入伪造成功。'
        '仅原样导入用户给定的完整结构化正文用 import_resource。'
    ),
    'create_lexicon_edit': (
        '生成全新的独立词库或搜索词，传 generation_request：objective、platform、requirements；'
        '仅用户指定数量时传 requested_count，禁止扩展的原文词表传 exact_terms；'
        '可选 keyword_profile，模板由后台加载。修改已有词库用 update_resource_edit。'
        '返回完整词库、search_terms、read_arguments、update_arguments 和 resource_ref，'
        '用户采用后可将引用传给 configure_investigation_draft.lexicon_ref，临时使用无需保存。'
        '生成不保存、不采用、不启动。生成失败时按错误修正，不改用导入伪造成功。'
        '仅原样导入用户给定的完整结构化正文用 import_resource。'
    ),
}
IMPORT_DESCRIPTION = (
    '仅原样导入用户已提供的完整结构化规则或词库，kind 与 content 类型一致。'
    '后台校验完整正文并创建当前会话编辑稿，不调用生成模型。'
    '按需求生成用 create_ruleset_proposal/create_lexicon_edit；已有资源修改用 update_resource_edit。'
    '不得在生成失败后自行编造正文改走导入。导入不保存、不采用、不启动。'
)


def normalize_import(name, arguments):
    """Validate an explicit import before forwarding to its existing command."""
    if name != 'import_resource':
        return name, arguments
    parsed = ImportResourceInput.model_validate(arguments)
    canonical_name = ('create_ruleset_proposal' if parsed.kind == 'ruleset'
                      else 'create_lexicon_edit')
    return canonical_name, {'content': parsed.content.model_dump(mode='json')}


def facade_schemas(existing_schemas):
    """Replace generation discovery only; callers retain scope filtering."""
    result = deepcopy(list(existing_schemas))
    for schema in result:
        name = schema['name']
        if name in GENERATION_INPUTS:
            schema['description'] = GENERATION_DESCRIPTIONS[name]
            schema['parameters'] = GENERATION_INPUTS[name].model_json_schema()
    imported = {
        'name': 'import_resource', 'description': IMPORT_DESCRIPTION,
        'parameters': ImportResourceInput.model_json_schema(),
    }
    for index, schema in enumerate(result):
        if schema['name'] == 'import_resource':
            result[index] = imported
            break
    else:
        result.append(imported)
    return result
