import { useEffect, useState } from "react";
import { fetchCrawlerAccounts } from "../../services/crawlerAccounts";
import type { CrawlerAccount } from "../../types/crawlerAccounts";
import type { ConfirmationPreview, InvestigationTaskParameters } from "../../types/investigationCreation";

export function TaskParametersForm({ preview, onSave, onDirty, disabled = false, globalSettings = false }: {
  preview: Pick<ConfirmationPreview, "requested_parameters" | "effective_parameters" | "platform" | "mode" | "resolved_search_terms" | "estimated_max_contents">;
  onSave: (parameters: InvestigationTaskParameters) => Promise<void>;
  onDirty: (dirty: boolean) => void;
  disabled?: boolean;
  globalSettings?: boolean;
}) {
  const initial = preview.requested_parameters || preview.effective_parameters;
  const [values, setValues] = useState(initial);
  const [accounts, setAccounts] = useState<CrawlerAccount[]>([]);
  const [accountError, setAccountError] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const initialKey = JSON.stringify(initial ?? null);
  const [syncedKey, setSyncedKey] = useState(initialKey);
  useEffect(() => {
    if (dirty || initialKey === syncedKey) return;
    setValues(initial);
    setSyncedKey(initialKey);
    onDirty(false);
  }, [dirty, initial, initialKey, onDirty, syncedKey]);
  useEffect(() => {
    let live = true;
    fetchCrawlerAccounts().then(items => { if (live) setAccounts(items.filter(a => a.platform === preview.platform)); })
      .catch(() => { if (live) setAccountError("账号列表读取失败，请刷新后重试。"); });
    return () => { live = false; };
  }, [preview.platform]);
  if (!values) return <p role="alert">任务参数未加载，请刷新后重试。</p>;
  const change = <K extends keyof InvestigationTaskParameters>(key: K, value: InvestigationTaskParameters[K]) => {
    setValues({ ...values, [key]: value }); setDirty(true); onDirty(true); setError("");
  };
  const numeric = (key: keyof InvestigationTaskParameters, label: string, min: number, max: number, unit: string, note: string, inactive = false) => (
    <label className="investigation-parameter-field">
      <span>{label}</span>
      <span><input aria-label={label} type="number" min={min} max={max} step="1" required
        disabled={inactive} value={Number.isNaN(values[key]) ? "" : Number(values[key])}
        onChange={event => change(key, event.target.value === "" ? NaN : Number(event.target.value))} /> {unit}</span>
      <small>{min}–{max}；{note}</small>
    </label>
  );
  const toggle = (key: keyof InvestigationTaskParameters, label: string, inactive = false) => (
    <label className="investigation-parameter-check"><input type="checkbox" checked={Boolean(values[key])}
      disabled={inactive} onChange={event => change(key, event.target.checked)} />{label}</label>
  );
  const effective = preview.effective_parameters;
  const keywordCount = Math.max(1, preview.resolved_search_terms.length);
  const plannedCount = Math.min(values.max_total_notes, values.max_notes * keywordCount);
  return <form className="investigation-parameters" aria-label="采集与分析参数" onSubmit={async event => {
    event.preventDefault(); setSaving(true); setError("");
    try {
      await onSave({ ...values, auto_analyze: true, analyze_limit: values.max_total_notes });
      setDirty(false); onDirty(false);
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "参数保存失败"); }
    finally { setSaving(false); }
  }}>
    <h4>采集与分析参数</h4>
    <p>{globalSettings ? "保存后，后续确认启动的任务统一使用这些参数；已启动任务保持原参数。服务端安全上限仍然生效。" : "当前显示已保存值；修改后请保存并核对实际生效值。"}</p>
    <fieldset disabled={saving || disabled}>
      <legend>内容与账号</legend>
      <label className="investigation-parameter-field"><span>采集账号</span><select aria-label="采集账号"
        value={values.crawler_account_id || ""} onChange={event => change("crawler_account_id", event.target.value || null)}>
        <option value="">自动选择可用账号</option>
        {accounts.map(account => <option key={account.id} value={account.id} disabled={account.status !== "active" || !account.hasAuthState}>
          {account.displayName}{account.status !== "active" || !account.hasAuthState ? "（不可用）" : ""}
        </option>)}
      </select></label>
      {accountError ? <p role="alert">{accountError}</p> : null}
      <div className="investigation-parameter-grid">
        {numeric("max_notes", preview.mode === "search" ? "每个关键词采集上限" : "本任务采集上限", 1, 5, "条", "单任务仍受总采集上限约束")}
        {numeric("max_total_notes", "单任务总采集上限", 1, 5, "条", "达到后停止后续关键词")}
        {numeric("start_page", "起始页", 1, 10000, "页", "默认第 1 页；恢复使用检查点")}
      </div>
      <p>{globalSettings ? `关键词任务每词最多 ${values.max_notes} 条，单任务最多 ${values.max_total_notes} 条。` : `${preview.resolved_search_terms.length} 个关键词，预计最多 ${plannedCount} 条；实际采集内容全部自动审核。`}</p>
      {toggle("collect_media", "采集并审核图片与视频")}
    </fieldset>
    <fieldset disabled={saving || disabled}><legend>评论采集</legend>
      {toggle("collect_comments", "采集评论")}
      {toggle("get_sub_comment", "采集二级评论", !values.collect_comments)}
      <div className="investigation-parameter-grid">
        {numeric("max_comments", "每帖一级评论上限", 0, 1000, "条", "0 表示不采集评论", !values.collect_comments)}
      </div>
    </fieldset>
    <fieldset disabled={saving || disabled}><legend>抓帖速度</legend>
      <div className="investigation-parameter-grid">
        {numeric("max_items_per_minute", "帖子抓取速度", 1, 5, "条/分钟", "不等同于评论抓取速度")}
        {numeric("max_concurrency", "采集并发", 1, 3, "个", "默认 1；受服务端上限约束")}
      </div>
    </fieldset>
    <fieldset disabled={saving || disabled}><legend>分析设置</legend>
      <div className="investigation-parameter-grid">
        {numeric("analysis_batch_size", "分析批次", 1, 20, "条/批", "内容完成后响应暂停/停止")}
      </div>
      <small>实际采集并入库的内容会全部自动审核；审核数量不再单独设置。管理员限速、凭据和浏览器配置由服务端管理。</small>
    </fieldset>
    {effective ? <p className="investigation-effective-parameters" aria-label="已保存的实际生效参数">
      已保存生效值：采集上限 {effective.max_notes} 条{preview.mode === "search" ? "/词" : "/任务"}，单任务最多 {effective.max_total_notes} 条{globalSettings ? "" : `，本次预计最多 ${preview.estimated_max_contents} 条`}；
      评论 {effective.max_comments} 条/帖，{effective.get_sub_comment ? "含二级评论" : "仅一级评论"}；
      图片与视频{effective.collect_media ? "采集并审核" : "不采集、不审核"}；
      {effective.max_items_per_minute} 条/分钟，并发 {effective.max_concurrency}；
      实际采集内容全部自动审核，每批 {effective.analysis_batch_size} 条。
    </p> : null}
    {error ? <p role="alert">{error}</p> : null}
    <button type="submit" className="mt-button mt-button-secondary" disabled={(!dirty && !globalSettings) || saving || disabled}>{saving ? "保存中…" : globalSettings ? "保存统一设置" : "保存采集与分析参数"}</button>
    {dirty ? <span role="status"> 有未保存参数。</span> : null}
  </form>;
}
