import { apiRequest } from "./apiClient";

export interface RuntimeConfig {
  supported_platforms?: string[];
  default_keyword?: string;
  has_api_key?: boolean;
}

export function fetchRuntimeConfig() {
  return apiRequest<RuntimeConfig>("/api/config");
}
