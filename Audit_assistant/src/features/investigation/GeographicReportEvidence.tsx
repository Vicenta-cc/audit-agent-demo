import { createElement as h } from 'react';
import type { GeographicCoverage, GeographicSelectionBasis } from '../../types/reports';

const labels: Record<string, string> = {
  local: '本地', province_unknown: '省级信息（城市未明）', ip_only: 'IP 补充（待核实）'
};

export function GeographicPostBasis({ basis }: { basis?: GeographicSelectionBasis }) {
  if (!basis) return null;
  return h('section', { 'aria-label': '本帖地域入选依据', className: 'r31-drawer-section' },
    h('h3', null, '地域入选依据'),
    basis.target_name && h('p', null, `调查地域：${basis.target_name}${basis.province_name && basis.province_name !== basis.target_name ? `（${basis.province_name}）` : ''}`),
    h('p', null, `${labels[basis.geo_tier] || '地域待核实'} · 搜索词：${basis.source_keyword}`),
    h('p', null, `选择策略：${basis.selection_policy === 'local_only' ? '仅本地' : '本地优先，允许省级及 IP 补充'}`),
    h('p', null, `首次初筛：${basis.risk_band === 'strong' ? '强疑似' : basis.risk_band === 'weak' ? '弱疑似' : '未入选'} · 原始序位：${basis.rank}`),
    h('p', null, '初筛结果保留自首次捕获；正式审核使用获准交付的精采内容。IP 补充不代表本地事件。')
  );
}

export function GeographicReportEvidence({ coverage, basis = [] }: { coverage?: GeographicCoverage[]; basis?: GeographicSelectionBasis[] }) {
  if (!coverage?.length) return null;
  return h('section', { 'aria-label': '已发布地域筛选依据', className: 'r31-drawer-section' },
    h('h2', null, '地域筛选范围与依据'),
    h('p', null, '以下记录随本报告发布版本冻结。候选记录按搜索词计数，同一内容可能在不同词下出现；与正式审核帖子数分开统计。'),
    ...coverage.map((source, index) => {
      const selected = basis.filter(item => item.source_job_id === source.source_job_id);
      const count = (tier: string) => selected.filter(item => item.geo_tier === tier).length;
      return h('div', { key: source.source_job_id },
        h('h3', null, `来源调查 ${index + 1}`),
        selected[0]?.target_name && h('p', null, `冻结地域：${selected[0].target_name}${selected[0].province_name && selected[0].province_name !== selected[0].target_name ? `（${selected[0].province_name}）` : ''}`),
        h('p', null, `候选记录 ${source.candidate_occurrences} · 精采成功 ${source.collected} · 交付审核 ${source.handed_off} · 精采后未交付 ${source.collected_not_delivered}`),
        h('p', null, `本报告入选依据：本地 ${count('local')} · 省级信息（城市未明）${count('province_unknown')} · IP 补充（待核实）${count('ip_only')}`),
        source.unfinished_queries.length > 0 && h('p', null, `未完成搜索词 ${source.unfinished_queries.length}`),
        h('ul', null, selected.map(item => h('li', { key: `${item.source_job_id}:${item.content_key}` },
          `${item.content_key} · ${labels[item.geo_tier] || '地域待核实'} · 搜索词：${item.source_keyword}`)))
      );
    })
  );
}
