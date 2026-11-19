import type { GeographicTier } from '../../types/investigationCreation';

export const MENTION_STRATEGY = 'geographic-mention-v1';

const legacyLabels: Record<GeographicTier, string> = {
  local: '本地', province_unknown: '省级信息（城市未明）', ip_only: 'IP 补充（待核实）',
  other_region: '其他地域', unresolved: '地域待核实', conflict: '地域信息冲突'
};

const mentionLabels: Record<GeographicTier, string> = {
  ...legacyLabels,
  local: '目标地域直接命中', province_unknown: '同省文字线索', ip_only: '作者 IP 同省',
  unresolved: '地域未命中'
};

export function geographicTierLabels(strategy?: string) {
  return strategy === MENTION_STRATEGY ? mentionLabels : legacyLabels;
}

export function geographicTierLabel(tier: string | null, strategy?: string) {
  return geographicTierLabels(strategy)[tier as GeographicTier] || '地域待核实';
}

export function mentionPolicyDescription(maxRounds?: number) {
  const rounds = Number.isInteger(maxRounds) && maxRounds! >= 1 && maxRounds! <= 3 ? maxRounds : 3;
  return `按目标地域、同省文字、作者 IP 依次筛选；每词最多 ${rounds} 轮`;
}

export function geographicEndReason(reason?: string | null) {
  const reasons: Record<string, string> = {
    selected: '已有可入选内容', max_rounds: '已达到补筛轮次上限',
    source_exhausted: '候选来源已取完', task_limit: '已达到任务总量上限'
  };
  return reason ? reasons[reason] : undefined;
}
