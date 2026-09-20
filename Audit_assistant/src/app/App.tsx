import { BrowserRouter } from "react-router-dom";
import { TopNavigation } from "../components/navigation/TopNavigation";
import { RuleAssistantWorkspaceProvider } from "../features/rule-assistant/RuleAssistantWorkspaceContext";
import { AppRouter } from "./router";
import { EnvironmentBoundary } from './EnvironmentBoundary';
import { AuthBoundary } from "../features/auth/AuthBoundary";

export default function App() {
  return (
    <BrowserRouter><AuthBoundary><EnvironmentBoundary>
      <RuleAssistantWorkspaceProvider>
        <TopNavigation />
        <AppRouter />
      </RuleAssistantWorkspaceProvider>
    </EnvironmentBoundary></AuthBoundary></BrowserRouter>
  );
}
