import { BookOpen, Edit3, X } from "lucide-react";
import { Button } from "../../components/common/Button";
import { IconButton } from "../../components/common/IconButton";
import { formatFullDateTime } from "../../services/configCenter";
import type { ResearchPolicy } from "../../types/configCenter";

interface PolicyDetailDrawerProps {
  policy: ResearchPolicy | null;
  onClose: () => void;
  onEdit: (policy: ResearchPolicy) => void;
}

export function PolicyDetailDrawer({ policy, onClose, onEdit }: PolicyDetailDrawerProps) {
  if (!policy) {
    return null;
  }
  const enabledDetections = policy.detectionConfigs.filter((item) => item.enabled);

  return (
    <div className="config-drawer-backdrop" role="presentation" onClick={onClose}>
      <aside
        className="config-detail-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="policy-detail-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="config-drawer-header">
          <div>
            <span className="config-drawer-kicker">方案详情</span>
            <h2 id="policy-detail-title">{policy.name}</h2>
            <div className="config-drawer-meta">最近更新：{formatFullDateTime(policy.updatedAt)}</div>
          </div>
          <IconButton type="button" aria-label="关闭方案详情" onClick={onClose}>
            <X size={20} />
          </IconButton>
        </header>

        <div className="config-drawer-body">
          <section className="config-drawer-section">
            <h3>方案说明</h3>
            <p className="config-drawer-description is-plain">{policy.description || "暂无方案说明"}</p>
          </section>

          <section className="config-drawer-section">
            <h3>绑定知识库</h3>
            {policy.lexiconNames.length ? (
              <div className="drawer-chip-list">
                {policy.lexiconNames.map((name) => (
                  <span key={name}><BookOpen size={14} />{name}</span>
                ))}
              </div>
            ) : <p className="drawer-empty-copy">当前未绑定黑话库</p>}
          </section>

          <section className="config-drawer-section">
            <div className="drawer-section-heading">
              <h3>检测配置</h3>
              <span>{policy.scoringModeLabel}</span>
            </div>
            {enabledDetections.length ? (
              <div className="drawer-detection-list">
                {enabledDetections.map((item) => (
                  <div className="drawer-detection-item" key={item.scope}>
                    <strong>{item.location}</strong>
                    <span>{item.evidence}</span>
                    <em>{item.importance}</em>
                  </div>
                ))}
              </div>
            ) : <p className="drawer-empty-copy">当前未启用检测位置</p>}
          </section>

          <section className="config-drawer-section">
            <h3>引用任务</h3>
            {policy.references.length ? (
              <div className="drawer-reference-list">
                {policy.references.map((reference) => (
                  <div className="drawer-reference-item" key={reference.id}>
                    <strong>{reference.name}</strong>
                    <span>{reference.status}</span>
                  </div>
                ))}
              </div>
            ) : <p className="drawer-empty-copy">当前暂无监控任务引用该方案</p>}
          </section>
        </div>

        <footer className="config-drawer-footer">
          <Button type="button" variant="primary" onClick={() => onEdit(policy)}>
            <Edit3 size={16} />
            编辑方案
          </Button>
        </footer>
      </aside>
    </div>
  );
}
