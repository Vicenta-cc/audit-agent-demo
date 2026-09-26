import { useState } from "react";
import type { PublicInvestigationDraft } from "../../types/investigationCreation";
import { saveInvestigationDraftLexicon, saveInvestigationDraftRuleset } from "../../services/investigationCreation";

/** Save the displayed server revision, never rewrite a confirmed task. */
export function DraftResourceSave({ draft }: { draft: PublicInvestigationDraft }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const investigation = draft.configuration.investigation;
  const hasLexicon = investigation.mode === "search"
    && investigation.recall_plan.strategy === "temporary_terms"
    && Boolean(investigation.recall_plan.lexicon_content);
  const save = async (kind: "lexicon" | "ruleset") => {
    if (busy) return;
    setBusy(true);
    setMessage("");
    setFailed(false);
    try {
      const action = kind === "lexicon" ? saveInvestigationDraftLexicon : saveInvestigationDraftRuleset;
      await action(draft.id, {
        expectedRevision: draft.current_revision,
        operationId: `draft-${kind}:${draft.id}:r${draft.current_revision}`
      });
      setMessage(`${kind === "lexicon" ? "关键词" : "规则"}已保存到我的资源库；任务配置未改变。`);
    } catch (error) {
      setFailed(true);
      setMessage(error instanceof Error ? error.message : "保存失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  };
  return <section aria-label="保存任务资源">
    <p>保存当前已展示版本到自己的资源库，不影响任务或报告。</p>
    {hasLexicon && <button type="button" disabled={busy} onClick={() => void save("lexicon")}>保存关键词到我的词库</button>}
    <button type="button" disabled={busy} onClick={() => void save("ruleset")}>另存规则到我的规则库</button>
    {message && <p role={failed ? "alert" : "status"}>{message}</p>}
  </section>;
}
