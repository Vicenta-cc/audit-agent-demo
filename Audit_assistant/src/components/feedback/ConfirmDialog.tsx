import { AlertTriangle } from "lucide-react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmText: string;
  cancelText?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmText,
  cancelText = "取消",
  onConfirm,
  onCancel
}: ConfirmDialogProps) {
  if (!open) {
    return null;
  }

  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title">
        <div className="dialog-icon" aria-hidden="true">
          <AlertTriangle size={22} />
        </div>
        <div className="dialog-content">
          <h2 id="confirm-dialog-title">{title}</h2>
          <p>{description}</p>
        </div>
        <div className="dialog-actions">
          <button className="btn btn-secondary" type="button" onClick={onCancel}>
            {cancelText}
          </button>
          <button className="btn btn-danger" type="button" onClick={onConfirm}>
            {confirmText}
          </button>
        </div>
      </section>
    </div>
  );
}
