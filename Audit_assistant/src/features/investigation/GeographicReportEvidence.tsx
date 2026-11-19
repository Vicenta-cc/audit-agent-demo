import { createElement as h } from 'react';
import type { GeographicCoverage, GeographicSelectionBasis } from '../../types/reports';
import { geographicEndReason, geographicTierLabel, geographicTierLabels, MENTION_STRATEGY } from './geographicPresentation';

export function GeographicPostBasis({ basis }: { basis?: GeographicSelectionBasis }) {
  if (!basis) return null;
  return h('section', { 'aria-label': '本帖地域入选依据', className: 'r31-drawer-section' },
    h('h3', null, '地域入选依据'),
    basis.target_name && h('p', null, `调查地域：${basis.target_name}${basis.province_name && basis.province_name !== basis.target_name ? `（${basis.province_name}）` : ''}`),
    h('p', null, `${geographicTierLabel(basis.geo_tier, basis.geographic_strategy)} · 搜索词：${basis.source_keyword}${basis.geographic_strategy === MENTION_STRATEGY && basis.round_index ? ` · 第 ${basis.round_index} 轮` : ''}`),
    h('p', null, `选择策略：${basis.geographic_strategy === MENTION_STRATEGY ? '目标地域直接命中优先，其次同省文字线索，再其次作者 IP 同省' : basis.selection_policy === 'local_only' ? '仅本地' : '本地优先，允许省级及 IP 补充'}`),
    h('p', null, `首次初筛：${basis.risk_band === 'strong' ? '强疑似' : basis.risk_band === 'weak' ? '弱疑似' : '未入选'} · 原始序位：${basis.rank}`),
    h('p', null, `初筛结果保留自首次捕获；正式审核使用获准交付的精采内容。${basis.geographic_strategy === MENTION_STRATEGY ? '' : 'IP 补充不代表本地事件。'}`)
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
      const labels = geographicTierLabels(source.geographic_strategy);
      return h('div', { key: source.source_job_id },
        h('h3', null, `来源调查 ${index + 1}`),
        selected[0]?.target_name && h('p', null, `冻结地域：${selected[0].target_name}${selected[0].province_name && selected[0].province_name !== selected[0].target_name ? `（${selected[0].province_name}）` : ''}`),
        h('p', null, `候选记录 ${source.candidate_occurrences} · 精采成功 ${source.collected} · 交付审核 ${source.handed_off} · 精采后未交付 ${source.collected_not_delivered}`),
        h('p', null, `本报告入选依据：${labels.local} ${count('local')} · ${labels.province_unknown}${count('province_unknown')} · ${labels.ip_only}${count('ip_only')}`),
        source.geographic_strategy === MENTION_STRATEGY && !!source.queries?.length && h('ul', { 'aria-label': '冻结逐词补筛进度' },
          source.queries.map(query => h('li', { key: query.logical_query_id },
            `搜索词：${query.query} · 第 ${query.round_index} / ${query.max_rounds} 轮`,
            geographicEndReason(query.end_reason) ? ` · ${geographicEndReason(query.end_reason)}` : ''))),
        source.unfinished_queries.length > 0 && h('p', null, `未完成搜索词 ${source.unfinished_queries.length}`),
        h('ul', null, selected.map(item => h('li', { key: `${item.source_job_id}:${item.content_key}` },
          `${item.content_key} · ${geographicTierLabel(item.geo_tier, item.geographic_strategy)} · 搜索词：${item.source_keyword}`)))
      );
    })
  );
}
