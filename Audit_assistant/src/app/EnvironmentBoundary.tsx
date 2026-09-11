import { useEffect, useState, type ReactNode } from 'react';

type Environment = { environment_id: string; worktree: string; data_dir: string; api_port: number; frontend_port: number; build_hash: string };
declare global { interface Window { M3_EXPECTED_ENVIRONMENT?: Environment } }

export function EnvironmentBoundary({ children }: { children: ReactNode }) {
  const expected = window.M3_EXPECTED_ENVIRONMENT;
  const [state, setState] = useState(expected ? 'loading' : 'ready');
  useEffect(() => {
    if (!expected) return;
    let active = true;
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 10000);
    fetch('/api/m3-runtime', { signal: controller.signal, cache: 'no-store' })
      .then(async (response) => {
        if (!response.ok) throw new Error('unavailable');
        const actual = await response.json() as Environment;
        if (Object.keys(expected).some((key) => actual[key as keyof Environment] !== expected[key as keyof Environment])) {
          throw new Error('mismatched');
        }
        const reportsResponse = await fetch('/api/historical-report-workspaces', { signal: controller.signal, cache: 'no-store' });
        if (!reportsResponse.ok) throw new Error('reports unavailable');
        const reports = await reportsResponse.json() as { items: { workspace_id: string; report_version_id: string }[] };
        if (!['historical-report-a', 'historical-report-b'].every((id) => reports.items.some((item) => item.workspace_id === id))) {
          throw new Error('A/B missing');
        }
        await Promise.all(reports.items.map(async (item) => {
          const response = await fetch(`/api/report-versions/${encodeURIComponent(item.report_version_id)}`, { signal: controller.signal });
          if (!response.ok) throw new Error('report unavailable');
        }));
        if (active) setState('ready');
      }).catch(() => { if (active) setState('error'); })
      .finally(() => window.clearTimeout(timer));
    return () => { active = false; window.clearTimeout(timer); controller.abort(); };
  }, [expected]);
  if (state !== 'ready') return <main style={{ padding: 40 }} role="status">
    <h1>{state === 'loading' ? '正在连接 M3 实验环境' : '实验环境连接失败'}</h1>
    <p>{state === 'loading' ? '正在核对后台身份与 A/B 报告。' : '后台未启动、连接了其他环境，或 A/B 报告未准备完整。请检查实验环境状态。'}</p>
    {state === 'error' && <button onClick={() => window.location.reload()}>重新连接</button>}
  </main>;
  return <>{expected && <div style={{ padding: '5px 16px', background: '#eef4ff', color: '#304d7b', fontSize: 12 }}>M3 独立实验环境 · A/B 报告 · 新任务默认单帖验证</div>}{children}</>;
}
