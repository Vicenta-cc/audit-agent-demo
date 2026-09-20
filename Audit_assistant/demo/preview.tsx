import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { InvestigationSidebar } from "../src/features/investigation/InvestigationSidebar";
import { InvestigationCenterArea } from "../src/features/investigation/InvestigationCenterArea";
import { mapInvestigationRunState } from "../src/features/investigation/investigationRunState";
import type { InvestigationRunProjection } from "../src/types/investigationCreation";
import type { InvestigationSession } from "../src/types/investigation";
import "../src/styles/tokens.css";
import "../src/styles/global.css";
import "../src/styles/focus-users.css";
import "../src/styles/monitor-tasks.css";
import "../src/styles/create-task.css";
import "../src/styles/investigation-workspace.css";
import "./preview.css";

type Scene = "completed" | "running" | "paused" | "queued" | "partial" | "failed" | "ended";
const labels: Record<Scene, string> = {
  completed: "正常完成", running: "采集中", paused: "已暂停", queued: "排队中",
  partial: "部分审核失败", failed: "全部失败", ended: "已结束"
};
const stamp = "2026-09-20T15:40:00+08:00";
let serial = 0;
let quotaUsed = 1;
let command: (action: string) => void = () => {};
// All fetches on this preview page are handled in memory, including unknown APIs.
window.fetch = async (input, init) => {
  const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, location.origin);
  let data: unknown;
  if (url.pathname === "/api/me/task-quota") {
    data = { enabled: true, used: quotaUsed, remaining: 3 - quotaUsed, limit: 3, reset_at: "2026-09-21T00:00:00+08:00", legacy_record_count: 0 };
  } else if (/^\/api\/tasks\/demo-run-\d+\/cancel$/.test(url.pathname)) {
    command("end_task");
    data = { state: "RESERVED", decision: "CANCELLED" };
  } else if (url.pathname === "/api/jobs/demo-job/control") {
    command(JSON.parse(String(init?.body || "{}")).action);
    data = { ok: true };
  } else {
    return new Response(JSON.stringify({ message: "此功能不在假数据预览范围内" }), { status: 404, headers: { "Content-Type": "application/json" } });
  }
  return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
};

function makeRun(scene: Scene): InvestigationRunProjection {
  const run: InvestigationRunProjection = {
    run_id: `demo-run-${++serial}`, draft_id: "demo-draft", draft_revision: 1, job_id: "demo-job",
    status: "RUNNING", crawl_status: "running", analysis_status: "running", report_status: "pending",
    report_version_id: "", error_code: "", error_message: "", created_at: stamp, updated_at: stamp,
    started_at: stamp, completed_at: "",
    task_stats: { ingested_count: 6, completed_analysis_count: 4, failed_analysis_count: 1, pending_analysis_count: 1, queued_analysis_count: 0, analyzing_count: 1 },
    audit_results: [],
    available_actions: { end_task: true, pause_crawl: true, pause_analysis: true, stop_analysis: true },
    logs: [{ time: stamp, stage: "collection", level: "info", message: "演示数据：任务已接受并计次，已采集 6 条帖子。" }]
  };
  if (scene === "paused") {
    run.crawl_status = "paused"; run.analysis_status = "paused";
    run.available_actions = { end_task: true, resume_crawl: true, resume_analysis: true };
  }
  if (scene === "queued") {
    run.status = "QUEUED"; run.job_id = ""; run.crawl_status = "queued"; run.analysis_status = "pending";
    run.task_stats = {}; run.available_actions = { end_task: true }; run.logs = [];
  }
  if (scene === "partial" || scene === "failed") {
    run.status = "AUDIT_COMPLETED"; run.crawl_status = "completed";
    run.analysis_status = scene === "partial" ? "partial" : "failed";
    run.report_status = scene === "partial" ? "published" : "blocked_by_failed_posts";
    run.completed_at = stamp; run.available_actions = { ended: true };
    run.task_stats = { ingested_count: 5, completed_analysis_count: scene === "partial" ? 4 : 0, failed_analysis_count: scene === "partial" ? 1 : 5, pending_analysis_count: 0, queued_analysis_count: 0, analyzing_count: 0 };
  }
  if (scene === "completed") {
    run.status = "PUBLISHED"; run.crawl_status = "completed"; run.analysis_status = "completed"; run.report_status = "published"; run.available_actions = { ended: true };
    run.task_stats = { ingested_count: 4, completed_analysis_count: 4, failed_analysis_count: 0, queued_analysis_count: 0, analyzing_count: 0 };
  }
  if (scene === "partial") {
    run.status = "PUBLISHED";
    run.audit_results = Array.from({ length: 4 }, (_, i) => ({
      audit_result_id: `demo-result-${i}`, content_key: `demo-post-${i}`, platform: "dy",
      content_title: ["新品体验分享", "售后沟通记录", "产品功能讨论", "使用体验反馈"][i],
      author_display_name: `演示作者 ${i + 1}`, decision: "pass", risk_level: "low",
      summary: "假数据：本条内容已完成审核，未发现明显风险。", analyzed_at: stamp, evidence: []
    }));
  }
  if (scene === "ended") {
    run.status = "FAILED"; run.crawl_status = "stopped"; run.analysis_status = "stopped";
    run.report_status = "cancelled"; run.error_code = "cancelled"; run.error_message = "任务已终止。";
    run.available_actions = { ended: true }; run.completed_at = stamp;
  }
  return run;
}

