import { AlertCircle } from "lucide-react";
import { Button } from "../common/Button";

interface ErrorStateProps {
  message: string;
  onRetry: () => void;
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div className="mt-state-panel mt-error-panel">
      <AlertCircle size={24} />
      <div>
        <strong>数据加载失败</strong>
        <p>{message}</p>
      </div>
      <Button type="button" variant="primary" onClick={onRetry}>
        重试
      </Button>
    </div>
  );
}
