import { BrowserRouter } from "react-router-dom";
import { TopNavigation } from "../components/navigation/TopNavigation";
import { RuleAssistantWorkspaceProvider } from "../features/rule-assistant/RuleAssistantWorkspaceContext";
import { AppRouter } from "./router";

export default function App() {
  return (
    <BrowserRouter>
      <RuleAssistantWorkspaceProvider>
        <TopNavigation />
        <AppRouter />
      </RuleAssistantWorkspaceProvider>
    </BrowserRouter>
  );
}
