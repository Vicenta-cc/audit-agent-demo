export type ReportMetricTone = "danger" | "warning" | "success" | "";

// Key metric labels are fixed by the backend presentation. Older published
// reports keep their stored labels, so those stay mapped too.
const METRIC_TONES: Record<string, ReportMetricTone> = {
  建议拦截: "danger",
  进入复审: "warning",
  审核通过: "success",
  中高风险: "danger",
  未发现明显风险: "success"
};

export function reportMetricTone(label: string): ReportMetricTone {
  return METRIC_TONES[label] || "";
}
