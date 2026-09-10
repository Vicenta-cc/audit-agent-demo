import { apiRequest } from "./apiClient";

export interface LexiconCategory {
  id: string;
  title?: string;
  risk_label?: string;
  keywords?: Array<{ keyword: string; match_type?: string }>;
}

export function fetchLexicons() {
  return apiRequest<{ categories?: LexiconCategory[] } | LexiconCategory[]>("/api/lexicons");
}
