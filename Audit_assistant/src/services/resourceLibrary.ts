import { apiRequest } from './apiClient';
import type { AuditRuleSet, RiskRule } from '../types/investigation';

export interface ExemptionContent { exemption_id: string; name: string; condition: string; enabled?: boolean }
export interface RuleContent {
  rule_id: string; name: string; hit_condition: string; suggested_risk_level: 'low' | 'medium' | 'high';
  rule_exemptions: ExemptionContent[]; application_stages: string[]; adjudication_notes: string; enabled: boolean; order: number;
}
export interface RulesContent {
  schema_version: 0; name: string; domain: string; audit_goal: string; general_exemptions: ExemptionContent[];
  categories: { category_id: string; name: string; description: string; order: number; rules: RuleContent[] }[];
}
export interface LexiconEntry {
  id: string; term: string; kind: 'main' | 'variant' | 'tag'; parent_id: string; enabled: boolean;
  platform: string; match_type: string; risk_level: string; note: string;
}
export interface LexiconContent { title: string; risk_label: string; description: string; entries: LexiconEntry[] }
export interface Resource<T> {
  id: string; kind: 'ruleset' | 'lexicon'; content: T; version: number; editable: boolean;
  published_revision_id?: string; published_version?: number;
}
export async function listResources<T>(kind: 'ruleset' | 'lexicon'): Promise<Resource<T>[]> {
  const ids: string[] = [];
  for (let offset = 0; ; offset += 100) {
    const page = await apiRequest<{ items: { id: string }[]; has_more: boolean }>(`/api/resource-library/${kind}?offset=${offset}&limit=100`);
    ids.push(...page.items.map(item => item.id));
    if (!page.has_more) break;
  }
  return Promise.all(ids.map(id => apiRequest<Resource<T>>(`/api/resource-library/${kind}/${encodeURIComponent(id)}`)));
}
export const saveResource = <T>(kind: 'ruleset' | 'lexicon', id: string, content: T, expectedVersion: number, operationId: string) =>
  apiRequest<Resource<T>>(`/api/resource-library/${kind}/${encodeURIComponent(id)}`, {
    method: 'PUT', body: JSON.stringify({ content, expected_version: expectedVersion, operation_id: operationId })
  });
export const deleteResource = (resource: Resource<unknown>) => apiRequest(`/api/resource-library/${resource.kind}/${encodeURIComponent(resource.id)}`, {
  method: 'DELETE', body: JSON.stringify({ expected_version: resource.version })
});

const stages: Record<string, RiskRule['applicationStages'][number]> = {
  image_evidence: '图片证据提取', video_frame_evidence: '视频关键帧提取', comment_audit: '评论审核', fusion_audit: '融合研判'
};
const levels = { low: '低风险', medium: '中风险', high: '高风险' } as const;
export function ruleSetView(resource: Resource<RulesContent>): AuditRuleSet {
  const c = resource.content;
  return {
    id: resource.id, name: c.name, category: c.domain, version: `v${resource.published_version || 1}`,
    status: resource.published_revision_id ? '已发布' : '草稿', updatedAt: '已同步', referencedTaskCount: 0,
    generalExemptions: c.general_exemptions.map(e => ({ id: e.exemption_id, title: e.name, description: e.condition, enabled: e.enabled !== false })),
    categories: [...c.categories].sort((a, b) => a.order - b.order).map(cat => ({
      id: cat.category_id, name: cat.name, rules: [...cat.rules].sort((a,b) => a.order - b.order).map(r => ({
        id: r.rule_id, name: r.name, content: r.hit_condition, suggestedLevel: levels[r.suggested_risk_level],
        exemptionConditions: r.rule_exemptions.map(e => e.condition).join('\n'),
        applicationStages: r.application_stages.filter(s => stages[s]).map(s => stages[s]),
        notes: r.adjudication_notes, enabled: r.enabled
      }))
    }))
  };
}
export function rulesContent(view: AuditRuleSet, original?: RulesContent): RulesContent {
  if (!view.categories.length || view.categories.some(c => !c.rules.length)) throw new Error('请为每个风险类型至少添加一条规则后再保存。');
  const cleanExemption = (e: ExemptionContent): ExemptionContent => ({ exemption_id: e.exemption_id, name: e.name, condition: e.condition, ...(e.enabled === false ? { enabled: false } : {}) });
  return {
    schema_version: 0, name: view.name, domain: view.category, audit_goal: original?.audit_goal || `依据《${view.name}》识别风险，并结合证据与豁免条件进行研判。`,
    general_exemptions: view.generalExemptions.map(e => cleanExemption({ exemption_id: e.id, name: e.title, condition: e.description, enabled: e.enabled })),
    categories: view.categories.map((cat, i) => {
      const oldCat = original?.categories.find(c => c.category_id === cat.id);
      return { category_id: cat.id, name: cat.name, description: oldCat?.description || '', order: i,
        rules: cat.rules.map((r, j) => {
          const old = original?.categories.flatMap(c => c.rules).find(v => v.rule_id === r.id);
          const unchangedExemptions = old?.rule_exemptions.map(e => e.condition).join('\n') === r.exemptionConditions;
          return { rule_id: r.id, name: r.name, hit_condition: r.content,
            suggested_risk_level: r.suggestedLevel === '高风险' ? 'high' : r.suggestedLevel === '低风险' ? 'low' : 'medium',
            rule_exemptions: unchangedExemptions ? old!.rule_exemptions.map(cleanExemption) : r.exemptionConditions.split('\n').filter(s => s.trim()).map((s,k) => ({ exemption_id: `ex-${k}`, name: s.trim().slice(0,160), condition: s.trim() })),
            application_stages: Object.entries(stages).filter(([,label]) => r.applicationStages.includes(label)).map(([key]) => key),
            adjudication_notes: r.notes?.trim() || r.content, enabled: r.enabled, order: j };
        }) };
    })
  };
}
