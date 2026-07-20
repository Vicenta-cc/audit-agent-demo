import type { CSSProperties } from "react";
import type { RiskDistributionItem } from "../../types/focusUsers";

interface DistributionPieProps {
  title: string;
  description: string;
  items: RiskDistributionItem[];
}

export function DistributionPie({ title, description, items }: DistributionPieProps) {
  const total = items.reduce((sum, item) => sum + item.count, 0);
  const gradient = buildGradient(items);
  const summary = items.map((item) => `${item.label}${item.count}条`).join("，");

  return (
    <section className="penetration-chart-section" aria-label={title}>
      <div className="penetration-chart-heading">
        <div>
          <h3>{title}</h3>
          <p>{description}</p>
        </div>
        <strong>{total} 条</strong>
      </div>
      <div className="penetration-chart-content">
        <div
          className="penetration-pie"
          role="img"
          aria-label={`${title}：${summary || "暂无数据"}`}
          style={{ "--pie-background": gradient } as CSSProperties}
        >
          <span aria-hidden="true">{total}</span>
        </div>
        <div className="penetration-legend" aria-label={`${title}图例`}>
          {items.map((item) => (
            <div className="penetration-legend-row" key={item.key}>
              <span className="penetration-swatch" style={{ backgroundColor: item.color }} aria-hidden="true" />
              <span className="penetration-legend-label">{item.label}</span>
              <strong>{item.count}</strong>
              <span>{item.percentage}%</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function buildGradient(items: RiskDistributionItem[]): string {
  if (!items.length) return "#e2e8f0";
  let cursor = 0;
  const segments = items.map((item, index) => {
    const start = cursor;
    cursor = index === items.length - 1 ? 100 : cursor + item.percentage;
    return `${item.color} ${start}% ${cursor}%`;
  });
  return `conic-gradient(${segments.join(", ")})`;
}
