import { Check, ChevronDown, ChevronUp, X } from "lucide-react";
import { useState } from "react";
import { IconButton } from "../../components/common/IconButton";
import type { ConfigHistoryItem, TaskConfig } from "../../types/taskOutputs";

interface TaskConfigDrawerProps {
  config: TaskConfig;
  history: ConfigHistoryItem[];
  onClose: () => void;
}

export function TaskConfigDrawer({ config, history, onClose }: TaskConfigDrawerProps) {
  const [tab, setTab] = useState<"current" | "history">("current");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  return (
    <>
      <header className="output-drawer-header">
        <h2 id="output-drawer-title">查看配置</h2>
        <IconButton type="button" aria-label="关闭查看配置" onClick={onClose}><X size={19} /></IconButton>
      </header>
      <div className="config-drawer-tabs" role="tablist" aria-label="配置视图">
        <button type="button" role="tab" aria-selected={tab === "current"} className={tab === "current" ? "is-active" : ""} onClick={() => setTab("current")}>当前配置</button>
        <button type="button" role="tab" aria-selected={tab === "history"} className={tab === "history" ? "is-active" : ""} onClick={() => setTab("history")}>配置历史</button>
      </div>
      <div className="config-drawer-body">
        {tab === "current" ? <CurrentConfig config={config} /> : (
          <div className="config-history-list">
            {history.length ? history.map((item) => (
              <article className="config-history-item" key={item.id}>
                <div className="config-history-title">
                  <strong>版本 {item.version}</strong>
                  {item.isCurrent ? <span>当前</span> : null}
                </div>
                <dl>
                  <div><dt>更新时间</dt><dd>{item.updatedAt}</dd></div>
                  <div><dt>操作人</dt><dd>{item.operator}</dd></div>
                  <div><dt>变更摘要</dt><dd>{item.summary}</dd></div>
                </dl>
                <button type="button" onClick={() => setExpandedId((current) => current === item.id ? null : item.id)}>
                  查看详情 {expandedId === item.id ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                </button>
                {expandedId === item.id ? <p className="config-history-detail">配置快照 ID：{item.id}</p> : null}
              </article>
            )) : <div className="config-history-empty">接口未返回配置历史</div>}
          </div>
        )}
      </div>
    </>
  );
}

function CurrentConfig({ config }: { config: TaskConfig }) {
  return (
    <>
      <ConfigSection title="基本信息">
        <ConfigKv label="引用方案" value={config.referencePlan} />
        <ConfigKv label="配置版本" value={config.configVersion} />
        <ConfigKv label="更新时间" value={config.updatedAt} />
        <ConfigKv label="生效时间" value={config.effectiveAt} />
      </ConfigSection>
      <ConfigSection title="数据来源与采集范围">
        <ConfigKv label="采集平台" value={config.platform} />
        <ConfigKv label="采集时间范围" value={config.collectionTimeRange} />
        <ConfigWide label="关键词或话题" values={config.keywords} emptyText="未配置" />
        <ConfigKv label="数据类型" value={config.dataType} />
      </ConfigSection>
      <ConfigSection title="使用的风险库">
        <ConfigWide label="风险库" values={config.riskLibraries.map((item) => item.label)} emptyText="未配置" />
      </ConfigSection>
      <ConfigSection title="内容分析范围">
        <div className="config-scope-list">
          {config.analysisScopes.map((item) => (
            <span className={item.enabled ? "is-enabled" : "is-disabled"} key={item.label}>
              <Check size={14} />{item.label}
            </span>
          ))}
        </div>
      </ConfigSection>
    </>
  );
}

function ConfigSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="config-section"><h3>{title}</h3><div className="config-section-content">{children}</div></section>;
}

function ConfigKv({ label, value }: { label: string; value: string }) {
  return <div className="config-kv"><span>{label}</span><strong title={value}>{value || "--"}</strong></div>;
}

function ConfigWide({ label, values, emptyText }: { label: string; values: string[]; emptyText: string }) {
  return (
    <div className="config-wide">
      <span>{label}</span>
      <div className="config-chip-list">
        {values.length ? values.map((item) => <strong key={item}>{item}</strong>) : <em>{emptyText}</em>}
      </div>
    </div>
  );
}
