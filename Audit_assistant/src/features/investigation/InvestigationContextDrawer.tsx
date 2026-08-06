import { X, ShieldCheck, FileText, Search, Users, Sparkles, ArrowRight } from "lucide-react";
import type { TaskDraft, ReportSummary, EvidenceItem, KeyUserProfile, AuditRuleSet } from "../../types/investigation";

export type DrawerType =
  | "task_config"
  | "report"
  | "evidence"
  | "key_users"
  | "agent_logs"
  | "ruleset"
  | null;

interface InvestigationContextDrawerProps {
  isOpen: boolean;
  type: DrawerType;
  onClose: () => void;
  draft?: TaskDraft;
  report?: ReportSummary;
  evidenceItems?: EvidenceItem[];
  keyUsers?: KeyUserProfile[];
  activeRuleSet?: AuditRuleSet;
  onFollowUpUser?: (user: KeyUserProfile) => void;
}

export function InvestigationContextDrawer({
  isOpen,
  type,
  onClose,
  draft,
  report,
  evidenceItems = [],
  keyUsers = [],
  activeRuleSet,
  onFollowUpUser
}: InvestigationContextDrawerProps) {
  if (!isOpen || !type) return null;

  const getTitle = () => {
    switch (type) {
      case "task_config": return "任务高级配置";
      case "report": return "完整研判报告目录";
      case "evidence": return "原始与多媒体证据链";
      case "key_users": return "重点作者候选列表";
      case "agent_logs": return "4 名 Agent 协同执行日志";
      case "ruleset": return "研判方案配置";
      default: return "上下文详情";
    }
  };

  return (
    <div className={`inv-context-drawer ${isOpen ? "is-open" : ""}`} aria-label="上下文详情抽屉">
      <div className="inv-drawer-header">
        <span className="inv-drawer-title">{getTitle()}</span>
        <button type="button" className="inv-drawer-close" onClick={onClose}>
          <X size={18} />
        </button>
      </div>

      <div className="inv-drawer-body">
        {/* TASK CONFIG VIEW */}
        {type === "task_config" && draft ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <div style={{ background: "#f8fafc", padding: "14px", borderRadius: "8px", border: "1px solid #e2e8f0" }}>
              <div style={{ fontSize: "11px", color: "#64748b" }}>任务名称</div>
              <div style={{ fontSize: "15px", fontWeight: "800", color: "#0f172a" }}>{draft.taskName}</div>
              <div style={{ fontSize: "12px", color: "#334155", marginTop: "4px" }}>采集主题：{draft.subject}</div>
              <div style={{ fontSize: "12px", color: "#334155" }}>任务类型：{draft.taskType}</div>
            </div>

            <div>
              <div style={{ fontSize: "12.5px", fontWeight: "750", marginBottom: "6px" }}>本次搜索词</div>
              <div style={{ color: "#334155", fontSize: "13px", lineHeight: "1.65" }}>{draft.keywords.join("、")}</div>
            </div>

            <div>
              <div style={{ fontSize: "12.5px", fontWeight: "750", marginBottom: "6px" }}>推荐研判方案</div>
              <div style={{ color: "#0f172a", fontSize: "13px", fontWeight: "700" }}>
                {draft.analysisPlanName || draft.matchedRuleSet}
              </div>
              <div style={{ color: "#64748b", fontSize: "12px", lineHeight: "1.55", marginTop: "4px" }}>{draft.ruleSetDescription}</div>
            </div>

            <div style={{ paddingTop: "12px", borderTop: "1px solid #e2e8f0" }}>
              <div style={{ fontSize: "12.5px", fontWeight: "750", marginBottom: "4px" }}>采集范围</div>
              <div style={{ color: "#64748b", fontSize: "12px", lineHeight: "1.55" }}>
                {draft.scopeDescription || "采集范围使用系统默认值，可在此调整时间范围与采集数量。"}
              </div>
            </div>
          </div>
        ) : null}

        {/* REPORT VIEW */}
        {type === "report" && report ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
            <div style={{ fontSize: "14px", fontWeight: "800", color: "#0f172a" }}>{report.title}</div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", background: "#f8fafc", padding: "12px", borderRadius: "8px" }}>
              <div><span style={{ fontSize: "11px", color: "#64748b" }}>总量</span><div style={{ fontSize: "16px", fontWeight: "800" }}>{report.totalCollected}</div></div>
              <div><span style={{ fontSize: "11px", color: "#64748b" }}>疑似风险</span><div style={{ fontSize: "16px", fontWeight: "800", color: "#dc2626" }}>{report.suspectedRisks}</div></div>
              <div><span style={{ fontSize: "11px", color: "#64748b" }}>建议复核</span><div style={{ fontSize: "16px", fontWeight: "800", color: "#d97706" }}>{report.suggestedReview}</div></div>
              <div><span style={{ fontSize: "11px", color: "#64748b" }}>重点作者</span><div style={{ fontSize: "16px", fontWeight: "800" }}>{report.keyAuthorCandidates}</div></div>
            </div>

            <div>
              <div style={{ fontSize: "13px", fontWeight: "750", marginBottom: "8px" }}>主要风险类型分布</div>
              <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                {report.riskDistribution.map((item, idx) => (
                  <div key={idx} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: "#ffffff", padding: "8px 10px", border: "1px solid #e2e8f0", borderRadius: "6px", fontSize: "12px" }}>
                    <span>{item.name}</span>
                    <span style={{ fontWeight: "700", color: "#2563eb" }}>{item.count} 条 ({item.percentage}%)</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : null}

        {/* EVIDENCE VIEW */}
        {type === "evidence" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            <div style={{ fontSize: "12px", color: "#64748b" }}>已提取结构化证据片段（共 {evidenceItems.length} 处）</div>
            {evidenceItems.map((item) => (
              <div key={item.id} className="inv-ev-card">
                <div className="inv-ev-head">
                  <span className="inv-ev-title">{item.title}</span>
                  <span style={{ fontSize: "11px", padding: "1px 6px", background: item.riskLevel === "高风险" ? "#fef2f2" : "#fef3c7", color: item.riskLevel === "高风险" ? "#dc2626" : "#b45309", borderRadius: "4px", fontWeight: "700" }}>
                    {item.riskLevel}
                  </span>
                </div>
                <div style={{ fontSize: "12px", color: "#475569" }}>作者：{item.author} ({item.platform})</div>
                <div className="inv-ev-snippet">{item.snippet}</div>
                {item.ocrText ? <div style={{ fontSize: "11.5px", color: "#1e293b", background: "#f1f5f9", padding: "4px 8px", borderRadius: "4px" }}>OCR 提取: {item.ocrText}</div> : null}
                {item.asrText ? <div style={{ fontSize: "11.5px", color: "#1e293b", background: "#f1f5f9", padding: "4px 8px", borderRadius: "4px" }}>ASR 语音: {item.asrText}</div> : null}
                {item.videoTimestamp ? <div style={{ fontSize: "11px", color: "#2563eb", fontWeight: "600" }}>视频关键帧: {item.videoTimestamp}</div> : null}
              </div>
            ))}
          </div>
        ) : null}

        {/* KEY USERS VIEW */}
        {type === "key_users" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {keyUsers.length > 0 ? (
              keyUsers.map((usr) => (
                <div key={usr.id} style={{ background: "#ffffff", border: "1px solid #cbd5e1", borderRadius: "8px", padding: "12px", display: "flex", flexDirection: "column", gap: "6px" }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <span style={{ fontSize: "14px", fontWeight: "800", color: "#0f172a" }}>{usr.name}</span>
                    <span style={{ fontSize: "11px", background: "#fee2e2", color: "#dc2626", padding: "1px 6px", borderRadius: "4px", fontWeight: "700" }}>
                      {usr.riskCount} 条风险记录
                    </span>
                  </div>
                  <div style={{ fontSize: "12px", color: "#64748b" }}>账号：{usr.handle} | 平台：{usr.platform} | 粉丝：{usr.followers}</div>
                  <div style={{ fontSize: "12px", color: "#334155" }}>{usr.bio}</div>
                  {onFollowUpUser ? (
                    <button
                      type="button"
                      className="inv-btn-secondary"
                      onClick={() => onFollowUpUser(usr)}
                      style={{ marginTop: "4px", fontSize: "12px", padding: "4px 8px" }}
                    >
                      <ArrowRight size={13} />
                      <span>发起穿透调查</span>
                    </button>
                  ) : null}
                </div>
              ))
            ) : (
              <div style={{ fontSize: "13px", color: "#64748b" }}>暂无重点作者数据。</div>
            )}
          </div>
        ) : null}

        {/* AGENT LOGS VIEW */}
        {type === "agent_logs" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "10px", fontSize: "12.5px" }}>
            <div style={{ background: "#1e293b", color: "#38bdf8", padding: "10px", borderRadius: "6px", fontFamily: "monospace" }}>
              [10:02:15] [平台采集专员] 启动 7 个临时召回词搜索 (抖音、小红书)...
            </div>
            <div style={{ background: "#1e293b", color: "#f8fafc", padding: "10px", borderRadius: "6px", fontFamily: "monospace" }}>
              [10:03:10] [平台采集专员] 去重完成，交接 356 条内容 &rarr; 多媒体证据专员
            </div>
            <div style={{ background: "#1e293b", color: "#f8fafc", padding: "10px", borderRadius: "6px", fontFamily: "monospace" }}>
              [10:04:22] [多媒体证据专员] 识别 176 条视频，完成 OCR / ASR 与关键帧抽取，构建 38 处结构化证据
            </div>
            <div style={{ background: "#1e293b", color: "#fbbf24", padding: "10px", borderRadius: "6px", fontFamily: "monospace" }}>
              [10:05:05] [风险研判专员] 发起反向补充证据请求 &rarr; 多媒体证据专员 (核验 00:36-00:48 视频)
            </div>
            <div style={{ background: "#1e293b", color: "#34d399", padding: "10px", borderRadius: "6px", fontFamily: "monospace" }}>
              [10:06:12] [报告调查专员] 汇总 42 条疑似风险，生成世界杯博彩专题研判报告。
            </div>
          </div>
        ) : null}

        {/* RULESET VIEW */}
        {type === "ruleset" && activeRuleSet ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            <div style={{ padding: "12px 0 14px", borderBottom: "1px solid #e2e8f0" }}>
              <div style={{ fontSize: "15px", fontWeight: "700", color: "#0f172a" }}>
                {draft?.analysisPlanName || activeRuleSet.name}
              </div>
              <div style={{ fontSize: "12px", color: "#64748b", marginTop: "4px" }}>
                系统推荐 · 引用 {activeRuleSet.name} {activeRuleSet.version}
              </div>
              <div style={{ fontSize: "12px", color: "#64748b", lineHeight: "1.55", marginTop: "8px" }}>
                当前已发布版本保持只读。需要调整时将另存为新方案或新版本，不会覆盖现有已发布版本。
              </div>
            </div>

            <div>
              <div style={{ fontSize: "12.5px", fontWeight: "750", marginBottom: "6px" }}>通用豁免条件 ({activeRuleSet.generalExemptions.length} 条)</div>
              {activeRuleSet.generalExemptions.map((ex) => (
                <div key={ex.id} style={{ background: "#f8fafc", border: "1px solid #e2e8f0", padding: "8px 10px", borderRadius: "6px", marginBottom: "6px", fontSize: "12px" }}>
                  <div style={{ fontWeight: "700", color: "#0f172a" }}>{ex.title}</div>
                  <div style={{ color: "#475569" }}>{ex.description}</div>
                </div>
              ))}
            </div>

            <div>
              <div style={{ fontSize: "12.5px", fontWeight: "750", marginBottom: "6px" }}>风险类型 & 规则</div>
              {activeRuleSet.categories.map((cat) => (
                <div key={cat.id} style={{ marginBottom: "10px" }}>
                  <div style={{ fontSize: "13px", fontWeight: "800", color: "#2563eb", marginBottom: "4px" }}>▼ {cat.name} ({cat.rules.length} 条)</div>
                  {cat.rules.map((rl) => (
                    <div key={rl.id} style={{ background: "#ffffff", border: "1px solid #cbd5e1", padding: "10px", borderRadius: "6px", marginBottom: "6px", fontSize: "12px" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", fontWeight: "800" }}>
                        <span>{rl.name}</span>
                        <span style={{ color: rl.suggestedLevel === "高风险" ? "#dc2626" : "#b45309" }}>{rl.suggestedLevel}</span>
                      </div>
                      <div style={{ color: "#334155", marginTop: "4px" }}>{rl.content}</div>
                      <div style={{ color: "#15803d", marginTop: "2px" }}>豁免：{rl.exemptionConditions}</div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
