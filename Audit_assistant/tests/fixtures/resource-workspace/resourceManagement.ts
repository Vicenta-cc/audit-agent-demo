import { apiRequest } from '../../../src/services/apiClient';

export type ResourceKind = 'ruleset' | 'lexicon';
export interface Entry {
  id: string; term: string; kind: 'main' | 'variant' | 'tag'; parent_id: string;
  enabled: boolean; platform: string; match_type: string; risk_level: string; note: string;
}
export interface Exemption { exemption_id: string; name: string; condition: string; source_mappings?: unknown[] }
export interface Rule {
  rule_id: string; name: string; hit_condition: string; suggested_risk_level: string;
  enabled: boolean; application_stages: string[]; adjudication_notes: string;
  rule_exemptions: Exemption[]; order: number;
}
export interface ResourceContent {
  title?: string; risk_label?: string; entries?: Entry[];
  name?: string; domain?: string; audit_goal?: string; general_exemptions?: Exemption[];
  categories?: Array<{ category_id: string; name: string; rules: Rule[] }>;
}
export interface ResourceEdit {
  edit_id: string; kind: ResourceKind; version: number; content: ResourceContent;
  source: { id?: string; editable?: boolean }; saved: boolean;
  search_terms?: string[]; saves: SaveReceipt[];
}
export interface SaveReceipt {
  status: string; operation_id: string; resource_id: string; version: number;
  edit_id: string; edit_version: number;
}
export interface ResourceSummary { id: string; title: string; kind: ResourceKind; editable: boolean }
export interface Change { operation: string; target_id?: string; values?: Record<string, unknown> }
const base = (session: string) => `/api/investigation-workspaces/${encodeURIComponent(session)}`;
const path = (session: string, edit: string) => `${base(session)}/resource-edits/${encodeURIComponent(edit)}`;
export const listResourceEdits = (session: string) => apiRequest<{items: ResourceEdit[]}>(`${base(session)}/resource-edits`);
export const openResourceEdit = (session: string, kind: ResourceKind, resource_id: string) => apiRequest<ResourceEdit>(`${base(session)}/resource-edits/open`, {method:'POST',body:JSON.stringify({kind,resource_id})});
export const readResource = (kind: ResourceKind, id: string) => apiRequest<{content:ResourceContent; version:number}>(`/api/resource-library/${kind}/${encodeURIComponent(id)}`);
export const listResources = (kind: ResourceKind, offset = 0) => apiRequest<{items:ResourceSummary[];has_more:boolean}>(`/api/resource-library/${kind}?offset=${offset}&limit=50`);
export const updateResourceEdit = (session:string, edit:ResourceEdit, changes:Change[]) => apiRequest<ResourceEdit>(path(session,edit.edit_id), {method:'PATCH',body:JSON.stringify({expected_version:edit.version,changes})});
export const getResourceEdit = (session:string, edit:string) => apiRequest<ResourceEdit>(path(session,edit));
export async function saveResourceEdit(session:string, edit:ResourceEdit, mode:'new'|'update'|'copy') {
  const key = `m3-save:${session}:${edit.edit_id}:${edit.version}:${mode}`;
  const pending = localStorage.getItem(key);
  const operation_id = pending || crypto.randomUUID();
  localStorage.setItem(key,operation_id);
  if (pending) {
    const receipt = await apiRequest<SaveReceipt>(`${base(session)}/resource-saves/${encodeURIComponent(operation_id)}`);
    if (receipt.status === 'saved') { localStorage.removeItem(key); return receipt; }
  }
  const result = await apiRequest<SaveReceipt>(`${path(session,edit.edit_id)}/save`, {method:'POST',body:JSON.stringify({expected_version:edit.version,mode,operation_id})});
  localStorage.removeItem(key);
  return result;
}
