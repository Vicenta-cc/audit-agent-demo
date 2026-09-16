import { useEffect, useMemo, useState } from "react";
import { apiRequest } from "../../services/apiClient";
import type { InvestigationTaskParameters } from "../../types/investigationCreation";
import { TaskParametersForm } from "./TaskParametersForm";

type SavedSettings = { revision: number; parameters: InvestigationTaskParameters; effective_parameters?: InvestigationTaskParameters };
const ignoreDirty = () => {};

export function TaskSettingsPage() {
  const [saved, setSaved] = useState<SavedSettings>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const reload = async () => {
    try { setSaved(await apiRequest<SavedSettings>("/api/task-settings")); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "读取统一设置失败"); }
  };
  useEffect(() => { void reload(); }, []);
  const preview = useMemo(() => saved ? ({
    requested_parameters: saved.parameters, effective_parameters: saved.effective_parameters,
    platform: "dy" as const, mode: "search" as const, resolved_search_terms: [], estimated_max_contents: 0,
  }) : null, [saved]);
  return <section style={{ maxWidth: 1000, margin: "24px auto", padding: 24, background: "white", borderRadius: 12 }} aria-label="统一采集与分析设置">
    <h2>采集与分析设置</h2>
    <p>应用级统一设置，不属于某一个调查会话。每个任务在确认启动时留存实际生效参数，后续恢复仍使用该快照。</p>
    {error ? <p role="alert">{error}</p> : null}
    <button type="button" className="mt-button mt-button-secondary" onClick={() => void reload()}>重新读取已保存设置</button>
    {preview && saved ? <TaskParametersForm preview={preview} globalSettings onDirty={ignoreDirty} onSave={async parameters => {
      const result = await apiRequest<SavedSettings>("/api/task-settings", {
        method: "PUT", body: JSON.stringify({ expected_revision: saved.revision, parameters }),
      });
      setSaved(result); setNotice("统一设置已保存，将用于后续确认启动的任务。正在运行的任务不受影响。");
    }} /> : <p>正在读取设置…</p>}
    {notice ? <p role="status">{notice}</p> : null}
  </section>;
}
