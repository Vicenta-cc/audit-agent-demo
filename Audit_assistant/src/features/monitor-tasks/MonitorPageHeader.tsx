import { Plus, Sparkles } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "../../components/common/Button";

interface MonitorPageHeaderProps {
  onCreate: () => void;
}

export function MonitorPageHeader({ onCreate }: MonitorPageHeaderProps) {
  const navigate = useNavigate();

  return (
    <section className="monitor-page-header">
      <div>
        <h1>监控任务</h1>
        <p>平台抓取、直播接入、重点用户、本地视频四类来源的运行操作台</p>
      </div>
      <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
        <Button
          type="button"
          variant="secondary"
          onClick={() => navigate("/tasks/chat-new")}
        >
          <Sparkles size={16} />
          对话式任务创建
        </Button>
        <Button type="button" variant="primary" onClick={onCreate}>
          <Plus size={18} />
          新建监控任务
        </Button>
      </div>
    </section>
  );
}
