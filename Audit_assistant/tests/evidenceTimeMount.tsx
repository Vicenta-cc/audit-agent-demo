import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { TaskOutputDetailPage } from "../src/features/task-outputs/TaskOutputDetailPage";

export function mountEvidenceTime(host: HTMLElement) {
  createRoot(host).render(
    <MemoryRouter initialEntries={["/tasks/time-preview/123"]}>
      <Routes><Route path="/tasks/:taskId/:outputId" element={<TaskOutputDetailPage />} /></Routes>
    </MemoryRouter>
  );
}
