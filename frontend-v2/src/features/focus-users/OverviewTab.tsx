import type { AccountTabKey } from "./AccountTabs";
import { LatestRiskCard } from "./LatestRiskCard";
import { MonitoringPlanCard } from "./MonitoringPlanCard";
import { RelatedAccountsCard } from "./RelatedAccountsCard";
import { RiskHistoryPreviewCard } from "./RiskHistoryPreviewCard";
import { RiskSummaryCard } from "./RiskSummaryCard";
import type { MonitoredAccount, RiskOutput } from "../../types/focusUsers";

interface OverviewTabProps {
  account: MonitoredAccount;
  isRefetching: boolean;
  onRefetch: () => void;
  onSwitchTab: (tab: AccountTabKey) => void;
  onOpenDetail: (risk: RiskOutput) => void;
  onOpenAnalysisTask: (taskId: string) => void;
}

export function OverviewTab({
  account,
  isRefetching,
  onRefetch,
  onSwitchTab,
  onOpenDetail,
  onOpenAnalysisTask
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
        totalCount={account.relatedAccounts.length}
        variant="preview"
        onViewAll={() => onSwitchTab("relations")}
        onOpenAnalysisTask={onOpenAnalysisTask}
      />
      <RiskHistoryPreviewCard
        risks={account.riskOutputs}
        onViewAll={() => onSwitchTab("risks")}
        onOpenRisk={onOpenDetail}
      />
      <MonitoringPlanCard plan={account.monitoringPlan} onAdjust={() => onSwitchTab("plan")} />
    </section>
  );
}
