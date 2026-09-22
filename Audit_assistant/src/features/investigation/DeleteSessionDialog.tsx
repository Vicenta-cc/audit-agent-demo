import { useEffect, useRef } from "react";
import { AlertTriangle, Trash2 } from "lucide-react";

import { Button } from "../../components/common/Button";

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
    <div className="inv-delete-dialog-header">
      <span className="inv-delete-dialog-icon" aria-hidden="true"><Trash2 size={20} /></span>
      <div>
        <span className="inv-delete-dialog-kicker">删除会话</span>
        <h2 id="inv-delete-title">删除会话及报告</h2>
      </div>
    </div>
    <div className="inv-delete-dialog-body">
      <p className="inv-delete-question">确定删除“<strong>{title}</strong>”？</p>
      <div className="inv-delete-warning">
        <AlertTriangle size={17} aria-hidden="true" />
        <p id="inv-delete-description">该会话的全部历史、关联报告和报告问答记录将一起永久删除，删除后无法恢复。</p>
      </div>
      {error ? <p role="alert" className="inv-delete-error">{error}</p> : null}
      <div className="inv-delete-actions">
        <Button type="button" variant="secondary" size="medium" disabled={busy} autoFocus onClick={onCancel}>取消</Button>
        <Button type="button" variant="danger" size="medium" loading={busy} onClick={onConfirm}>
          {busy ? "正在删除…" : "永久删除"}
        </Button>
      </div>
    </div>
  </dialog>;
}
