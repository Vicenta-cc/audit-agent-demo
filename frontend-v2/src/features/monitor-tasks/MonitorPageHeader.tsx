import { Plus } from "lucide-react";
import { Button } from "../../components/common/Button";

interface MonitorPageHeaderProps {
  creating: boolean;
  onCreate: () => void;
}

export function MonitorPageHeader({ creating, onCreate }: MonitorPageHeaderProps) {
  return (
    <section className="monitor-page-header">
      <div>
        <h1>监控任务</h1>
        <p>平台抓取、直播接入、重点用户、本地视频四类来源的运行操作台</p>
      </div>
      <Button type="button" variant="primary" loading={creating} onClick={onCreate}>
        {!creating ? <Plus size={18} /> : null}
        新建监控任务
      </Button>
    </section>
  );
}