function Preview() {
  const [run, setRun] = useState(() => makeRun("running"));
  const [collapsed, setCollapsed] = useState(false);
  const [notice, setNotice] = useState("看左下角的额度，以及流水线内的任务控制；可点击暂停、继续、结束任务。");
  const [deleted, setDeleted] = useState(false);
  const [used, setUsed] = useState(1);
  const [title, setTitle] = useState("新品口碑调查 · 演示");
  useEffect(() => {
    command = (action) => {
      if (action === "end_task") {
        setRun(current => ({ ...current, available_actions: { ending: true } }));
        return;
      }
      setRun(current => {
        const next = { ...current, available_actions: { ...current.available_actions } };
        if (action.endsWith("crawl")) {
          const paused = action === "pause_crawl";
          next.crawl_status = paused ? "paused" : "running";
          next.available_actions.pause_crawl = !paused; next.available_actions.resume_crawl = paused;
        } else {
          const paused = action !== "resume_analysis";
          next.analysis_status = action === "stop_analysis" ? "stopped" : paused ? "paused" : "running";
          next.available_actions.pause_analysis = !paused; next.available_actions.stop_analysis = !paused;
          next.available_actions.resume_analysis = paused;
        }
        return next;
      });
      setNotice("演示操作已生效。暂停、继续都沿用当前额度，左下角次数不变。");
    };
    return () => { command = () => {}; };
  }, []);
  useEffect(() => {
    if (!run.available_actions?.ending) return;
    const timer = setTimeout(() => {
      setRun(current => ({ ...current, status: "FAILED", crawl_status: "stopped", analysis_status: "stopped", report_status: "cancelled", error_code: "cancelled", error_message: "任务已终止。", available_actions: { ended: true } }));
      setNotice("模拟后台已停止：次数未退还。可以从侧栏会话的更多菜单删除会话。");
    }, 2500);
    return () => clearTimeout(timer);
  }, [run.available_actions?.ending, run.run_id]);
  const session: InvestigationSession = {
    id: "demo-session", title, status: run.available_actions?.ended ? "审核完成" : "研判中", updatedAt: "刚刚",
    executionPhase: mapInvestigationRunState(run).phase, executionProgress: 50,
    draft: { taskName: title, taskType: "关键词调查", subject: "新品口碑", platforms: ["dy"], keywords: ["新品体验", "产品口碑"], matchedRuleSet: "内容风险审核", ruleSetDescription: "演示审核规则", scopeDescription: "抖音公开帖子", status: "研判中", confirmed: true },
    messages: [
      { id: "demo-question", sender: "user", type: "text", timestamp: "15:40", content: "帮我调查这款新品的用户反馈，看看有没有需要关注的内容。" },
      { id: `demo-card-${run.run_id}`, sender: "assistant", type: "agent_collaboration", timestamp: "15:40" }
    ],
    creationBinding: { workspaceSessionId: "demo-session", run }
  };
  const demoOnly = () => setNotice("本预览演示额度和流水线控制；其他入口未接入业务服务。");
  return <MemoryRouter><div className="demo-shell">
    <header className="demo-toolbar">
      <div><strong>假数据预览 · 当前候选前端</strong><span>使用正式组件的紧凑布局与状态颜色；此工具栏不属于正式页面。</span></div>
      <nav aria-label="演示场景">{(Object.keys(labels) as Scene[]).map(scene => <button key={scene} onClick={() => { setDeleted(false); setRun(makeRun(scene)); setNotice(`正在查看「${labels[scene]}」假数据。`); }}>{labels[scene]}</button>)}
        <button onClick={() => { quotaUsed = used === 1 ? 3 : 1; setUsed(quotaUsed); window.dispatchEvent(new Event("task-quota-changed")); }}>额度：{used}/3（切换）</button>
      </nav>
      <p role="status">{notice}</p>
    </header>
    <div className="inv-workspace-root">
      <InvestigationSidebar sessions={deleted ? [] : [session]} activeSessionId={session.id}
        isCollapsed={collapsed} onToggleCollapse={() => setCollapsed(!collapsed)} onSelectSession={() => {}}
        onNewInvestigation={demoOnly} onSelectSubView={demoOnly} onRenameSession={(_, value) => setTitle(value)}
        onDeleteSession={() => {
          if (!run.available_actions?.ended) { setNotice("模拟删除检查：任务尚未结束，暂停也不能直接删除。请先结束整个任务并等待停止。"); return; }
          setDeleted(true); setNotice("演示会话已删除，今日额度不变。可用顶部按钮重新载入假数据。");
        }} />
      {deleted ? <main className="inv-empty-workspace"><h2>暂无调查会话</h2><p>已删除演示会话，扣次记录保留。</p></main> :
        <InvestigationCenterArea key={run.run_id} session={session} isSendingMessage={false}
          isSidebarCollapsed={collapsed} onToggleSidebar={() => setCollapsed(false)}
          onUpdateDraftKeywords={demoOnly} onUpdateCreationSearchTerms={async () => demoOnly()}
          onRunControlAccepted={() => {}} onUpdateDraftPlatforms={demoOnly} onGenerateTaskConfig={demoOnly}
          onStartAgentExecution={demoOnly} onPhaseChange={() => {}} onSendMessage={demoOnly}
          onOpenDrawer={demoOnly} onOpenReportSupport={demoOnly} onExamplePromptSelect={demoOnly} />}
    </div>
  </div></MemoryRouter>;
}
createRoot(document.getElementById("root")!).render(<Preview />);
