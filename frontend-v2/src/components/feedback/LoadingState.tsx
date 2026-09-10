import { Loader2 } from "lucide-react";

interface LoadingStateProps {
  label?: string;
}

export function LoadingState({ label = "正在加载数据..." }: LoadingStateProps) {
  return (
    <div className="mt-state-panel">
      <Loader2 className="spin" size={24} />
      <span>{label}</span>
    </div>
  );
}
