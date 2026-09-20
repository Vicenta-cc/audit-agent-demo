import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";

// Import through Vite's normal module graph so router context is shared with
// the production components (direct prebundle URLs can create a second copy).
export function mountPresentation(host: HTMLElement, content: ReactNode) {
  createRoot(host).render(<MemoryRouter>{content}</MemoryRouter>);
}
