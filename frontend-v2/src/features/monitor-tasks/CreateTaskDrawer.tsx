import { FilePlus2, X } from "lucide-react";
import { Button } from "../../components/common/Button";
import { IconButton } from "../../components/common/IconButton";

interface CreateTaskDrawerProps {
  open: boolean;
  onClose: () => void;
}

export function CreateTaskDrawer({ open, onClose }: CreateTaskDrawerProps) {
  if (!open) {
    return null;
  }

  return (
    <div className="drawer-backdrop" role="presentation">
      <aside className="task-detail-drawer create-task-drawer" role="dialog" aria-modal="true" aria-labelledby="create-task-title">
        <header className="drawer-header">
          <div>
            <span className="drawer-kicker">新建监控任务</span>
            <h2 id="create-task-title">创建入口已接入</h2>
            <p>正式创建表单将在下一阶段复用现有任务创建 API 与参数结构。</p>
          </div>
          <IconButton type="button" aria-label="关闭新建任务" onClick={onClose}>
            <X size={20} />
          </IconButton>
        </header>
        <div className="drawer-body">
          <div className="create-placeholder">
            <FilePlus2 size={34} />
            <strong>当前仅开放任务运行列表与操作台</strong>
            <span>本次没有修改 POST /api/jobs 的 payload，也没有实现新的创建流程。</span>
            <Button type="button" variant="primary" onClick={onClose}>
              知道了
            </Button>
          </div>
        </div>
      </aside>
    </div>
  );
}
