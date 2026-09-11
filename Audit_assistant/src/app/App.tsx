import { BrowserRouter } from "react-router-dom";
import { TopNavigation } from "../components/navigation/TopNavigation";
import { RuleAssistantWorkspaceProvider } from "../features/rule-assistant/RuleAssistantWorkspaceContext";
import { AppRouter } from "./router";
import { EnvironmentBoundary } from './EnvironmentBoundary';

export default function App() {
  return (
    <EnvironmentBoundary><BrowserRouter>
      <RuleAssistantWorkspaceProvider>
        <TopNavigation />
        <AppRouter />
      </RuleAssistantWorkspaceProvider>
    </BrowserRouter></EnvironmentBoundary>
  );
}
