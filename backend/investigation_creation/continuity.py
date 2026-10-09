"""Opt-in access to existing session facts; never an alternative resource store."""
from pydantic import Field
from backend.rulesets.contracts import StrictModel
from backend.audit_agent.config import settings
from backend.resource_management.session_state import SessionResourceReader


class ListSessionResources(StrictModel):
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str = Field(default='', max_length=512)


class ReadSessionResource(StrictModel):
    key: str = Field(min_length=1, max_length=512)


INPUTS = {'list_session_resources': ListSessionResources, 'read_session_resource': ReadSessionResource}
DESCRIPTIONS = {
    'list_session_resources': '从数据库恢复当前会话全部临时规则、词库、历史编辑版本、保存回执、草案与任务冻结配置的目录。按需翻页；不依赖模型记忆，不写入、不采用。当前编辑版本不等于任务使用版本；来源未知不得猜测。',
    'read_session_resource': '用会话资源目录返回的 key 读取精确版本完整内容。保存本次调查使用的规则或词库前先读取 run 的冻结配置；继续编辑则读取当前 edit_version。只读；多个目标确有歧义时询问，明确任务版本时不要求用户操作选择面板。',
}


def schemas():
    if not settings.continuous_resource_session_enabled:
        return ()
    return tuple(dict(name=name, description=DESCRIPTIONS[name], parameters=model.model_json_schema())
                 for name, model in INPUTS.items())


def read(application, name, arguments, *, session_id, principal):
    parsed = INPUTS[name].model_validate(arguments)
    reader = SessionResourceReader(application.store.db_path,
        application.resource_service.lexicon_store.db_path, application.conversation_store.db_path)
    method = reader.read if name == 'list_session_resources' else reader.detail
    return method(session_id, principal=principal, **parsed.model_dump())


GUIDANCE = '''
连续会话能力已由应用启用。可以在报告问询和词库、规则的生成/编辑/保存之间切换。
报告状态只约束原任务：已启动的配置保持冻结，本会话不创建第二个调查；独立资源仍可修改和保存。
用 list_session_resources / read_session_resource 恢复本会话资源与精确版本，不凭最近编辑猜测任务实际采用版本。
资源身份和成功状态以工具回执为准；目录读取失败不等于没有资源。保存任务所用内容先读取冻结配置，
不要用最新编辑稿替换；保存与采用、启动分别处理。有明确目标直接执行，确实有歧义才简短澄清。
报告工具只查询授权的已发布报告，不修改报告；没有报告绑定时仍可管理资源。
'''


def prepare_report(conversation, workspace, principal, history):
    """Bind existing read-only report tools under the Hermes product lock."""
    if not workspace.run or not workspace.run.report_version_id:
        return history
    report_service = conversation.report_service
    if report_service is None:
        raise RuntimeError('连续会话报告服务未配置')
    run = conversation.authorize_report_handoff(workspace.session.id, workspace.run.run_id, principal=principal)
    report_session = conversation.store.find_session_by_anchor(f'm3-run:{run.run_id}')
    if report_session is None or report_session.report_version_id != run.report_version_id:
        raise RuntimeError('报告关联无法验证')
    try:
        report_service.bind_workspace_report(report_session, workspace_session_id=workspace.session.id)
    finally:
        # Binding configures report mode; the current agent remains the resource
        # conversation with its registered tools, not a report-only agent.
        conversation.runtime_binding.activate_product_mode('creation')
    from hermes_m0.runtime import report_task_runtime_for_session
    from hermes_m0.reference_state import prepare_conversation_history
    from hermes_m0.comment_batches import reconcile
    runtime = report_task_runtime_for_session(workspace.session.id)
    if hasattr(runtime, 'comment_batch_states'):
        reconcile(runtime, workspace.session.id, conversation.store)
    return prepare_conversation_history(runtime, workspace.session.id, history) if history else history


def report_context(session_id):
    from hermes_m0.runtime import report_task_runtime_for_session
    from hermes_m0.comment_batches import delivery_context
    runtime = report_task_runtime_for_session(session_id)
    if runtime is None:
        return '\n当前没有可查询的报告绑定，不能声称已读取报告。'
    import json
    context = '\n当前已有已发布报告，可使用 read_report 及报告证据工具。'
    if hasattr(runtime, 'comment_batch_states'):
        context += '\n服务端评论交付进度：' + json.dumps(delivery_context(runtime, session_id), ensure_ascii=False)
    if runtime.account_activity is not None:
        context += '\n账号活动查询授权报告（具体活动仍须工具读取）：' + json.dumps(
            [repo.report.title for repo in runtime.account_activity.authorized_report_repositories], ensure_ascii=False)
    return context
