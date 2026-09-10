import { Navigate, Route, Routes } from "react-router-dom";
import { ConfigCenterPage } from "../features/config-center/ConfigCenterPage";
import { CrawlerAccountsPage } from "../features/crawler-accounts/CrawlerAccountsPage";
import { FocusUsersPage } from "../features/focus-users/FocusUsersPage";
import { MonitorTasksPage } from "../features/monitor-tasks/MonitorTasksPage";
import { CreateTaskPage } from "../features/monitor-tasks/create-task/CreateTaskPage";
import { RiskWorkbenchPage } from "../features/risk-workbench/RiskWorkbenchPage";
import { TaskOutputDetailPage } from "../features/task-outputs/TaskOutputDetailPage";
import { TaskOutputsPage } from "../features/task-outputs/TaskOutputsPage";

export const navigationItems = [
  { label: "风险研判", path: "/risk" },
  { label: "监控任务", path: "/tasks" },
  { label: "重点用户", path: "/users" },
  { label: "采集账号", path: "/crawler-accounts" },
  { label: "配置底座", path: "/config" }
] as const;

export function AppRouter() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/tasks" replace />} />
      <Route path="/tasks" element={<MonitorTasksPage />} />
      <Route path="/tasks/new" element={<CreateTaskPage />} />
      <Route path="/tasks/:taskId/outputs" element={<TaskOutputsPage />} />
      <Route path="/tasks/:taskId/outputs/:outputId" element={<TaskOutputDetailPage />} />
      <Route path="/users" element={<FocusUsersPage />} />
      <Route path="/crawler-accounts" element={<CrawlerAccountsPage />} />
      <Route path="/risk" element={<RiskWorkbenchPage />} />
      <Route path="/config/*" element={<ConfigCenterPage />} />
      <Route path="*" element={<Navigate to="/tasks" replace />} />
    </Routes>
  );
}
