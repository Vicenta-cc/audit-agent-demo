import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { BookOpen, ChevronRight, X } from "lucide-react";
import type { RuleSetProposalPresentation } from "../../types/investigationCreation";
import { AssistantMarkdown } from "./AssistantMarkdown";
import "./generatedRules.css";

const stageNames: Record<string, string> = {
  image_evidence: "图片证据提取", video_frame_evidence: "视频关键帧提取",
  comment_audit: "评论研判", fusion_audit: "融合研判",
};
const riskNames: Record<string, string> = { high: "高风险", medium: "中风险", low: "低风险", none: "无风险" };

// The reply remains the source of search text until a draft is adopted.
// Only a clearly labelled Markdown section is excerpted; the full narrative stays accessible.
function searchExcerpt(text: string) {
  const lines = text.split("\n");
  const start = lines.findIndex(line => (/^#{1,6}\s+/.test(line) || /^\*\*[^*]+\*\*[:：]?\s*$/.test(line))
    && /搜索词|召回词|黑话库|lexicon/i.test(line) && !/规则/.test(line));
  if (start < 0) return "";
  const depth = lines[start].match(/^#+/)?.[0].length || 6;
  let end = start + 1;
  while (end < lines.length) {
    const heading = lines[end].match(/^(#{1,6})\s+/);
    if (heading && heading[1].length <= depth) break;
    if (/^\*\*[^*]+\*\*[:：]?\s*$/.test(lines[end]) || /^\s*---\s*$/.test(lines[end])) break;
    end++;
  }
  return lines.slice(start, end).join("\n").replace(/\n\s*---\s*$/, "").trim();
}

export function GeneratedRulesMessage({ content, presentations }: {
  content: string; presentations: RuleSetProposalPresentation[];
}) {
  const [selected, setSelected] = useState<RuleSetProposalPresentation | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const [allTerms, setAllTerms] = useState(false);
  useEffect(() => {
    if (selected && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [selected]);
  const snapshots = presentations.filter(item => item.snapshot);
  if (!snapshots.length) return <div className="inv-assistant-text" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{content}</div>;
  const narrative = presentations.reduce((text, item) => item.text ? text.replace(item.text, "") : text, content).trim();
  const excerpt = searchExcerpt(narrative);
  const fencedTerms = excerpt.match(/```[^\n]*\n([\s\S]*?)```/);
  const terms = fencedTerms?.[1].split("\n").map(term => term.trim()).filter(Boolean) || [];
  const ruleset = selected?.snapshot?.content;
  return <div className="generated-resources">
    {excerpt ? <section className="generated-search" aria-label="本次生成的搜索词">
      {terms.length ? <>
        <h3>黑话库 · 搜索词 <span>共 {terms.length} 个</span></h3>
        <div className="generated-search-tags">{(allTerms ? terms : terms.slice(0, 6)).map((term, index) => <span key={index}>{term}</span>)}</div>
        {terms.length > 6 && <button type="button" className="generated-terms-toggle" aria-expanded={allTerms} onClick={() => setAllTerms(!allTerms)}>{allTerms ? "收起搜索词" : `展开全部 ${terms.length} 个搜索词`}</button>}
      </> : <AssistantMarkdown content={excerpt} />}
    </section> : narrative ? <AssistantMarkdown content={narrative} /> : null}
    {snapshots.map((item, index) => {
      const data = item.snapshot!.content;
      const count = data.categories.reduce((total, category) => total + category.rules.length, 0);
      return <section className="generated-rules-summary" key={item.presentation_id || index} aria-label="生成的规则概览">
        <div className="generated-summary-label"><BookOpen size={16} />审核规则 <span>{data.categories.length} 类 · {count} 条</span></div>
        <h3>{data.name}</h3>
        <p>{data.audit_goal}</p>
        <div className="generated-category-tags">{data.categories.map(category => <span key={category.category_id}>{category.name}</span>)}</div>
        <div className="generated-summary-footer"><span>通用豁免 {data.general_exemptions.length} 条 · 版本 {item.snapshot!.version}</span>
          <button type="button" onClick={() => setSelected(item)}>查看完整规则<ChevronRight size={15} /></button></div>
      </section>;
    })}
    {excerpt && <details className="generated-original"><summary>查看生成说明</summary><AssistantMarkdown content={narrative} /></details>}
    {createPortal(<dialog ref={dialog} className="generated-rules-dialog" aria-labelledby="generated-rules-title"
      onClose={() => setSelected(null)} onClick={event => { if (event.target === dialog.current) dialog.current?.close(); }}>
      <div className="generated-dialog-shell">
        <header><div><BookOpen size={19} /><h2 id="generated-rules-title">完整审核规则</h2></div><button type="button" aria-label="关闭完整规则" onClick={() => dialog.current?.close()}><X size={20} /></button></header>
        {ruleset && <div className="generated-dialog-body">
          <div className="generated-rules-intro"><h3>{ruleset.name}</h3><p>{ruleset.audit_goal}</p></div>
          {ruleset.general_exemptions.length > 0 && <section className="generated-exemptions"><h3>通用豁免条件 <small>适用于这套审核规则中的全部规则</small></h3>
            {ruleset.general_exemptions.map(ex => <p key={ex.exemption_id}><strong>{ex.name}：</strong>{ex.condition}</p>)}
          </section>}
          {ruleset.categories.map(category => <section className="generated-rule-category" key={category.category_id}>
            <h3>所属风险类型：{category.name}<small>（{category.rules.length} 条）</small></h3>
            {category.rules.map(rule => <article className="generated-rule-card" key={rule.rule_id}>
              <div className="generated-rule-title"><h4>{rule.name}</h4><span className={`generated-risk is-${rule.suggested_risk_level}`}>{riskNames[rule.suggested_risk_level] || "未指定风险等级"}</span>{!rule.enabled && <small>未启用</small>}</div>
              <p><strong>命中：</strong>{rule.hit_condition}</p>
              {rule.rule_exemptions.map(ex => <p key={ex.exemption_id}><strong className="generated-exemption-label">豁免：</strong>{ex.condition}</p>)}
              {rule.adjudication_notes && <details className="generated-rule-notes"><summary>判断注意事项</summary><p>{rule.adjudication_notes}</p></details>}
              {rule.application_stages.length > 0 && <footer>{rule.application_stages.map(stage => stageNames[stage] || "其他研判阶段").join(" · ")}</footer>}
            </article>)}
          </section>)}
        </div>}
      </div>
    </dialog>, document.body)}
  </div>;
}
