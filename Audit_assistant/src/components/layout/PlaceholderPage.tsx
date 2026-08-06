import { Construction } from "lucide-react";

interface PlaceholderPageProps {
  title: string;
}

export function PlaceholderPage({ title }: PlaceholderPageProps) {
  return (
    <main className="placeholder-shell">
      <section className="placeholder-panel">
        <div className="placeholder-icon" aria-hidden="true">
          <Construction size={28} />
        </div>
        <div>
          <h1>{title}</h1>
          <p>当前路由已接入，完整工作流将在下一阶段补齐。</p>
        </div>
      </section>
    </main>
  );
}
