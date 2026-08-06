import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { ConfigCenterPage } from "../features/config-center/ConfigCenterPage";
import { CrawlerAccountsPage } from "../features/crawler-accounts/CrawlerAccountsPage";
import { FocusUsersPage } from "../features/focus-users/FocusUsersPage";
import { MonitorTasksPage } from "../features/monitor-tasks/MonitorTasksPage";
import { CreateTaskPage } from "../features/monitor-tasks/create-task/CreateTaskPage";
import { AnalysisRecordsPage } from "../features/investigation/AnalysisRecordsPage";
import { InvestigationEvidenceAppendixPage } from "../features/investigation/InvestigationEvidenceAppendixPage";
import { InvestigationPage } from "../features/investigation/InvestigationPage";
import { InvestigationReportPage } from "../features/investigation/InvestigationReportPage";
import { KnowledgeCenterPage } from "../pages/KnowledgeCenterPage";
import { RiskWorkbenchPage } from "../features/risk-workbench/RiskWorkbenchPage";
import { RuleAssistantPage } from "../features/rule-assistant/RuleAssistantPage";
import { RuleCandidatePreviewPage } from "../features/rule-assistant/RuleCandidatePreviewPage";
import { LexiconCandidatePreviewPage } from "../features/rule-assistant/LexiconCandidatePreviewPage";
import { useRuleAssistantWorkspace } from "../features/rule-assistant/RuleAssistantWorkspaceContext";
import type { RuleAssistantRouteState } from "../features/rule-assistant/types";
import { TaskOutputDetailPage } from "../features/task-outputs/TaskOutputDetailPage";
import { TaskOutputsPage } from "../features/task-outputs/TaskOutputsPage";

export const navigationItems = [
  { label: "智能调查工作区", path: "/investigation" },
  { label: "风险研判", path: "/risk" },
  { label: "监控任务", path: "/tasks" },
  { label: "重点用户", path: "/users" },
  { label: "采集账号", path: "/crawler-accounts" },
  { label: "知识库资源", path: "/knowledge-center" },
  { label: "配置底座", path: "/config" }
] as const;

function RuleAssistantStructurePage() {
  const { ruleSetId } = useParams();
  const navigate = useNavigate();
  const { openRuleSet } = useRuleAssistantWorkspace();
  const selectedRuleSetId = ruleSetId ? decodeURIComponent(ruleSetId) : "ruleset-erotic";

  return (
    <KnowledgeCenterPage
      initialRuleSetId={selectedRuleSetId}
      backLabel="返回规则对话"
      onBack={() => navigate("/rule-assistant")}
      onOpenRuleAssistant={(targetRuleSetId) => {
        openRuleSet(targetRuleSetId);
        navigate("/rule-assistant");
      }}
    />
  );
}

function RuleAssistantResourcePage({ initialTab }: { initialTab: "rulesets" | "recall" }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { openRuleSet } = useRuleAssistantWorkspace();
  const routeState = (location.state || {}) as RuleAssistantRouteState;

  return (
    <KnowledgeCenterPage
      initialTab={initialTab}
      initialRuleSetId={routeState.entryRuleSetId}
      backLabel="返回知识助手"
      onBack={() => navigate("/rule-assistant")}
      onOpenRuleAssistant={(targetRuleSetId) => {
        openRuleSet(targetRuleSetId);
        navigate("/rule-assistant");
      }}
    />
  );
}

export function AppRouter() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/investigation" replace />} />
      <Route path="/investigation" element={<InvestigationPage />} />
      <Route path="/investigation/:investigationId" element={<InvestigationPage />} />
      <Route path="/investigation/:investigationId/report" element={<InvestigationReportPage />} />
      <Route path="/investigation/:investigationId/report/evidence" element={<InvestigationEvidenceAppendixPage />} />
      <Route path="/investigation/:investigationId/analysis-records" element={<AnalysisRecordsPage />} />
      <Route path="/knowledge-center" element={<Navigate to="/rule-assistant" replace />} />
      <Route path="/rule-assistant" element={<RuleAssistantPage />} />
      <Route path="/rule-assistant/rulesets" element={<RuleAssistantResourcePage initialTab="rulesets" />} />
      <Route path="/rule-assistant/lexicons" element={<RuleAssistantResourcePage initialTab="recall" />} />
      <Route path="/rule-assistant/import-preview/:previewId" element={<RuleCandidatePreviewPage />} />
      <Route path="/rule-assistant/lexicon-preview/:conversationId" element={<LexiconCandidatePreviewPage />} />
      <Route path="/rule-assistant/structure/:ruleSetId" element={<RuleAssistantStructurePage />} />
      <Route path="/tasks" element={<MonitorTasksPage />} />
      <Route path="/tasks/new" element={<CreateTaskPage />} />
      <Route path="/tasks/chat-new" element={<InvestigationPage />} />
      <Route path="/tasks/chat" element={<InvestigationPage />} />
      <Route path="/tasks/:taskId/outputs" element={<TaskOutputsPage />} />
      <Route path="/tasks/:taskId/outputs/:outputId" element={<TaskOutputDetailPage />} />
      <Route path="/users" element={<InvestigationPage initialSubView="users" />} />
      <Route path="/crawler-accounts" element={<InvestigationPage initialSubView="crawler-accounts" />} />
      <Route path="/risk" element={<RiskWorkbenchPage />} />
      <Route path="/config/*" element={<ConfigCenterPage />} />
      <Route path="*" element={<Navigate to="/investigation" replace />} />
    </Routes>
  );
}
