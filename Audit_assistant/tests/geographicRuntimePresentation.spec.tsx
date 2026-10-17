import { expect, test } from '@playwright/test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { GeographicSelectionView } from '../src/features/investigation/GeographicSelectionPanel';
import type { GeographicSelectionPage } from '../src/types/investigationCreation';

const base: GeographicSelectionPage = { run_id: 'run-a', status: 'ready', offset: 0, limit: 50, total: 1,
  summary: { candidate_occurrences: 12, reserved: 2, collected: 2, handed_off: 1, collected_not_delivered: 1, unfinished_queries: [], reasons: [] },
  items: [{ occurrence_id: 'o', query_id: 'q', content_key: 'dy:1', rank: 1, source_keyword: '克拉玛依大火', geo_tier: 'ip_only', geo_status: 'ok', risk_band: 'strong', risk_score: 200 }] };

test('geographic summary separates captures, collected and delivery and labels IP as unverified', () => {
  const html = renderToStaticMarkup(createElement(GeographicSelectionView, { page: base }));
  expect(html).toContain('候选记录 12');
  expect(html).toContain('精采成功 2');
  expect(html).toContain('已交付审核 1');
  expect(html).toContain('精采后未交付 1');
  expect(html).toContain('IP 补充（待核实）');
  expect(html).toContain('克拉玛依大火');
  expect(html).toContain('初筛');
  expect(html).not.toContain('高风险');
});
for (const [status, label] of [['pending', '候选评估尚未完成'], ['incomplete', '尚未完成'], ['empty', '本轮无可交付内容']] as const) {
  test(`${status} does not claim successful reporting or no risk`, () => {
    const html = renderToStaticMarkup(createElement(GeographicSelectionView, { page: { ...base, status, items: [] } }));
    expect(html).toContain(label);
    expect(html).not.toContain('无风险');
    expect(html).not.toContain('报告已完成');
  });
}
test('unscoped result leaves existing task display unchanged', () => {
  expect(renderToStaticMarkup(createElement(GeographicSelectionView, { page: { ...base, status: 'not_applicable' } }))).toBe('');
});

test('technical geography pending stays distinct from exclusion and completed reporting', () => {
  const page: GeographicSelectionPage = { ...base, status: 'incomplete', reason: 'geographic_evaluation_pending',
    summary: { ...base.summary!, geographic_evaluation_pending: 1, reasons: ['geographic_evaluation_pending'] },
    items: [{ ...base.items[0], geo_tier: 'unresolved', evaluation_status: 'invalid_response', pending_reason: 'geographic_response_invalid' }] };
  const html = renderToStaticMarkup(createElement(GeographicSelectionView, { page }));
  expect(html).toContain('地域预筛待判定 1 条');
  expect(html).toContain('报告暂不能完成');
  expect(html).not.toContain('其他地域');
  expect(html).not.toContain('无符合地域');
  const waiting = renderToStaticMarkup(createElement(GeographicSelectionView, { page: {
    ...page, items: [{ ...page.items[0], risk_band: null, risk_score: null }] } }));
  expect(waiting).toContain('待初筛');
  expect(waiting).not.toContain('未入选');
});

import { GeographicPostBasis, GeographicReportEvidence } from '../src/features/investigation/GeographicReportEvidence';
import type { GeographicSelectionBasis } from '../src/types/reports';
const selected: GeographicSelectionBasis = { target_name: '克拉玛依市', province_name: '新疆维吾尔自治区', source_job_id: 'frozen-job-a', content_key: 'dy:frozen-post', occurrence_id: 'o', query_id: 'q', source_keyword: '冻结原词', rank: 3,
  risk_band: 'weak', risk_score: 100, geo_tier: 'province_unknown', initial_geo_tier: 'province_unknown', scope_digest: 'scope-a', policy_digest: 'policy-a', plan_digest: 'plan-a', initial_evidence_digest: 'capture-a', detail_evidence_digest: 'detail-a', selection_policy: 'local_first_with_supplement', target_level: 'city' };

test('published geographic evidence partitions sources and does not add to formal risk statistics', () => {
  const coverage = ['frozen-job-a', 'frozen-job-b'].map(source_job_id => ({ source_job_id, candidate_occurrences: 12, excluded_occurrences: 0, ineligible_occurrences: 1, geographic_pending_or_conflict: 0, reserved: 0, collected: 1, handed_off: 1, collected_not_delivered: 0, unfinished_queries: [], reasons: [] }));
  const html = renderToStaticMarkup(createElement(GeographicReportEvidence, { coverage, basis: [selected, { ...selected, source_job_id: 'frozen-job-b', geo_tier: 'ip_only', source_keyword: '另一个冻结词' }] }));
  expect(html).toContain('随本报告发布版本冻结');
  expect(html).toContain('与正式审核帖子数分开统计');
  expect(html).toContain('来源调查 1');
  expect(html).toContain('来源调查 2');
  expect(html.match(/省级信息（城市未明）1/g)).toHaveLength(1);
  expect(html.match(/IP 补充（待核实）1/g)).toHaveLength(1);
  expect(html).toContain('冻结原词');
});

test('published post shows original risk separately and legacy reports stay unchanged', () => {
  const html = renderToStaticMarkup(createElement(GeographicPostBasis, { basis: selected }));
  expect(html).toContain('首次初筛：弱疑似');
  expect(html).toContain('调查地域：克拉玛依市（新疆维吾尔自治区）');
  expect(html).toContain('正式审核使用获准交付的精采内容');
  expect(renderToStaticMarkup(createElement(GeographicReportEvidence, {}))).toBe('');
  expect(renderToStaticMarkup(createElement(GeographicPostBasis, {}))).toBe('');
});

import { buildRelevanceScopeView } from '../src/features/investigation/confirmationView';
test('scope notice follows server blockers without changing confirmation permission', () => {
  const relevance_scope = { schema_version: 'geo-scope-v1' as const, target: { name: '克拉玛依市', aliases: [] }, province: { name: '新疆维吾尔自治区', aliases: [] }, topic: null, content_unit: 'parent_post_event' as const, selection_policy: 'local_only' as const };
  expect(buildRelevanceScopeView({ relevance_scope })?.notice).toContain('当前不能启动');
  expect(buildRelevanceScopeView({ relevance_scope, blockers: [{ code: 'RELEVANCE_SCOPE_EXECUTION_UNAVAILABLE' }] })?.notice).toContain('当前不能启动');
  expect(buildRelevanceScopeView({ relevance_scope, blockers: [] })?.notice).toBe('将按已确认地域与策略筛选内容。');
});
