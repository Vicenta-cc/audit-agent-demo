"""Model-facing partial draft configuration, reusing existing write boundaries."""
from typing import Literal
from pydantic import Field, StrictInt, StrictStr, model_validator

from .contracts import StrictModel, Platform, InvestigationDraftConfiguration, CreateDraftCommand, UpdateDraftCommand
from .approval import UseRuleSetProposalInput
from .errors import ConfigurationValidationError
from .scope_intent import RelevanceScopeChange

NAME = 'configure_investigation_draft'
LEGACY_NAMES = frozenset({'create_investigation_draft', 'update_investigation_draft', 'use_ruleset_proposal'})


class ConfigureDraftInput(StrictModel):
    operation: Literal['create', 'update']
    draft_id: StrictStr | None = Field(default=None, min_length=1)
    expected_revision: StrictInt | None = Field(default=None, ge=1)
    title: StrictStr | None = Field(default=None, min_length=1, max_length=300)
    objective: StrictStr | None = Field(default=None, min_length=1, max_length=4000)
    platform: Platform | None = None
    ruleset_ref: StrictStr | None = Field(default=None, pattern=r'^resource-ref:[0-9a-f]{32}$')
    lexicon_ref: StrictStr | None = Field(default=None, pattern=r'^resource-ref:[0-9a-f]{32}$')
    creator_url: StrictStr | None = Field(default=None, min_length=1, max_length=2000)
    scope_decision: Literal['apply', 'none'] | None = Field(
        default=None,
        description='仅 create 必填：调查对象/事件名称包含地域选 apply 并传 set；无地域或已明确取消选 none 且不传 change。update 省略。',
    )
    relevance_scope_change: RelevanceScopeChange | None = None

    @model_validator(mode='after')
    def validate_operation(self):
        changes = ('title', 'objective', 'platform', 'ruleset_ref', 'lexicon_ref', 'creator_url', 'relevance_scope_change')
        if self.lexicon_ref and self.creator_url:
            raise ValueError('lexicon_ref and creator_url select different investigation modes; provide only one')
        if self.operation == 'create':
            if self.draft_id is not None or self.expected_revision is not None:
                raise ValueError('create must not include draft_id or expected_revision')
            if self.scope_decision is None:
                raise ValueError('create requires scope_decision: apply or none')
            if self.scope_decision == 'apply' and (
                self.relevance_scope_change is None or self.relevance_scope_change.action != 'set'
            ):
                raise ValueError('scope_decision=apply requires relevance_scope_change.action=set')
            if self.scope_decision == 'none' and self.relevance_scope_change is not None:
                raise ValueError('scope_decision=none must omit relevance_scope_change')
            if not all((self.title, self.objective, self.platform, self.ruleset_ref, self.lexicon_ref or self.creator_url)):
                raise ValueError('create requires title, objective, platform, ruleset_ref and either lexicon_ref or creator_url')
        else:
            if 'scope_decision' in self.model_fields_set:
                raise ValueError('update must omit create-only scope_decision')
            if not self.draft_id or self.expected_revision is None or not any(getattr(self, key) is not None for key in changes):
                raise ValueError('update requires draft_id, expected_revision and at least one changed field')
        for key in self.model_fields_set:
            if getattr(self, key) is None:
                raise ValueError(f'{key}: omit unchanged fields; null is not a clear operation')
        return self


