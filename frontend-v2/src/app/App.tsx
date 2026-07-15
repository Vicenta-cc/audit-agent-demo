import { BrowserRouter } from "react-router-dom";
import { TopNavigation } from "../components/navigation/TopNavigation";
import { AppRouter } from "./router";

export default function App() {
  return (
    <BrowserRouter basename="/saas-v2">
      <TopNavigation />
      <AppRouter />
    </BrowserRouter>
  );
}
