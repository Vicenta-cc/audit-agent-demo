import type { InvestigationSession } from "../../types/investigation";
import type { InvestigationRunProjection } from "../../types/investigationCreation";
import { mapInvestigationRunState } from "./investigationRunState";
import { formatSessionTimestamp, latestSessionTimestamp } from "./sessionOrdering";

/** A run updates its own card even when the user is already editing a later round. */
export function applyWorkspaceRun(session: InvestigationSession, run: InvestigationRunProjection): InvestigationSession {
  const binding = session.creationBinding;
  if (!binding) return session;
  const current = binding.draft?.id === run.draft_id;
  const round = binding.rounds?.find((item) => item.draft_id === run.draft_id);
  const id = `workspace-run:${run.run_id}`;
  const previous = session.messages.find((message) => message.id === id || message.runData?.run_id === run.run_id);
  const runMessage = {
    ...previous,
    id,
    sender: "assistant" as const,
    timestamp: new Date(run.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
    occurredAt: run.created_at,
    type: "agent_collaboration" as const,
    runData: run,
    roundLabel: round?.label,
    draftData: previous?.draftData || { ...session.draft, confirmed: true, status: "已创建" }
  };
  const messages = previous
    ? session.messages.map((message) => message === previous ? runMessage : message)
    : [...session.messages, runMessage];
  const updatedAtIso = latestSessionTimestamp(session.updatedAtIso, run.updated_at);
  return {
    ...session,
    updatedAtIso,
    updatedAt: formatSessionTimestamp(updatedAtIso),
    messages,
    ...(current ? {
      status: run.status === "PUBLISHED" ? "报告已生成" : run.status === "AUDIT_COMPLETED" ? "审核完成"
        : run.status === "FAILED" ? "调查失败" : run.status === "INTERRUPTED" ? "调查已中断" : "研判中",
      draft: { ...session.draft, status: "已创建", confirmed: true },
      executionPhase: mapInvestigationRunState(run).phase,
      executionProgress: run.status === "PUBLISHED" ? 100 : run.status === "AUDIT_COMPLETED" ? 75 : session.executionProgress
    } : {}),
    creationBinding: {
      ...binding,
      rounds: binding.rounds?.map((item) => item.draft_id === run.draft_id ? { ...item, run } : item),
      ...(current ? { run, error: run.error_message || undefined } : {})
    }
  };
}
