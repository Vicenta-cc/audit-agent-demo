import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { AuthBoundary } from "../src/features/auth/AuthBoundary";
import { AdminUsersPage } from "../src/features/auth/AdminUsersPage";
import { clearAuthenticationState } from "../src/services/apiClient";
import { InvestigationAccountTools } from "../src/features/investigation/InvestigationAccountTools";
import { InvestigationSidebar } from "../src/features/investigation/InvestigationSidebar";
import { InvestigationCenterArea } from "../src/features/investigation/InvestigationCenterArea";
import type { InvestigationSession } from "../src/types/investigation";
import type { InvestigationRunProjection } from "../src/types/investigationCreation";
import "../src/styles/tokens.css";
import "../src/styles/global.css";
import "../src/styles/focus-users.css";
import "../src/styles/monitor-tasks.css";
import "../src/styles/create-task.css";
import "../src/styles/investigation-workspace.css";
import "./preview.css";
import "./accounts.css";

type Scene = "workspace" | "login" | "expired" | "admin";
const names: Record<Scene, string> = { workspace: "用户工作区", login: "登录页", expired: "账号到期", admin: "管理员页面" };
const user = (id: string, username: string, role: "user" | "admin" = "user", days = 7) => ({
  id, username, role, status: "active", activation_mode: "first_login", created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(), validity_started_at: new Date().toISOString(),
  expires_at: role === "admin" ? null : new Date(Date.now() + days * 86400000).toISOString()
});
let scene: Scene = "workspace";
let current: ReturnType<typeof user> | null = user("preview-user", "体验用户");
let users = [user("preview-admin", "演示管理员", "admin", 30), current!, {...user("preview-new", "待激活用户"),expires_at:"",validity_started_at:""}, user("preview-expired", "已到期用户", "user", -1)];
const crawlerAccounts = [{id:"preview-crawler-a",display_name:"采集账号 A · 演示",platform:"dy",status:"active"}, {id:"preview-crawler-b",display_name:"采集账号 B · 演示",platform:"dy",status:"active"}];
let active = true;
let control: (action:string)=>void = () => {};
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {status,headers:{"Content-Type":"application/json"}});
// This entry serves every fetch in memory. No request falls through to a backend.
window.fetch = async (input, init) => {
  const path = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, location.origin).pathname;
  const method = init?.method || "GET";
  const body = init?.body ? JSON.parse(String(init.body)) : {};
  if(path === "/api/auth/config") return json({enabled:true});
  if(path === "/api/auth/login") {
    if(scene === "expired") return json({detail:{code:"ACCOUNT_EXPIRED",message:"演示账号已到期"}},403);
    current = user("preview-user",body.username || "体验用户");
    return json({user:current,csrf_token:"preview-only"});
  }
  if(!current) return json({detail:{code:scene === "expired" ? "ACCOUNT_EXPIRED" : "AUTHENTICATION_REQUIRED"}},401);
  if(path === "/api/auth/me") return json({user:current});
  if(path === "/api/auth/csrf") return json({csrf_token:"preview-only"});
  if(path === "/api/auth/logout") {current=null;return new Response(null,{status:204});}
  if(path === "/api/me/task-quota") return json({enabled:true,used:1,remaining:2,limit:3,legacy_record_count:0,
    active_task:active ? {task_id:"preview-run",job_id:"preview-job",kind:"investigation",queue_state:"QUEUED",decision:"OPEN",waiting_reason:"account_busy"} : null});
  if(path === "/api/investigation-workspaces") return json({items:[{workspace_session_id:"preview-session",run_id:"preview-run"}],has_more:false});
  if(path === "/api/crawler-accounts") return json({items:crawlerAccounts});
  if(path === "/api/admin/users") {
    if(method === "POST") {
      if(users.some(u=>u.username === body.username)) return json({detail:"该演示账号名称已存在。"},409);
      users=[...users,{...user(`preview-${crypto.randomUUID()}`,body.username),expires_at:"",validity_started_at:""}];
    }
    return json({items:users});
  }
  const parts=path.split("/");
  if(path.startsWith("/api/admin/users/")) {
    const id=decodeURIComponent(parts[4]);
    if(method === "PATCH") {
      users=users.map(u=>u.id!==id ? u : {...u,status:body.status||u.status,expires_at:body.renew_days ? new Date(Math.max(Date.now(),Date.parse(u.expires_at || "")||0)+body.renew_days*86400000).toISOString() : u.expires_at});
      return json({user:users.find(u=>u.id===id)});
    }
  }
  if(path === "/api/tasks/preview-run/cancel") {control("end");active=false;return json({state:"RELEASED",decision:"CANCELLED"});}
  return json({detail:"此入口未接入演示，不会访问真实服务。"},404);
};

