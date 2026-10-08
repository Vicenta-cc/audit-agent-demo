import { apiRequest } from './apiClient';
import type { RulesContent, LexiconContent } from './resourceLibrary';

export type ResourceKind = 'ruleset' | 'lexicon';
export type SelectionPurpose = 'edit' | 'view';
export interface SessionResource {
  key: string; type: string; kind?: ResourceKind; title?: string; version?: number;
  content_hash?: string; is_current?: boolean;
}
export interface ResourceSelection {
  event_id: string; kind: ResourceKind; purpose: SelectionPurpose;
  status: 'selected' | 'stale' | 'unavailable' | 'cleared';
  key?: string; title?: string; version?: number;
}
export interface ResourceState {
  status: 'complete' | 'partial'; items: SessionResource[]; next_cursor: string;
  selection: { status: 'unknown' | 'recorded'; slots?: ResourceSelection[] };
}
export interface SelectionRequest {
  event_id: string; expected_event_id: string; kind: ResourceKind;
  purpose: SelectionPurpose; key: string; content_hash: string;
}
const root = (session: string) => `/api/investigation-workspaces/${encodeURIComponent(session)}`;
export async function loadSessionResources(session: string): Promise<ResourceState> {
  let cursor = '';
  const items: SessionResource[] = [];
  do {
    // A stale cursor is an error, not permission to mix pages from two snapshots.
    const page = await apiRequest<ResourceState>(`${root(session)}/resource-state?limit=100&cursor=${encodeURIComponent(cursor)}`);
    items.push(...page.items);
    cursor = page.next_cursor;
    if (!cursor) return { ...page, items };
  } while (cursor);
  throw new Error('资源目录未能完整读取。');
}
export const recordResourceSelection = (session: string, body: SelectionRequest) =>
  apiRequest(`${root(session)}/resource-selection`, { method: 'POST', body: JSON.stringify(body) });
export const readSessionResource = (session: string, key: string) =>
  apiRequest<SessionResource & { content: RulesContent | LexiconContent }>(
    `${root(session)}/resource-state/detail?key=${encodeURIComponent(key)}`);
