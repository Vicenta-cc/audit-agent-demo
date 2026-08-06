import { Plus } from "lucide-react";
import { Button } from "../../components/common/Button";

interface MonitorPageHeaderProps {
  onCreate: () => void;
}

export function MonitorPageHeader({ onCreate }: MonitorPageHeaderProps) {
  return (
    <section className="monitor-page-header">
      <div>
        <h1>监控任务</h1>
        <p>平台抓取、直播接入、重点用户、本地视频四类来源的运行操作台</p>
      </div>
      <Button type="button" variant="primary" onClick={onCreate}>
        <Plus size={18} />
        新建监控任务
      </Button>
    </section>
  );
}
