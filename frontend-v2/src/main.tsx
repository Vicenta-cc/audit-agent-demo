import React from "react";
import ReactDOM from "react-dom/client";
import App from "./app/App";
import "./styles/tokens.css";
import "./styles/global.css";
import "./styles/focus-users.css";
import "./styles/monitor-tasks.css";
import "./styles/create-task.css";
import "./styles/risk-workbench.css";
import "./styles/task-outputs.css";
import "./styles/config-center.css";
import "./styles/crawler-accounts.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
