import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { AgentCollaborationCard } from "../src/features/investigation/AgentCollaborationCard";
import { TaskQuota } from "../src/components/feedback/TaskQuota";
import type { InvestigationRunProjection } from "../src/types/investigationCreation";
import "../src/styles/tokens.css";
import "../src/styles/global.css";
import "../src/styles/focus-users.css";
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
    <button onClick={() => setRun({ ...base, job_id: "fixture-job", status: "RUNNING", crawl_status: "running", analysis_status: "running", available_actions: { end_task: true, pause_crawl: true, pause_analysis: true, stop_analysis: true } })}>模拟运行状态</button>
    <button onClick={() => setRun({ ...base, status: "PUBLISHED", crawl_status: "completed", analysis_status: "partial", report_status: "published", available_actions: { ended: true } })}>模拟部分失败发布</button>
    <button onClick={() => setRun({ ...base, status: "PUBLISHED", crawl_status: "completed", analysis_status: "completed", report_status: "published", task_stats: { completed_analysis_count: 4, failed_analysis_count: 0 }, available_actions: { ended: true } })}>模拟正常发布</button>
    <button onClick={() => setRun({ ...base, status: "AUDIT_COMPLETED", crawl_status: "completed", analysis_status: "failed", report_status: "blocked_by_failed_posts", task_stats: { completed_analysis_count: 0, failed_analysis_count: 5 }, available_actions: { ended: true } })}>模拟全部失败</button>
    <AgentCollaborationCard phase="collection_working" authoritative run={run} investigationId="fixture" investigationTitle="验收任务"
      onControlAccepted={() => setRun(current => ({ ...current, available_actions: { ending: true } }))} />
  </main></MemoryRouter>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