DESCRIPTION = (
    '创建或局部更新调查草案的唯一配置入口。operation 明确为 create 或 update。'
    'create 提供标题、目标、平台、ruleset_ref，以及 lexicon_ref（关键词调查）或 creator_url（账号调查）。'
    'create 必须明确传 scope_decision：apply 同时传 relevance_scope_change.action=set；none 必须省略 relevance_scope_change。缺失或矛盾会在写入前拒绝。'
    'update 提供 draft_id/expected_revision，只传变化字段，省略的内容由后台原样保留；不传 null 或整份 configuration。'
    'update 不传 create 专用的 scope_decision；地域沿用省略保留、set 修改、clear 取消。'
    '正式和临时规则都填 ruleset_ref，正式和临时词库都填 lexicon_ref；优先直接复制生成、读取或编辑回执中的 resource_ref；'
    '正式资源从 read_resource、旧临时稿从 get_resource_edit 取得引用，'
    '不能猜测引用或抄写内容、hash、presentation_id。临时规则仍须先完整展示，等待后续用户明确采用；引用不是同意凭证。'
    '多个字段一起校验、一起提交。词库按其主词、变体与启用状态计算搜索词；改词条或启用状态先 update_resource_edit，再取新引用。'
    '本工具不修改资源正文、不保存资源、不启动任务；启动仍使用现有确认工具。版本冲突先读草案并核对，不强行替换版本重试。'
    '本次调查对象、议题或事件名称包含地域时默认采用该地域，无需用户另说某地的舆情或明确限定。'
    '例如调查克拉玛依大火、深圳楼市、新疆棉花争议，分别采用克拉玛依、深圳、新疆；议题与地域分别保存，不能只写入标题、目标或词库。'
    '设置或换地域时，relevance_scope_change传action=set、用户原始地名target_text、真实用户原话source_quote和selection_policy。'
    '同任务用户最新明确更换、取消或要求全国且不限制地域优先；关注全国网友讨论该事件不等于取消事件地域。'
    '资源齐备并获采用授权时，回看同任务真实用户历史中的调查对象和后续变更或取消；本轮只说采用资源不表示放弃此前地域，创建时引用原要求并一并配置，不要求用户重复。'
    '明确只要当地、不补充选local_only，其余有地域的调查选local_first_with_supplement，无需为默认策略重复确认。'
    '创建前明确取消地域时选scope_decision=none；已有草案取消时只传action=clear和source_quote；只改资源或标题时省略地域变化保留原地域，不能传null。'
    '不要从账号背景、资源名称、引用举例、否定、助手建议或仅修改标题推断地域；用户没提地名时不靠事件常识补地点；不填行政代码、身份或来源ID。'
    '调查目标支持单一省级地域或地级市（含直辖市）；地域简称直接传原文解析，解析成功直接报告。'
    '省市同名简称、下级地域、未知名或指代不明也传原文解析，仅按真实pending回执及候选提示澄清；区县同名不阻断明确城市别名，不自行猜测纠错或扩大范围。'
    '已有草案不能只口头澄清而保留可启动状态。'
    '条件齐备且用户已明确要求时直接修改草案，成功后报告，无需再问是否应用；资源展示和后续采用授权仍须满足。'
    '地域调查是否可启动以真实确认预览和工具预检回执为准；未接通地域执行能力时如实报告阻断。'
    '只有确认工具成功回执后才可声称任务已排队，实际执行状态以运行回执为准；保存地域本身不表示已开始限定采集。'
)


def execute(tool_service, command, *, principal, session_id, identity, create_operation, mutation_receipt=None):
    app = tool_service.application_service
    with tool_service._conversation_lock:
        turn_id = tool_service._conversation_turns.get(session_id, '')
    previous = app.get_draft(command.draft_id, principal=principal) if command.operation == 'update' else None
    geographic = dict(scope_change=command.relevance_scope_change, scope_turn_id=turn_id)
    if previous and previous.current_revision != command.expected_revision:
        raise ConfigurationValidationError('草案版本已变化，请读取并核对后再修改。', code='DRAFT_REVISION_STALE')
    body = previous.configuration.model_dump(mode='json') if previous else {}
    title = command.title if command.title is not None else (previous.title if previous else '')
    objective = command.objective if command.objective is not None else (previous.objective if previous else '')
    if command.platform is not None:
        body['platform'] = command.platform
    if command.lexicon_ref:
        recall = app.resource_management.resolve_lexicon_ref(command.lexicon_ref, principal=principal, session_id=session_id)
        body['investigation'] = {'mode': 'search', 'recall_plan': recall}
    elif command.creator_url:
        body['investigation'] = {'mode': 'creator', 'creator_url': command.creator_url}
    presentation = None
    if command.ruleset_ref:
        from backend.resource_management.ruleset_refs import resolve
        judgement, presentation = resolve(app.resource_management, command.ruleset_ref,
            principal=principal, session_id=session_id, turn_id=turn_id)
        if judgement:
            body['judgement'] = judgement
    if presentation:
        target = ({'draft_id': command.draft_id, 'expected_revision': command.expected_revision} if previous
                  else {'create_draft': {'title': title, 'objective': objective,
                        'configuration': {key: value for key, value in body.items() if key != 'judgement'}}})
        # The adoption transaction owns the combined update. No intermediate
        # ordinary update is committed before adoption has succeeded.
        return app.use_ruleset_proposal(UseRuleSetProposalInput(presentation_id=presentation, **target),
            principal=principal, session_id=session_id, turn_id=turn_id,
            runtime_turn_id=identity.turn_id if identity else '', tool_call_id=identity.tool_call_id if identity else '',
            resource_ref=command.lexicon_ref, create_operation=create_operation, mutation_receipt=mutation_receipt,
            **geographic,
            draft_patch=({'title': title, 'objective': objective,
                          'configuration': {key: value for key, value in body.items() if key != 'judgement'}} if previous else None))
    configuration = InvestigationDraftConfiguration.model_validate(body)
    if previous:
        return app.update_draft(UpdateDraftCommand(draft_id=previous.id, expected_revision=command.expected_revision,
            title=title, objective=objective, configuration=configuration), principal=principal,
            resource_ref=command.lexicon_ref, session_id=session_id, mutation_receipt=mutation_receipt,
            **geographic)
    return app.create_draft(CreateDraftCommand(title=title, objective=objective, configuration=configuration),
        principal=principal, resource_ref=command.lexicon_ref, session_id=session_id, create_operation=create_operation,
        **geographic)
