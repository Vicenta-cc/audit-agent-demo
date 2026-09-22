import type { InvestigationTurnStage } from "../../types/investigations";

export function creationPendingLabel(stage?: InvestigationTurnStage) {
  if (stage === "accepted") return "已接收，等待开始处理";
  if (stage === "planning") return "正在理解你的问题";
  if (stage === "preparing_sources") return "正在准备所需资料";
  if (stage === "acquiring_source") return "正在等待相关信息返回";
  if (stage === "answering") return "正在整理回答";
  return "正在思考";
}