const makeRun = (): InvestigationRunProjection => ({
  run_id:"preview-run",draft_id:"preview-draft",draft_revision:1,job_id:"",status:"QUEUED",crawl_status:"queued",analysis_status:"pending",report_status:"pending",report_version_id:"",error_code:"",error_message:"",created_at:"",updated_at:"",started_at:"",completed_at:"",task_stats:{},audit_results:[],available_actions:{end_task:true}
});
function Workspace() {
  const [collapsed,setCollapsed]=useState(false);
  const [run,setRun]=useState(makeRun);
  const [notice,setNotice]=useState("");
  const [deleted,setDeleted]=useState(false);
  control=()=>setRun(r=>({...r,status:"FAILED",crawl_status:"stopped",analysis_status:"stopped",report_status:"cancelled",error_code:"cancelled",available_actions:{ended:true}}));
  const onlyPreview=()=>setNotice("这里是外观预览，未接入真实业务；可从顶部切换页面。");
  const session:InvestigationSession={
    id:"preview-session",title:"新品口碑调查 · 演示",status:active ? "研判中" : "审核完成",updatedAt:"刚刚",executionPhase:"collection_working",executionProgress:0,
    draft:{taskName:"新品口碑调查",taskType:"关键词调查",subject:"新品口碑",platforms:["dy"],keywords:["新品体验","产品口碑"],matchedRuleSet:"内容风险审核",ruleSetDescription:"演示审核规则",status:"研判中",confirmed:true},
    messages:[{id:"preview-question",sender:"user",type:"text",timestamp:"刚刚",content:"帮我调查这款新品的用户反馈，看看有没有需要关注的内容。"},{id:"preview-task",sender:"assistant",type:"agent_collaboration",timestamp:"刚刚"}],creationBinding:{workspaceSessionId:"preview-session",run}
  };
  return <div className="inv-workspace-root">
    <InvestigationSidebar sessions={deleted?[]:[session]} activeSessionId={session.id} isCollapsed={collapsed} onToggleCollapse={()=>setCollapsed(!collapsed)} onSelectSession={()=>{}} onNewInvestigation={onlyPreview} onSelectSubView={onlyPreview} onDeleteSession={()=>{if(active)onlyPreview();else setDeleted(true);}} />
    {notice ? <div role="status" className="inv-deletion-notice"><span>{notice}</span><button onClick={()=>setNotice("")}>关闭</button></div>:null}
    {deleted ? <main className="inv-center-area"><header className="inv-center-header"><span>调查工作区</span><InvestigationAccountTools /></header><div className="inv-empty-workspace"><h2>暂无调查会话</h2><p>删除不返还已扣次数。</p></div></main> : <InvestigationCenterArea session={session} isSendingMessage={false} isSidebarCollapsed={collapsed} onToggleSidebar={()=>setCollapsed(false)} onUpdateDraftKeywords={onlyPreview} onUpdateCreationSearchTerms={async()=>onlyPreview()} onRunControlAccepted={()=>{}} onUpdateDraftPlatforms={onlyPreview} onGenerateTaskConfig={onlyPreview} onStartAgentExecution={onlyPreview} onPhaseChange={()=>{}} onSendMessage={onlyPreview} onOpenDrawer={onlyPreview} onOpenReportSupport={onlyPreview} onExamplePromptSelect={onlyPreview} />}
  </div>;
}
function Preview() {
  const [selected,setSelected]=useState<Scene>("workspace");
  const [revision,setRevision]=useState(0);
  const select=(next:Scene)=>{
    scene=next;active=true;clearAuthenticationState();
    current=next==="workspace" ? user("preview-user","体验用户") : next==="admin" ? user("preview-admin","演示管理员","admin",30) : null;
    setSelected(next);setRevision(v=>v+1);
  };
  return <div className="phase5-demo">
    <header className="demo-toolbar">
      <div><strong>阶段五 · 假数据预览</strong><span>顶部切换条仅用于演示；下方复用当前前端组件。</span></div>
      <nav aria-label="页面预览">{(Object.keys(names) as Scene[]).map(key=><button key={key} aria-pressed={selected===key} onClick={()=>select(key)}>{names[key]}</button>)}<a href="/demo/">查看流水线状态演示</a></nav>
      <p>{selected==="login" ? "可输入演示账号 demo、任意非空演示密码体验登录，请勿输入真实密码。" : selected==="expired" ? "演示账号到期后回到登录入口；可输入 demo 和任意演示密码查看到期提示。" : selected==="admin" ? "可以试点开通、续期、禁用和密码设置；所有操作仅修改本页内存假数据。" : "账号与额度已移至对话顶部；点击剩余次数查看等待原因及当前任务，点击用户名查看有效期与退出。"}</p>
    </header>
    <section className="phase5-demo-body" key={revision}><MemoryRouter initialEntries={[selected==="admin"?"/admin/users":"/investigation"]}><AuthBoundary><Routes><Route path="/admin/users" element={<AdminUsersPage/>}/><Route path="*" element={<Workspace/>}/></Routes></AuthBoundary></MemoryRouter></section>
  </div>;
}
clearAuthenticationState();
createRoot(document.getElementById("root")!).render(<Preview/>);
