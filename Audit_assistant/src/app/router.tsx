import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { AdminUsersPage } from "../features/auth/AdminUsersPage";
import { ConfigCenterPage } from "../features/config-center/ConfigCenterPage";
import { CrawlerAccountsPage } from "../features/crawler-accounts/CrawlerAccountsPage";
import { FocusUsersPage } from "../features/focus-users/FocusUsersPage";
import { MonitorTasksPage } from "../features/monitor-tasks/MonitorTasksPage";
import { CreateTaskPage } from "../features/monitor-tasks/create-task/CreateTaskPage";
import { AnalysisRecordsPage } from "../features/investigation/AnalysisRecordsPage";
import { InvestigationEvidenceAppendixPage } from "../features/investigation/InvestigationEvidenceAppendixPage";
import { InvestigationPage } from "../features/investigation/InvestigationPage";
import { InvestigationReportPage } from "../features/investigation/InvestigationReportPage";
import { RiskWorkbenchPage } from "../features/risk-workbench/RiskWorkbenchPage";
import { RuleCandidatePreviewPage } from "../features/rule-assistant/RuleCandidatePreviewPage";
import { LexiconCandidatePreviewPage } from "../features/rule-assistant/LexiconCandidatePreviewPage";
import { TaskOutputDetailPage } from "../features/task-outputs/TaskOutputDetailPage";
import { TaskOutputsPage } from "../features/task-outputs/TaskOutputsPage";

export const navigationItems = [
  { label: "智能调查工作区", path: "/investigation" },
  { label: "风险研判", path: "/risk" },
  { label: "监控任务", path: "/tasks" },
  { label: "重点用户", path: "/users" },
  { label: "采集账号", path: "/crawler-accounts" },
  { label: "审核规则", path: "/investigation?view=audit-rules" },
  { label: "黑话库", path: "/investigation?view=slang-library" },
  { label: "配置底座", path: "/config" }
] as const;

function RuleAssistantStructurePage() {
  const { ruleSetId } = useParams();
  return <Navigate to={`/investigation?view=audit-rules&ruleSetId=${encodeURIComponent(ruleSetId || "ruleset-erotic")}`} replace />;
}

export function AppRouter() {
  return (
    <Routes>
      <Route path="/admin/users" element={<AdminUsersPage />} />
      <Route path="/" element={<Navigate to="/investigation" replace />} />
      <Route path="/investigation" element={<InvestigationPage />} />
      <Route path="/investigation/:investigationId" element={<InvestigationPage />} />
      <Route path="/investigation/:investigationId/report" element={<InvestigationReportPage />} />
      <Route path="/investigation/:investigationId/report/evidence" element={<InvestigationEvidenceAppendixPage />} />
      <Route path="/investigation/:investigationId/analysis-records" element={<AnalysisRecordsPage />} />
      <Route path="/knowledge-center" element={<Navigate to="/investigation?view=audit-rules" replace />} />
      <Route path="/rule-assistant" element={<Navigate to="/investigation" replace />} />
      <Route path="/rule-assistant/rulesets" element={<Navigate to="/investigation?view=audit-rules" replace />} />
      <Route path="/rule-assistant/lexicons" element={<Navigate to="/investigation?view=slang-library" replace />} />
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
