import { Navigate, Route, Routes } from "react-router-dom";
import { PlaceholderPage } from "../components/layout/PlaceholderPage";
import { ConfigCenterPage } from "../features/config-center/ConfigCenterPage";
import { FocusUsersPage } from "../features/focus-users/FocusUsersPage";
import { MonitorTasksPage } from "../features/monitor-tasks/MonitorTasksPage";
import { TaskOutputsPage } from "../features/task-outputs/TaskOutputsPage";

export const navigationItems = [
  { label: "风险研判", path: "/risk" },
  { label: "监控任务", path: "/tasks" },
  { label: "重点用户", path: "/users" },
  { label: "配置底座", path: "/config" }
] as const;

export function AppRouter() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/tasks" replace />} />
      <Route path="/tasks" element={<MonitorTasksPage />} />
      <Route path="/tasks/:taskId/outputs" element={<TaskOutputsPage />} />
      <Route path="/tasks/:taskId/outputs/:outputId" element={<PlaceholderPage title="风险证据详情页建设中" />} />
      <Route path="/users" element={<FocusUsersPage />} />
      <Route path="/risk" element={<PlaceholderPage title="风险研判页面建设中" />} />
      <Route path="/config/*" element={<ConfigCenterPage />} />
      <Route path="*" element={<Navigate to="/tasks" replace />} />
    </Routes>
  );
}
