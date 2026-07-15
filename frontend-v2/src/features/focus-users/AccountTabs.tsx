export type AccountTabKey = "overview" | "risks" | "relations" | "timeline" | "plan";

const tabs: Array<{ key: AccountTabKey; label: string }> = [
  { key: "overview", label: "概览" },
  { key: "risks", label: "风险产出" },
  { key: "relations", label: "关联账号" },
  { key: "timeline", label: "监控时间线" },
  { key: "plan", label: "监控计划" }
];

interface AccountTabsProps {
  activeTab: AccountTabKey;
  onChange: (key: AccountTabKey) => void;
}

export function AccountTabs({ activeTab, onChange }: AccountTabsProps) {
  return (
    <nav className="account-tabs" aria-label="账号详情标签页">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          className={`account-tab${activeTab === tab.key ? " is-active" : ""}`}
          onClick={() => onChange(tab.key)}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  );
}
