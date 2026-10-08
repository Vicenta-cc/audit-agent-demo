import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../services/apiClient';
import { loadSessionResources, readSessionResource, recordResourceSelection, type ResourceState,
  type SelectionRequest, type SessionResource, type SelectionPurpose } from '../../services/sessionResources';
import type { RulesContent, LexiconContent } from '../../services/resourceLibrary';
import './sessionResourceSelection.css';

/** Phase 1B opt-in UI. This records choices; it never changes draft/run configuration. */
export function SessionResourceSelection({ sessionId }: { sessionId: string }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<ResourceState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pending, setPending] = useState<SelectionRequest | null>(null);
  const [notice, setNotice] = useState('');
  const alive = useRef(true);
  const working = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  async function refresh() {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    try {
      const loaded = await loadSessionResources(sessionId);
      if (alive.current) { setState(loaded); setError(''); }
    } catch {
      if (alive.current) setError('资源状态读取失败，请刷新重试。');
    } finally {
      working.current = false;
      if (alive.current) setBusy(false);
    }
  }
  useEffect(() => { if (open) void refresh(); }, [open]);

  async function submit(body: SelectionRequest) {
    if (working.current) return;
    working.current = true;
    setBusy(true); setError(''); setNotice(''); setPending(body);
    let acknowledged = false;
    try {
      await recordResourceSelection(sessionId, body);
      acknowledged = true;
      if (!alive.current) return;
      setPending(null);
      // Replayed older receipts must never overwrite a newer selection on screen.
      const loaded = await loadSessionResources(sessionId);
      if (alive.current) { setState(loaded); setNotice('选择已记录。'); }
    } catch (err) {
      if (!alive.current) return;
      if (acknowledged) {
        setError('选择已记录，但资源状态暂时无法刷新，请稍后重新读取。');
      } else if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        setPending(null);
        setError('资源或选择已变化，请刷新后重新核对。');
      } else {
        setError('暂时无法确认选择状态，请刷新查看；重试会沿用同一次操作。');
      }
    } finally {
      working.current = false;
      if (alive.current) setBusy(false);
    }
  }
  function select(item: SessionResource, purpose: SelectionPurpose) {
    if (!item.kind || !item.content_hash) return;
    const slot = state?.selection.slots?.find(s => s.kind === item.kind && s.purpose === purpose);
    void submit({ event_id: crypto.randomUUID(), expected_event_id: slot?.event_id || '',
      kind: item.kind, purpose, key: item.key, content_hash: item.content_hash });
  }
  const slots = state?.selection.slots || [];
  const disabled = busy || !!pending || !!error || state?.status !== 'complete';
  return <details className="session-resource-selection" open={open} onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>会话资源选择</summary>
    {open && <div className="session-resource-body">
      <p>记录查看或编辑的对象，不会采用资源或启动任务。当前验收阶段，聊天仍按原流程处理。</p>
      <div className="session-resource-actions"><button type="button" disabled={busy} onClick={() => void refresh()}>刷新资源</button>
        {pending && <button type="button" disabled={busy} onClick={() => void submit(pending)}>重试记录</button>}
        {busy && <span role="status">正在读取或记录…</span>}
      </div>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {state?.status === 'partial' && <p role="alert">资源目录暂不完整，请恢复后再选择。</p>}
      {state && <section aria-label="已记录的选择">
        {!slots.length && <p>尚未记录明确选择。</p>}
        {slots.map(slot => <div className="session-resource-slot" key={`${slot.kind}-${slot.purpose}`}>
          <span>{slot.kind === 'ruleset' ? '规则' : '词库'} · {slot.purpose === 'edit' ? '编辑对象' : '查看对象'}：</span>
          <strong>{slot.status === 'unavailable' ? '原版本暂不可读取' : slot.status === 'cleared' ? '未选择' : `${slot.title} · v${slot.version}`}</strong>
          {slot.status === 'stale' && <span>（已有新版，请重新选择）</span>}
          {slot.status !== 'cleared' && <button type="button" disabled={disabled} onClick={() => void submit({
            event_id: crypto.randomUUID(), expected_event_id: slot.event_id, kind: slot.kind,
            purpose: slot.purpose, key: '', content_hash: '' })}>取消选择</button>}
        </div>)}
      </section>}
      <ul>{state?.items.filter(item => item.type === 'edit_version').map(item => <li key={item.key}>
        <span>{item.kind === 'ruleset' ? '规则' : '词库'} · {item.title} · v{item.version}
          {item.is_current ? '（当前编辑稿）' : '（历史版本）'}</span>
        <div className="session-resource-actions">
          <button type="button" disabled={disabled} onClick={() => select(item, 'view')}>选为查看对象</button>
          {item.is_current && <button type="button" disabled={disabled} onClick={() => select(item, 'edit')}>选为编辑对象</button>}
        </div>
        <ResourceVersionPreview sessionId={sessionId} item={item} />
      </li>)}</ul>
      {state && !state.items.some(i => i.type === 'edit_version') && <p>本会话暂无可选择的编辑稿。</p>}
    </div>}
  </details>;
}

function ResourceVersionPreview({ sessionId, item }: { sessionId: string; item: SessionResource }) {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState<RulesContent | LexiconContent | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!open) return;
    let active = true;
    readSessionResource(sessionId, item.key).then(detail => {
      if (active) setContent(detail.content);
    }).catch(() => { if (active) setError('该版本内容暂时无法读取。'); });
    return () => { active = false; };
  }, [open, sessionId, item.key]);
  const rules = item.kind === 'ruleset' ? content as RulesContent | null : null;
  const words = item.kind === 'lexicon' ? content as LexiconContent | null : null;
  return <details className="session-resource-preview" open={open} onToggle={e => { setError(''); setOpen(e.currentTarget.open); }}>
    <summary>查看版本内容</summary>
    {open && <div>
      {error ? <p role="alert">{error}</p> : !content ? <p>正在读取…</p> : <>
        {rules && <><p>{rules.audit_goal}</p>
          {rules.general_exemptions.map(ex => <p key={ex.exemption_id}>豁免：{ex.name} · {ex.condition}</p>)}
          {rules.categories.map(category => <section key={category.category_id}><h4>{category.name}</h4>
            {category.rules.map(rule => <p key={rule.rule_id}><strong>{rule.name}</strong>：{rule.hit_condition}</p>)}
          </section>)}
        </>}
        {words && <><p>{words.description}</p>{words.entries.map(entry => <p key={entry.id}>
          {entry.kind === 'variant' ? '变体' : entry.kind === 'tag' ? '标签' : '主词'}：{entry.term}{!entry.enabled && '（停用）'}
        </p>)}</>}
      </>}
    </div>}
  </details>;
}
