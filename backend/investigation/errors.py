from __future__ import annotations


class InvestigationError(RuntimeError):
    code = "investigation_error"
    safe_message = "调查对话暂时无法完成。"
    retryable = False

    def __init__(self, message: str = ""):
        super().__init__(message or self.safe_message)


class ReportScopeError(InvestigationError):
    code = "invalid_report_scope"
    safe_message = "当前会话绑定的报告版本或来源范围无效。"


class ReportNotFoundError(InvestigationError):
    code = "report_not_found"
    safe_message = "没有找到指定的已发布报告版本。"


class InvestigationSessionNotFoundError(InvestigationError):
    code = "session_not_found"
    safe_message = "调查会话不存在。"


class InvestigationTurnNotFoundError(InvestigationError):
    code = "turn_not_found"
    safe_message = "调查对话轮次不存在。"


class ArtifactNotFoundError(InvestigationError):
    code = "artifact_not_found"
    safe_message = "没有找到指定的资料 Artifact。"


class ArtifactIntegrityError(InvestigationError):
    code = "artifact_integrity_error"
    safe_message = "资料 Artifact 未通过完整性校验。"


class ArtifactConflictError(ArtifactIntegrityError):
    code = "artifact_fingerprint_conflict"
    safe_message = "资料 Artifact 指纹发生冲突，已停止写入。"


class ConcurrentTurnError(InvestigationError):
    code = "turn_already_running"
    safe_message = "当前会话已有一轮对话正在执行。"
    retryable = True


class ClientMessageConflictError(InvestigationError):
    code = "client_message_conflict"
    safe_message = "该消息标识已用于另一条问题，已拒绝重复提交。"


class CheckpointScopeMismatchError(InvestigationError):
    code = "checkpoint_scope_mismatch"
    safe_message = "恢复点与当前会话绑定的报告版本不一致，已停止恢复。"


class ToolProtocolError(InvestigationError):
    code = "tool_protocol_error"
    safe_message = "模型返回的工具调用协议无效。"


class GroundingValidationError(InvestigationError):
    code = "grounding_validation_failed"
    safe_message = "回答缺少可验证的当前报告来源。"


class ContextBudgetError(InvestigationError):
    code = "context_budget_exceeded"
    safe_message = "当前对话上下文超过了本轮可处理范围。"


class RunTokenBudgetError(InvestigationError):
    code = "run_token_budget_exceeded"
    safe_message = "本轮模型调用将超过累计 Token 上限，已在调用前停止。"
    stop_reason = "token_budget"


class QwenChatError(InvestigationError):
    def __init__(
        self,
        kind: str,
        safe_message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ):
        super().__init__(safe_message)
        self.kind = kind
        self.code = f"qwen_{kind}"
        self.safe_message = safe_message
        self.retryable = retryable
        self.status_code = status_code
