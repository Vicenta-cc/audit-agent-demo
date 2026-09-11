import { useEffect, useRef, useState } from 'react';

export function ResourceDialog({ title, description, busy = false, error = '', inputLabel, initialValue = '', confirmText = '确认删除', onCancel, onConfirm }: {
  title: string; description?: string; busy?: boolean; error?: string; inputLabel?: string; initialValue?: string;
  confirmText?: string; onCancel: () => void; onConfirm: (value: string) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [value, setValue] = useState(initialValue);
  useEffect(() => { const dialog = ref.current; dialog?.showModal(); return () => dialog?.close(); }, []);
  return <dialog ref={ref} aria-labelledby="resource-dialog-title" onCancel={e => { e.preventDefault(); if (!busy) onCancel(); }}
    style={{ border: '1px solid #e2e8f0', borderRadius: 12, padding: 24, maxWidth: 460, boxShadow: '0 20px 60px #0003' }}>
    <form onSubmit={e => { e.preventDefault(); onConfirm(value.trim()); }}>
      <h2 id="resource-dialog-title" style={{ fontSize: 18 }}>{title}</h2>
      {description ? <p style={{ color: '#64748b', lineHeight: 1.7 }}>{description}</p> : null}
      {inputLabel ? <label>{inputLabel}<input autoFocus required maxLength={160} value={value} onChange={e => setValue(e.target.value)} style={{ display: 'block', width: '100%', margin: '12px 0', padding: 8 }} /></label> : null}
      {error ? <p role="alert" style={{ color: '#dc2626' }}>{error}</p> : null}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 24 }}>
        <button type="button" className="recall-secondary-button" autoFocus={!inputLabel} disabled={busy} onClick={onCancel}>取消</button>
        <button type="submit" className="recall-primary-button" style={!inputLabel ? { background: '#dc2626' } : {}} disabled={busy}>{busy ? '处理中…' : confirmText}</button>
      </div>
    </form>
  </dialog>;
}
