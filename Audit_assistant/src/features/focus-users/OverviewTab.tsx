import type { AccountTabKey } from "./AccountTabs";
import { LatestRiskCard } from "./LatestRiskCard";
import { RelatedAccountsCard } from "./RelatedAccountsCard";
import { RiskHistoryPreviewCard } from "./RiskHistoryPreviewCard";
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
    <div className="overview-tab-wrapper">
      {/* 1. Latest Risk Content */}
      <LatestRiskCard
        risk={account.latestRisk}
        isRefetching={isRefetching}
        onRefetch={onRefetch}
        onViewHistory={() => onSwitchTab("risks")}
        onOpenDetail={onOpenDetail}
      />

      <div className="overview-two-col">
        {/* 2. Recent Risk Outputs */}
        <RiskHistoryPreviewCard
          risks={account.riskOutputs.slice(0, 3)}
          onViewAll={() => onSwitchTab("risks")}
          onOpenRisk={onOpenDetail}
        />

        {/* 3. Related Accounts */}
        <RelatedAccountsCard
          accounts={account.relatedAccounts.slice(0, 2)}
          totalCount={account.relatedAccounts.length}
          variant="preview"
          onViewAll={() => onSwitchTab("relations")}
          onOpenAnalysisTask={onOpenAnalysisTask}
        />
      </div>
    </div>
  );
}
