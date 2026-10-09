import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";

// Import through Vite's normal module graph so router context is shared with
// the production components (direct prebundle URLs can create a second copy).
export function mountPresentation(host: HTMLElement, content: ReactNode) {
  createRoot(host).render(<MemoryRouter>{content}</MemoryRouter>);
}

// Exercise production workspace routing with a real router in browser tests.
export async function mountInvestigationWorkspace(host: HTMLElement, sessionId: string) {
  const { Routes, Route } = await import('react-router-dom');
  const { InvestigationPage } = await import('../src/features/investigation/InvestigationPage');
  createRoot(host).render(<MemoryRouter initialEntries={[`/investigation/${encodeURIComponent(sessionId)}`]}>
    <Routes><Route path="/investigation/:investigationId" element={<InvestigationPage />} /></Routes>
  </MemoryRouter>);
}
