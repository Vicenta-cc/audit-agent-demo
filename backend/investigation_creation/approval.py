"""Validate binding provenance; natural-language adoption semantics belong to the Agent."""

import json
import sqlite3

from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator

from .contracts import InvestigationMode, Platform, StrictModel, TemporaryRuleSetProposal
from .errors import ConfigurationValidationError
from .presentation import message_presentations


class ProposalDraftConfiguration(StrictModel):
    platform: Platform
    investigation: InvestigationMode


class ProposalDraftCreation(StrictModel):
    title: StrictStr = Field(min_length=1, max_length=300)
    objective: StrictStr = Field(min_length=1, max_length=4_000)
    configuration: ProposalDraftConfiguration

    @field_validator("title", "objective", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class UseRuleSetProposalInput(StrictModel):
    presentation_id: StrictStr = Field(min_length=1, max_length=160)
    draft_id: StrictStr | None = Field(default=None, min_length=1, max_length=160)
    expected_revision: StrictInt | None = Field(default=None, ge=1)
    create_draft: ProposalDraftCreation | None = None

    @model_validator(mode="after")
    def target(self) -> "UseRuleSetProposalInput":
        if self.draft_id:
            if self.expected_revision is None or self.create_draft is not None:
                raise ValueError("existing Draft requires expected_revision and no create_draft")
        elif self.expected_revision is not None or self.create_draft is None:
            raise ValueError("without a Draft, complete create_draft configuration is required")
        return self


# Safe explanations also serve as the existing conversation's failure fallback.
BINDING_FAILURES = {
    "PROPOSAL_APPROVAL_REQUIRED": ("缺少有效的当前用户回合。", "请在当前会话中重新提出采用请求。"),
    "PROPOSAL_NOT_PRESENTED": ("当前会话没有可用的这份规则展示记录。", "请先完整展示规则，再等待用户下一次确认。"),
    "INVALID_PROPOSAL_PRESENTATION": ("规则展示记录未通过完整性校验。", "请重新完整展示规则，再等待用户下一次确认。"),
    "PROPOSAL_PRESENTATION_STALE": ("已展示的规则版本发生了变化。", "请重新展示当前版本，并等待下一次用户消息确认；不要在本轮自动采用新版。"),
    "DRAFT_REVISION_STALE": ("调查草案已发生更新。", "请先读取最新草案并核对变更，不要仅替换版本号强制重试。"),
    "DRAFT_TARGET_REQUIRED": ("当前会话已经有调查草案。", "请读取现有草案并明确采用目标。"),
    "DRAFT_NOT_AUTHORIZED": ("当前身份无法操作目标草案。", "请使用当前身份有权访问的草案。"),
    "DRAFT_NOT_EDITABLE": ("目标草案已不能编辑。", "请读取草案状态并选择可编辑的目标。"),
}


def reject(code: str, message: str = "", **details) -> None:
    reason, recovery = BINDING_FAILURES.get(code, (message, "请检查配置和操作目标后再继续。"))
    raise ConfigurationValidationError(
        reason, code=code, details={**details, "mutation_applied": False, "recovery": recovery}
    )


def published_presentations(connection: sqlite3.Connection, *, session_id: str,
                            before_sequence: int, prefix: str = "") -> tuple[list[dict], str]:
    # prefix is an internal SQL namespace, never a model/user argument.
    assert prefix in ("", "conversation.")
    rows = connection.execute(
        f"""SELECT t.*, m.content, m.sequence, m.created_at AS message_created_at
           FROM {prefix}investigation_turns t
           JOIN {prefix}investigation_messages m ON m.id=t.assistant_message_id
           JOIN {prefix}investigation_messages u ON u.id=t.user_message_id
           WHERE t.session_id=? AND t.status='completed' AND m.session_id=t.session_id
             AND m.turn_id=t.id AND m.role='assistant' AND m.sequence < ?
             AND u.session_id=t.session_id AND u.turn_id=t.id AND u.role='user'
             AND u.sequence < m.sequence
           ORDER BY m.sequence""", (session_id, before_sequence),
    ).fetchall()
    records = []
    current_draft_id = ""
    try:
        for row in rows:
            artifact = json.loads(row["public_artifact_json"])
            if artifact.get("artifact_type") == "investigation_draft":
                current_draft_id = artifact["draft_id"]
            for record in artifact.get("proposal_presentations", []):
                # Historical artifacts remain readable but cannot authorize use.
                if not record.get("presentation_id"):
                    continue
                snapshot = TemporaryRuleSetProposal.model_validate(record["snapshot"])
                expected = message_presentations(
                    [snapshot.model_dump(mode="json")], session_id=session_id, turn_id=row["id"],
                    user_message_id=row["user_message_id"], assistant_message_id=row["assistant_message_id"],
                    presented_at=row["message_created_at"],
                )[0]
                if record != expected or record["text"] not in row["content"]:
                    reject("INVALID_PROPOSAL_PRESENTATION")
                records.append(record)
    except (ValueError, KeyError, TypeError):
        reject("INVALID_PROPOSAL_PRESENTATION")
    return records, current_draft_id


def resolve_approval(connection: sqlite3.Connection, *, session_id: str,
                     turn_id: str, principal: str, presentation_id: str) -> dict:
    current = connection.execute(
        """SELECT t.*, m.sequence, s.owner_principal, s.scope_type,
                  s.status AS session_status
           FROM conversation.investigation_turns t
           JOIN conversation.investigation_messages m ON m.id=t.user_message_id
           JOIN conversation.investigation_sessions s ON s.id=t.session_id
           WHERE t.id=? AND t.session_id=? AND m.session_id=t.session_id
             AND m.turn_id=t.id AND m.role='user'""", (turn_id, session_id),
    ).fetchone()
    if (current is None or current["owner_principal"] != principal
            or current["scope_type"] != "creation" or current["session_status"] != "active"
            or current["status"] != "running"):
        reject("PROPOSAL_APPROVAL_REQUIRED")
    records, draft_id = published_presentations(
        connection, session_id=session_id, before_sequence=current["sequence"], prefix="conversation.",
    )
    selected = [record for record in records if record["presentation_id"] == presentation_id]
    if len(selected) != 1:
        reject("PROPOSAL_NOT_PRESENTED")
    return {"presentation": selected[0], "approving_user_turn_id": turn_id,
            "approving_user_message_id": current["user_message_id"], "current_draft_id": draft_id}
