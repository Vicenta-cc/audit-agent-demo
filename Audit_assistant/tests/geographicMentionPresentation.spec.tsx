import { expect, test } from '@playwright/test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { TaskDraft } from '../src/types/investigation';
import type { PublicInvestigationDraft, ConfirmationPreview } from '../src/types/investigationCreation';
import { GeographicSelectionView } from '../src/features/investigation/GeographicSelectionPanel';
import { GeographicPostBasis, GeographicReportEvidence } from '../src/features/investigation/GeographicReportEvidence';
import { buildRelevanceScopeView } from '../src/features/investigation/confirmationView';
import { geographicTierLabels, MENTION_STRATEGY } from '../src/features/investigation/geographicPresentation';
import type { GeographicSelectionPage, RelevanceScope } from '../src/types/investigationCreation';
import type { GeographicCoverage, GeographicSelectionBasis } from '../src/types/reports';

const scope: RelevanceScope = {
  schema_version: 'geo-scope-v1', target: { name: '新疆', aliases: [] },
  province: { name: '新疆', aliases: [] }, topic: null,
  content_unit: 'parent_post_event', selection_policy: 'local_only'
};

test('new province and city confirmations explain all tiers; legacy cards retain their policy', () => {
  for (const target of [{ name: '新疆', aliases: [] }, { name: '克拉玛依市', aliases: [] }]) {
    const view = buildRelevanceScopeView({ relevance_scope: { ...scope, target }, blockers: [],
      geographic_strategy: MENTION_STRATEGY, geographic_max_rounds: 3 });
    expect(view?.policy).toBe('按目标地域、同省文字、作者 IP 依次筛选；每词最多 3 轮');
    expect(view?.policy).not.toContain('仅本地');
  }
  for (const rounds of [1, 2, 3]) {
    expect(buildRelevanceScopeView({ relevance_scope: scope, geographic_strategy: MENTION_STRATEGY,
      geographic_max_rounds: rounds })?.policy).toContain(`最多 ${rounds} 轮`);
  }
  expect(buildRelevanceScopeView({ relevance_scope: scope })?.policy).toBe('仅本地，不补充其他地区');
});

test('new candidate display exposes all three labels, round and termination without changing risk labels', () => {
  const page: GeographicSelectionPage = {
    run_id: 'run', status: 'ready', geographic_strategy: MENTION_STRATEGY,
    offset: 0, limit: 50, total: 3,
    summary: { candidate_occurrences: 30, reserved: 0, collected: 1, handed_off: 1,
      collected_not_delivered: 0, unfinished_queries: [], reasons: [],
      queries: [{ logical_query_id: 'logical', query: '租房', round_index: 3, max_rounds: 3,
        status: 'evaluated', end_reason: 'selected' }] },
    items: (['local', 'province_unknown', 'ip_only'] as const).map((geo_tier, index) => ({
      occurrence_id: `o${index}`, query_id: 'q3', content_key: `post${index}`, rank: index + 1,
      source_keyword: '租房', round_index: 3, geo_tier, geo_status: 'located',
      risk_band: 'weak', risk_score: 100
    }))
  };
  const html = renderToStaticMarkup(createElement(GeographicSelectionView, { page }));
  for (const label of ['目标地域直接命中', '同省文字线索', '作者 IP 同省', '第 3 / 3 轮', '已有可入选内容', '弱疑似']) {
    expect(html).toContain(label);
  }
  expect(html).not.toContain('城市未明');
  expect(html).not.toContain('高风险');
  expect(geographicTierLabels(MENTION_STRATEGY).unresolved).toBe('地域未命中');
  expect(geographicTierLabels().ip_only).toBe('IP 补充（待核实）');
});

