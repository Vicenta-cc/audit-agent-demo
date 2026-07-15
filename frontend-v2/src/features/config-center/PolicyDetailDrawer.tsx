import { useState } from "react";
import type { ReactNode } from "react";
import { BookOpen, ChevronDown, Copy, Edit3, History, Layers, Target, X } from "lucide-react";
import { Button } from "../../components/common/Button";
import { DropdownMenu } from "../../components/common/DropdownMenu";
import { IconButton } from "../../components/common/IconButton";
import { StatusTag } from "../../components/common/StatusTag";
import {
  formatFullDateTime,
  getPolicyStatusLabel,
  getPolicyStatusTone
} from "../../services/configCenter";
import type { ResearchPolicy } from "../../types/configCenter";

interface PolicyDetailDrawerProps {
  policy: ResearchPolicy | null;
  onClose: () => void;
  onEdit: (policy: ResearchPolicy) => void;
  onCopy: (policy: ResearchPolicy) => void;
  onToggleStatus: (policy: ResearchPolicy) => void;
  onViewHistory: (policy: ResearchPolicy) => void;
}

export function PolicyDetailDrawer({
  policy,
  onClose,
  onEdit,
  onCopy,
  onToggleStatus,
  onViewHistory
}: PolicyDetailDrawerProps) {
  const [menuOpen, setMenuOpen] = useState(false);

  if (!policy) {
    return null;
  }

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
            <div className="config-drawer-meta">
              <StatusTag tone={getPolicyStatusTone(policy.status)}>{getPolicyStatusLabel(policy.status)}</StatusTag>
              <span>ID：{policy.id}</span>
            </div>
          </div>
          <IconButton type="button" aria-label="关闭方案详情" onClick={onClose}>
            <X size={20} />
          </IconButton>
        </header>

        <div className="config-drawer-body">
          <section className="config-drawer-section">
            <h3>基本信息</h3>
            <div className="config-drawer-kv-grid">
              <DrawerKv label="风险分类" value={policy.category} />
              <DrawerKv label="适用场景" value={policy.scenarioTags.join("、")} />
            </div>
            <p className="config-drawer-description">{policy.description}</p>
          </section>

          <section className="config-drawer-section">
            <h3>配置摘要</h3>
            <DrawerGroup icon={<BookOpen size={18} />} label="绑定知识库" values={policy.lexiconNames} />
            <DrawerGroup icon={<Layers size={18} />} label="内容范围" values={policy.contentScopes} />
            <DrawerGroup icon={<Target size={18} />} label="识别能力" values={policy.recognitionCapabilities} />
          </section>

          <section className="config-drawer-section">
            <h3>引用信息</h3>
            <div className="reference-summary">
              <strong>{policy.references.length} 个监控任务</strong>
              <span>{policy.references.length ? "最近引用任务预览" : "暂未被监控任务引用"}</span>
            </div>
            {policy.references.length ? (
              <div className="drawer-reference-list">
                {policy.references.slice(0, 4).map((reference) => (
                  <div className="drawer-reference-item" key={reference.id}>
                    <strong>{reference.name}</strong>
                    <span>{reference.status}</span>
                  </div>
                ))}
              </div>
            ) : null}
            <button className="config-text-button drawer-inline-action" type="button">
              查看全部引用
            </button>
          </section>

          <section className="config-drawer-section">
            <h3>版本信息</h3>
            <div className="config-drawer-kv-grid">
              <DrawerKv label="当前版本" value={policy.version.version} />
              <DrawerKv label="最近更新时间" value={formatFullDateTime(policy.updatedAt)} />
              <DrawerKv label="最近更新人" value={policy.updatedBy} />
            </div>
            <button className="config-text-button drawer-inline-action" type="button" onClick={() => onViewHistory(policy)}>
              查看版本历史
            </button>
          </section>
        </div>

        <footer className="config-drawer-footer">
          <Button type="button" variant="primary" onClick={() => onEdit(policy)}>
            <Edit3 size={16} />
            编辑方案
          </Button>
          <Button type="button" variant="secondary" onClick={() => onCopy(policy)}>
            <Copy size={16} />
            复制方案
          </Button>
          <DropdownMenu
            open={menuOpen}
            onClose={() => setMenuOpen(false)}
            trigger={
              <Button type="button" variant="secondary" onClick={() => setMenuOpen((open) => !open)}>
                更多操作
                <ChevronDown size={16} />
              </Button>
            }
          >
            <button type="button" onClick={() => onToggleStatus(policy)}>
              {policy.status === "published" ? "停用方案" : "发布方案"}
            </button>
            <button type="button" onClick={() => onViewHistory(policy)}>
              <History size={15} />
              查看版本历史
            </button>
          </DropdownMenu>
        </footer>
      </aside>
    </div>
  );
}

interface DrawerKvProps {
  label: string;
  value: string;
}

function DrawerKv({ label, value }: DrawerKvProps) {
  return (
    <div className="config-drawer-kv">
      <span>{label}</span>
      <strong title={value}>{value || "-"}</strong>
    </div>
  );
}

interface DrawerGroupProps {
  icon: ReactNode;
  label: string;
  values: string[];
}

function DrawerGroup({ icon, label, values }: DrawerGroupProps) {
  return (
    <div className="drawer-config-group">
      <div className="drawer-config-group-title">
        <span aria-hidden="true">{icon}</span>
        <strong>{label}</strong>
      </div>
      <div className="drawer-chip-list">
        {values.length ? values.map((item) => <span key={item}>{item}</span>) : <span>未配置</span>}
      </div>
    </div>
  );
}
