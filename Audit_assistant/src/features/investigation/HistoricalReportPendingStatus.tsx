import { createElement } from "react";
import { LoaderCircle } from "lucide-react";
import type { InvestigationTurnStage } from "../../types/investigations";
import { historicalPendingStatus } from "./historicalReportPresentation";

interface HistoricalReportPendingStatusProps {
  stage?: InvestigationTurnStage;
  recovering?: boolean;
}

export function HistoricalReportPendingStatus({
  stage,
  recovering = false
}: HistoricalReportPendingStatusProps) {
  return createElement(
    "div",
    {
      className: "inv-report-answer-pending",
      role: "status",
      "aria-live": "polite",
      "aria-atomic": "true"
    },
    createElement(LoaderCircle, {
      className: "inv-report-answer-spinner",
      size: 17,
      "aria-hidden": "true"
    }),
    createElement("span", null, historicalPendingStatus(stage, recovering))
  );
}
