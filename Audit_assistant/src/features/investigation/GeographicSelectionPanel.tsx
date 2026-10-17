import { createElement as h, useEffect, useState } from 'react';
import { getGeographicSelection } from '../../services/investigationCreation';
import type { GeographicSelectionPage, GeographicTier } from '../../types/investigationCreation';

const tiers: Record<GeographicTier, string> = {
  local: '本地', province_unknown: '省级信息（城市未明）', ip_only: 'IP 补充（待核实）',
  other_region: '其他地域', unresolved: '地域待核实', conflict: '地域信息冲突'
};
const reasons: Record<string, string> = {
  geographic_receipt_pending: '采集结果待对账', awaiting_reconciliation: '采集结果待对账',
  geographic_delivery_pending: '精采后的审核交付尚未完成', geographic_queries_unfinished: '仍有搜索词未完成候选评估',
  geographic_execution_incomplete: '执行尚未完成，请核对任务状态', no_candidates: '未获取候选内容',
  no_eligible_risk: '无符合初筛条件的候选', no_eligible_geographic_content: '无符合地域与初筛条件的内容',
  geographic_pending_or_conflict: '部分候选地域待核实或存在冲突', collected_not_delivered: '部分精采内容未获准交付',
  geographic_evaluation_pending: '部分帖子地域预筛未完成，已保留待判定记录，报告暂不能完成',
  collection_failed: '部分内容采集未完成', query_failed: '候选采集或评估失败', paused: '任务已暂停',
  awaiting_account_resolution: '等待解决采集账号状态'
};

export function GeographicSelectionView({ page }: { page: GeographicSelectionPage }) {
  if (page.status === 'not_applicable') return null;
  const status = {
    pending: '候选评估尚未完成', ready: '地域筛选已有审核交付', empty: '本轮无可交付内容', incomplete: '地域筛选尚未完成'
  }[page.status];
  const detailReasons = [...new Set([page.reason, ...(page.summary?.reasons || [])].flatMap(reason => reason && reasons[reason] ? [reasons[reason]] : []))];
  return h('section', { 'aria-label': '地域筛选记录', style: { fontSize: 13, lineHeight: 1.75, overflowWrap: 'anywhere' } },
    h('h3', { style: { fontSize: 14 } }, status),
    page.summary && h('p', null, `候选记录 ${page.summary.candidate_occurrences} · 精采成功 ${page.summary.collected} · 已交付审核 ${page.summary.handed_off} · 精采后未交付 ${page.summary.collected_not_delivered}`),
    !!page.summary?.geographic_evaluation_pending && h('p', null, `地域预筛待判定 ${page.summary.geographic_evaluation_pending} 条`),
    detailReasons.length > 0 && h('p', null, detailReasons.join('；')),
    h('p', null, '以下为候选的地域与初筛依据；正式风险结论以审核结果为准。'),
    h('ul', null, page.items.map(item => h('li', { key: item.occurrence_id },
      h('strong', null, item.pending_reason === 'geographic_response_invalid' ? '地域预筛待判定' : item.geo_tier ? tiers[item.geo_tier] : '地域待核实'),
      h('div', null, `搜索词：${item.source_keyword} · 原始序位：${item.rank}`),
      h('div', null, `内容：${item.content_key} · 初筛：${item.risk_band == null ? '待初筛' : item.risk_band === 'strong' ? '强疑似' : item.risk_band === 'weak' ? '弱疑似' : '未入选'}`)
    ))),
    page.total > 0 && h('p', null, `候选依据 ${page.offset + 1}–${Math.min(page.offset + page.items.length, page.total)} / ${page.total}`)
  );
}

export function GeographicSelectionPanel({ runId }: { runId: string }) {
  const [page, setPage] = useState<GeographicSelectionPage | null>(null);
  const [error, setError] = useState(false);
  const [offset, setOffset] = useState(0);
  const [tier, setTier] = useState<GeographicTier | ''>('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    setPage(null);
    setError(false);
    getGeographicSelection(runId, offset, tier || undefined).then(result => {
      if (active && result.run_id === runId) setPage(result);
    }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, [runId, offset, tier, refresh]);
  return <div>
    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
      <label>地域档位 <select aria-label="筛选地域档位" value={tier} onChange={event => { setOffset(0); setTier(event.target.value as GeographicTier | ''); }}>
        <option value="">全部</option>
        {Object.entries(tiers).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <button type="button" onClick={() => setRefresh(value => value + 1)}>刷新筛选记录</button>
    </div>
    {error ? <p role="alert">暂时无法读取本轮地域筛选记录，请重试。</p> : !page ? <p role="status">正在读取地域筛选记录…</p> : <GeographicSelectionView page={page} />}
    {page && page.total > page.limit && <nav aria-label="地域筛选记录分页">
      <button type="button" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - page.limit))}>上一页</button>
      <button type="button" disabled={offset + page.limit >= page.total} onClick={() => setOffset(value => value + page.limit)}>下一页</button>
    </nav>}
  </div>;
}
