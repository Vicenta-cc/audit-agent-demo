import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { AgentCollaborationCard } from "../src/features/investigation/AgentCollaborationCard";
import { TaskQuota } from "../src/components/feedback/TaskQuota";
import type { InvestigationRunProjection } from "../src/types/investigationCreation";
import "../src/styles/tokens.css";
import "../src/styles/global.css";
import "../src/styles/investigation-workspace.css";

const base: InvestigationRunProjection = {
  run_id: "fixture-run", draft_id: "fixture-draft", draft_revision: 1, job_id: "",
  status: "QUEUED", crawl_status: "queued", analysis_status: "pending",
  report_status: "pending", report_version_id: "", error_code: "", error_message: "",
  created_at: "", updated_at: "", started_at: "", completed_at: "",
  task_stats: { completed_analysis_count: 4, failed_analysis_count: 1, pending_analysis_count: 1, queued_analysis_count: 0 },
  audit_results: [], available_actions: { end_task: true }
};
function Fixture() {
  const [run, setRun] = useState(base);
  return <MemoryRouter><main style={{maxWidth: 850, margin: "24px auto"}}>
    <TaskQuota />
    <button onClick={() => setRun({ ...base, job_id: "fixture-job", status: "INTERRUPTED", crawl_status: "stopped", analysis_status: "paused", available_actions: { end_task: true, resume_analysis: true } })}>模拟暂停状态</button>
    <button onClick={() => setRun({ ...base, status: "FAILED", crawl_status: "stopped", analysis_status: "stopped", report_status: "cancelled", error_code: "cancelled", error_message: "任务已终止。", available_actions: { ended: true } })}>模拟后台确认停止</button>
    <AgentCollaborationCard phase="collection_working" authoritative run={run} investigationId="fixture" investigationTitle="验收任务"
      onControlAccepted={() => setRun(current => ({ ...current, available_actions: { ending: true } }))} />
  </main></MemoryRouter>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
