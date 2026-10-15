import type { RegionSelectionHistoryEntry } from "../../types/investigationCreation";

export function RegionSelectionHistoryCard({ entry }: { entry: RegionSelectionHistoryEntry }) {
  const candidates = entry.previous_state?.candidates || [];
  const selectedInCandidates = candidates.some(candidate => candidate.name === entry.selected_target.name);
  const previous = entry.previous_target?.name || entry.previous_state?.target_text || "尚未确定";
  const selected = `${entry.selected_target.name}${entry.selected_province.name !== entry.selected_target.name ? `（${entry.selected_province.name}）` : ""}`;
  const date = new Date(entry.created_at);
  return <section className="region-selection-history-card" aria-label="地域选择历史" data-event-id={entry.event_id}>
    <div className="region-selection-history-heading">
      <strong>地域选择记录</strong><span>系统操作</span>
      <time dateTime={entry.created_at}>{Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", { hour12: false })}</time>
    </div>
    <p>{entry.question}</p>
    {entry.previous_state?.reason ? <p className="task-suggestion-help">{entry.previous_state.reason}</p> : null}
    <div className="task-suggestion-platforms" aria-label="当时的地域选项">
      {candidates.map(candidate => {
        const chosen = candidate.name === entry.selected_target.name;
        return <button key={`${candidate.level}:${candidate.name}:${candidate.province_name}`}
          type="button" disabled aria-pressed={chosen} className={chosen ? "is-selected" : ""}>
          {candidate.name}{candidate.province_name && candidate.province_name !== candidate.name ? `（${candidate.province_name}）` : ""}{chosen ? " · 已选择" : ""}
        </button>;
      })}
      <button type="button" disabled aria-pressed={!selectedInCandidates} className={!selectedInCandidates ? "is-selected" : ""}>
        {selectedInCandidates ? "其他地域" : `其他地域：${selected} · 已选择`}
      </button>
    </div>
    <p aria-label="地域修改结果">{previous} → {selected}</p>
  </section>;
}
