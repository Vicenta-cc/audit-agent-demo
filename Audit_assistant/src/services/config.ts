import { apiRequest } from "./apiClient";

export interface RuntimeConfig {
  supported_platforms?: string[];
  default_keyword?: string;
  has_api_key?: boolean;
  activity_stream_enabled?: boolean;
  answer_stream_enabled?: boolean;
  creation_answer_stream_enabled?: boolean;
}

export function fetchRuntimeConfig() {
  return apiRequest<RuntimeConfig>("/api/config");
}
