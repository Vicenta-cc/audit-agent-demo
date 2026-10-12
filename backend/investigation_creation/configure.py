"""Model-facing partial draft configuration, reusing existing write boundaries."""
from typing import Literal
from pydantic import Field, StrictInt, StrictStr, model_validator

from .contracts import StrictModel, Platform, InvestigationDraftConfiguration, CreateDraftCommand, UpdateDraftCommand
from .approval import UseRuleSetProposalInput
from .errors import ConfigurationValidationError

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

    @model_validator(mode='after')
    def validate_operation(self):
        changes = ('title', 'objective', 'platform', 'ruleset_ref', 'lexicon_ref', 'creator_url')
        if self.lexicon_ref and self.creator_url:
            raise ValueError('lexicon_ref and creator_url select different investigation modes; provide only one')
        if self.operation == 'create':
            if self.draft_id is not None or self.expected_revision is not None:
                raise ValueError('create must not include draft_id or expected_revision')
            if not all((self.title, self.objective, self.platform, self.ruleset_ref, self.lexicon_ref or self.creator_url)):
                raise ValueError('create requires title, objective, platform, ruleset_ref and either lexicon_ref or creator_url')
        elif not self.draft_id or self.expected_revision is None or not any(getattr(self, key) is not None for key in changes):
            raise ValueError('update requires draft_id, expected_revision and at least one changed field')
        for key in self.model_fields_set:
            if getattr(self, key) is None:
                raise ValueError(f'{key}: omit unchanged fields; null is not a clear operation')
        return self


DESCRIPTION = (
    '创建或局部更新调查草案的唯一配置入口。operation 明确为 create 或 update。'
    'create 提供标题、目标、平台、ruleset_ref，以及 lexicon_ref（关键词调查）或 creator_url（账号调查）。'
    'update 提供 draft_id/expected_revision，只传变化字段，省略的内容由后台原样保留；不传 null 或整份 configuration。'
    '正式和临时规则都填 ruleset_ref，正式和临时词库都填 lexicon_ref；引用从 read_resource/get_resource_edit 取得，'
    '不能猜测引用或抄写内容、hash、presentation_id。临时规则仍须先完整展示，等待后续用户明确采用；引用不是同意凭证。'
    '多个字段一起校验、一起提交。词库按其主词、变体与启用状态计算搜索词；改词条或启用状态先 update_resource_edit，再取新引用。'
    '本工具不修改资源正文、不保存资源、不启动任务；启动仍使用现有确认工具。版本冲突先读草案并核对，不强行替换版本重试。'
)


def execute(tool_service, command, *, principal, session_id, identity, create_operation, mutation_receipt=None):
    app = tool_service.application_service
    with tool_service._conversation_lock:
        turn_id = tool_service._conversation_turns.get(session_id, '')
    previous = app.get_draft(command.draft_id, principal=principal) if command.operation == 'update' else None
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
            draft_patch=({'title': title, 'objective': objective,
                          'configuration': {key: value for key, value in body.items() if key != 'judgement'}} if previous else None))
    configuration = InvestigationDraftConfiguration.model_validate(body)
    if previous:
        return app.update_draft(UpdateDraftCommand(draft_id=previous.id, expected_revision=command.expected_revision,
            title=title, objective=objective, configuration=configuration), principal=principal,
            resource_ref=command.lexicon_ref, session_id=session_id, mutation_receipt=mutation_receipt)
    return app.create_draft(CreateDraftCommand(title=title, objective=objective, configuration=configuration),
        principal=principal, resource_ref=command.lexicon_ref, session_id=session_id, create_operation=create_operation)
