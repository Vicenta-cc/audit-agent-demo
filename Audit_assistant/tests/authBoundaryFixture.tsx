import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route, useLocation } from "react-router-dom";
import { AuthBoundary, AccountMenu, useApplicationAuth } from "../src/features/auth/AuthBoundary";
import { AdminUsersPage } from "../src/features/auth/AdminUsersPage";
import { TaskQuota } from "../src/components/feedback/TaskQuota";
import { RuleAssistantWorkspaceProvider, useRuleAssistantWorkspace } from "../src/features/rule-assistant/RuleAssistantWorkspaceContext";
import "../src/styles/tokens.css";
import "../src/styles/global.css";
import "../src/styles/focus-users.css";
import "../src/styles/monitor-tasks.css";
function Workspace() {
  const { user } = useApplicationAuth();
  const rules = useRuleAssistantWorkspace();
  const location = useLocation();
  return <main style={{maxWidth: 850, margin: "24px auto"}}><h1>用户工作区</h1><output data-testid="rule-seed-count">{rules.conversations.length + rules.ruleSets.length + rules.lexicons.length}</output><AccountMenu /><p data-testid="owner">{user?.id || "legacy"}</p><p data-testid="path">{location.pathname}</p><TaskQuota /></main>;
}
createRoot(document.getElementById("root")!).render(<React.StrictMode><BrowserRouter><AuthBoundary><RuleAssistantWorkspaceProvider><Routes>
  <Route path="/admin/users" element={<AdminUsersPage />} /><Route path="*" element={<Workspace />} />
</Routes></RuleAssistantWorkspaceProvider></AuthBoundary></BrowserRouter></React.StrictMode>);
