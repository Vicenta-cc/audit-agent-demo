import { useEffect, useRef } from "react";

export function DeleteSessionDialog({ title, busy, error, onCancel, onConfirm }: {
  title: string;
  busy: boolean;
  error: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  return <dialog ref={dialog} className="inv-delete-dialog" aria-labelledby="inv-delete-title"
    aria-describedby="inv-delete-description" onCancel={(event) => {
      event.preventDefault();
      if (!busy) onCancel();
    }}>
    <h2 id="inv-delete-title">删除会话及报告</h2>
    <p>确定删除“{title}”？</p>
    <p id="inv-delete-description">该会话的全部历史、关联报告和报告问答记录将一起永久删除，删除后无法恢复。</p>
    {error ? <p role="alert" className="inv-delete-error">{error}</p> : null}
    <div className="inv-delete-actions">
      <button type="button" className="mt-button" disabled={busy} autoFocus onClick={onCancel}>取消</button>
      <button type="button" className="mt-button inv-delete-confirm" disabled={busy} onClick={onConfirm}>
        {busy ? "正在删除…" : "永久删除"}
      </button>
    </div>
  </dialog>;
}
