import type { AccountTabKey } from "./AccountTabs";
import { LatestRiskCard } from "./LatestRiskCard";
import { MonitoringPlanCard } from "./MonitoringPlanCard";
import { RelatedAccountsCard } from "./RelatedAccountsCard";
import { RiskHistoryPreviewCard } from "./RiskHistoryPreviewCard";
import { RiskSummaryCard } from "./RiskSummaryCard";
import type { MonitoredAccount } from "../../types/focusUsers";

interface OverviewTabProps {
  account: MonitoredAccount;
  isRefetching: boolean;
  onRefetch: () => void;
  onSwitchTab: (tab: AccountTabKey) => void;
  onOpenDetail: () => void;
  onOpenRelation: () => void;
}

export function OverviewTab({
  account,
  isRefetching,
  onRefetch,
  onSwitchTab,
  onOpenDetail,
  onOpenRelation
}: OverviewTabProps) {
  return (
    <section className="overview-grid">
      <LatestRiskCard
        risk={account.latestRisk}
        isRefetching={isRefetching}
        onRefetch={onRefetch}
        onViewHistory={() => onSwitchTab("risks")}
        onOpenDetail={onOpenDetail}
      />
      <RiskSummaryCard metrics={account.metrics} onViewAll={() => onSwitchTab("risks")} />
      <RelatedAccountsCard
        accounts={account.relatedAccounts.slice(0, 3)}
        onViewAll={() => onSwitchTab("relations")}
        onOpenRelation={onOpenRelation}
      />
      <RiskHistoryPreviewCard risks={account.riskOutputs} onViewAll={() => onSwitchTab("risks")} />
      <MonitoringPlanCard plan={account.monitoringPlan} onAdjust={() => onSwitchTab("plan")} />
    </section>
  );
}
