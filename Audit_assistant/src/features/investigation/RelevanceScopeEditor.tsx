import { useEffect, useId, useRef, useState } from "react";
import { Info, MapPin, PencilLine } from "lucide-react";
import { ApiError } from "../../services/apiClient";
import type { ConfirmationPreview, RelevanceScopeState } from "../../types/investigationCreation";
import { buildRelevanceScopeView, formatCreationErrorMessage } from "./confirmationView";

export interface RelevanceScopeEditorProps {
  preview?: ConfirmationPreview;
  onSave?: (targetText: string, operationId: string, clarificationTargetText?: string) => Promise<void>;
  disabled?: boolean;
}

/** System clarification driven by public catalog results, separate from AI prose. */
export function RelevanceScopeEditor({ preview, onSave, disabled = false }: RelevanceScopeEditorProps) {
  const view = buildRelevanceScopeView(preview);
  const inputId = useId();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [resolution, setResolution] = useState<RelevanceScopeState>();
  const inFlight = useRef(false);
  const retry = useRef<{ identity: string; operationId: string }>();
  useEffect(() => {
    setEditing(false); setText(""); setError(""); setResolution(undefined);
    retry.current = undefined;
  }, [preview?.draft_id, preview?.draft_revision]);
  if (!view || !preview) return null;
  const state = resolution || preview.relevance_scope_state;
  const choosing = editing || view.pending;
  const candidates = state?.candidates.filter(candidate => ["province", "prefecture"].includes(candidate.level)) || [];
  const locked = disabled || saving;
  const clarifying = state?.status === "pending";
  const save = async (target: string) => {
    const targetText = target.trim();
    if (!onSave || locked || inFlight.current || !targetText) return;
    const clarificationTargetText = resolution?.target_text || undefined;
    const identity = JSON.stringify([preview.draft_id, preview.draft_revision, targetText, clarificationTargetText]);
    if (retry.current?.identity !== identity) retry.current = { identity, operationId: crypto.randomUUID() };
    inFlight.current = true; setSaving(true); setError("");
    try {
      await onSave(targetText, retry.current.operationId, clarificationTargetText);
      setEditing(false); setResolution(undefined); setText("");
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 422 && failure.details.mutation_applied === false) {
        const next = failure.details.region_resolution as RelevanceScopeState | undefined;
        if (next?.status === "pending" && Array.isArray(next.candidates)) setResolution(next);
        setError(failure.message);
      } else {
        setError(failure instanceof ApiError && failure.status === 409
          ? formatCreationErrorMessage("版本已变化") : "地域暂时无法保存，请重试；原有配置未在界面中改写。");
      }
    } finally { inFlight.current = false; setSaving(false); }
  };
  return <section className={`relevance-scope-editor${choosing ? " is-editing" : ""}`} aria-label="调查地域配置">
    <div className="relevance-scope-heading">
      <div className="relevance-scope-identity">
        <span className="relevance-scope-icon"><MapPin size={17} aria-hidden="true" /></span>
        <div className="relevance-scope-summary">
          <div className="relevance-scope-label">{view.pending ? "最新地域要求（待澄清）" : "调查地域"}</div>
          <p className="relevance-scope-value">{view.target}</p>
        </div>
      </div>
      {onSave && !choosing ? <button type="button" className="relevance-scope-edit" disabled={locked} onClick={() => setEditing(true)}>
        <PencilLine size={14} aria-hidden="true" />修改调查地域
      </button> : choosing ? <span className={`relevance-scope-status${clarifying ? " is-pending" : ""}`}>{clarifying ? "待确认" : "编辑中"}</span> : null}
    </div>
    {view.policy ? <p className="relevance-scope-policy">选择策略：{view.policy}</p> : null}
    {onSave && choosing ? <div aria-label="地域澄清" className="relevance-scope-panel">
      <div className="relevance-scope-panel-heading">
        <p className="relevance-scope-question">{clarifying ? (state?.target_text ? `“${state.target_text}”指哪个调查范围？` : "请选择或填写调查地域") : "修改调查地域"}</p>
        <p className="relevance-scope-description">{clarifying ? "点击候选直接确认，也可以填写其他省市。" : "填写新的省市名称，保存后将直接更新草案。"}</p>
      </div>
      {state?.reason && state.reason !== error ? <p className="relevance-scope-description">{state.reason}</p> : null}
      {clarifying ? <div className="relevance-scope-candidates">
        {candidates.map(candidate => <button key={`${candidate.level}:${candidate.name}:${candidate.province_name}`}
          type="button" className="relevance-scope-candidate" disabled={locked}
          aria-label={`${candidate.name}${candidate.province_name && candidate.province_name !== candidate.name ? `（${candidate.province_name}）` : ""}`}
          onClick={() => void save(candidate.name)}>
          {candidate.name}{candidate.province_name && candidate.province_name !== candidate.name ? <span>（{candidate.province_name}）</span> : null}
        </button>)}
        <button type="button" className="relevance-scope-other" disabled={locked} onClick={() => setEditing(true)}>其他地域</button>
      </div> : null}
      {(editing || candidates.length === 0) ? <form className="relevance-scope-form" onSubmit={event => { event.preventDefault(); void save(text); }}>
        <label htmlFor={inputId}>省级地域或城市完整名称</label>
        <div className="relevance-scope-input-row">
          <input id={inputId} aria-describedby={`${inputId}-help`} value={text} disabled={locked} onChange={event => { setText(event.target.value); setResolution(undefined); setError(""); }} placeholder="例如：北京市、吉林省、吉林市" />
          <div className="relevance-scope-actions">
            <button type="submit" className="relevance-scope-save" disabled={locked || !text.trim()}>{saving ? "正在保存…" : "验证并保存地域"}</button>
            {!view.pending ? <button type="button" className="relevance-scope-cancel" disabled={locked} onClick={() => { setEditing(false); setResolution(undefined); setError(""); setText(""); }}>取消修改</button> : null}
          </div>
        </div>
        <p id={`${inputId}-help`} className="relevance-scope-description">支持省级地域、地级市和直辖市；暂不支持区县、街道，不会自动扩大范围。</p>
      </form> : null}
      {saving ? <p className="relevance-scope-description" role="status">正在保存地域…</p> : null}
      {error ? <p className="relevance-scope-error" role="alert">{error}</p> : null}
    </div> : <>
      {view.reason ? <p className="relevance-scope-description">待澄清原因：{view.reason}</p> : null}
      {view.candidates ? <p className="relevance-scope-description">候选地域：{view.candidates}</p> : null}
    </>}
    <p className="relevance-scope-notice" aria-label="地域配置说明"><Info size={14} aria-hidden="true" /><span>{onSave && view.pending ? "请选择候选地域，或填写其他省市名称。确认前不能启动调查。" : view.notice}</span></p>
  </section>;
}