const basis: GeographicSelectionBasis = {
  geographic_strategy: MENTION_STRATEGY, round_index: 2,
  target_name: '新疆', province_name: '新疆', source_job_id: 'new', content_key: 'post',
  occurrence_id: 'o', query_id: 'q2', source_keyword: '租房', rank: 2, risk_band: 'weak', risk_score: 100,
  geo_tier: 'province_unknown', initial_geo_tier: 'province_unknown', scope_digest: 'scope',
  policy_digest: 'policy', plan_digest: 'plan', initial_evidence_digest: 'capture',
  detail_evidence_digest: 'detail', selection_policy: 'local_only', target_level: 'province'
};
const coverage: GeographicCoverage = {
  geographic_strategy: MENTION_STRATEGY, source_job_id: 'new', candidate_occurrences: 20,
  excluded_occurrences: 0, ineligible_occurrences: 19, geographic_pending_or_conflict: 0,
  reserved: 0, collected: 1, handed_off: 1, collected_not_delivered: 0,
  unfinished_queries: [], reasons: [],
  queries: [{ logical_query_id: 'logical', query: '租房', round_index: 2, max_rounds: 3,
    status: 'evaluated', end_reason: 'source_exhausted' }]
};

test('published reports use each source frozen strategy, including mixed old and new sources', () => {
  const html = renderToStaticMarkup(createElement(GeographicReportEvidence, {
    coverage: [coverage, { ...coverage, geographic_strategy: undefined, source_job_id: 'old' }],
    basis: [basis, { ...basis, geographic_strategy: undefined, source_job_id: 'old', geo_tier: 'ip_only' }]
  }));
  expect(html).toContain('同省文字线索1');
  expect(html).toContain('第 2 / 3 轮');
  expect(html).toContain('候选来源已取完');
  expect(html.match(/冻结逐词补筛进度/g)).toHaveLength(1);
  expect(html).toContain('作者 IP 同省0');
  expect(html).toContain('IP 补充（待核实）1');
  expect(html).toContain('省级信息（城市未明）0');
  const post = renderToStaticMarkup(createElement(GeographicPostBasis, { basis }));
  expect(post).toContain('同省文字线索');
  expect(post).toContain('第 2 轮');
  expect(post).toContain('首次初筛：弱疑似');
  expect(post).not.toContain('选择策略：仅本地');
});


test('drawer uses new policy for unconfirmed draft fallback and never relabels frozen history', async ({ page }) => {
  await page.route('**/api/**', route => route.fulfill({ json: { items: [] } }));
  await page.goto('/');
  const draft = { taskName: '调查', keywords: [], confirmed: false } as unknown as TaskDraft;
  const creationDraft = { id: 'draft', status: 'DRAFT', current_revision: 1, confirmed_revision: null,
    configuration: { relevance_scope: scope, investigation: { mode: 'search', recall_plan: { strategy: 'temporary_terms' } } }
  } as PublicInvestigationDraft;
  const frozen = { relevance_scope: scope } as ConfirmationPreview;
  for (const [overrides, expected] of [
    [{}, 'new'],
    [{ confirmationPreview: frozen }, 'legacy'],
    [{ creationDraft: { ...creationDraft, status: 'QUEUED', confirmed_revision: 1 } }, 'none'],
    [{ draft: { ...draft, confirmed: true } }, 'none'],
    [{ draft: { ...draft, historicalConfiguration: [{ title: '旧任务', text: '冻结配置' }] } }, 'none']
  ] as const) {
    await page.evaluate(async props => {
      const load = (path: string) => import(path);
      const React = (await load('/node_modules/.vite/deps/react.js')).default;
      const { createRoot } = (await load('/node_modules/.vite/deps/react-dom_client.js')).default;
      const { InvestigationContextDrawer } = await load('/src/features/investigation/InvestigationContextDrawer.tsx');
      const host = document.createElement('main');
      document.body.replaceChildren(host);
      createRoot(host).render(React.createElement(InvestigationContextDrawer, {
        ...props, isOpen: true, type: 'task_config', onClose: () => {}
      }));
    }, { draft, creationDraft, ...overrides });
    const drawer = page.getByLabel('上下文详情抽屉');
    await expect(drawer).toBeVisible();
    if (expected === 'new') {
      await expect(drawer).toContainText('按目标地域、同省文字、作者 IP 依次筛选');
      await expect(drawer).not.toContainText('仅本地，不补充其他地区');
      await expect(drawer).toContainText('轮数以确认预览为准');
      await expect(drawer).not.toContainText('每词最多');
    } else if (expected === 'legacy') {
      await expect(drawer).toContainText('仅本地，不补充其他地区');
    } else {
      await expect(drawer.getByLabel('调查地域配置')).toHaveCount(0);
    }
  }
});
