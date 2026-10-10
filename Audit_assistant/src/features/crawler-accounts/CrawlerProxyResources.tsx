import { FormEvent, useState } from "react";
import { Button } from "../../components/common/Button";
import {
  createCrawlerProxy,
  deleteCrawlerProxy,
  grantCrawlerProxyUse,
  probeCrawlerProxy,
  recoverCrawlerProxy,
  revokeCrawlerProxyUse,
  updateCrawlerProxy
} from "../../services/crawlerProxies";
import type { CrawlerProxy, CrawlerProxyOverview } from "../../types/crawlerProxies";

export function CrawlerProxyResources({ overview, onRefresh }: { overview: CrawlerProxyOverview; onRefresh: () => Promise<void> }) {
  const [probeAccountIds, setProbeAccountIds] = useState<Record<string, string>>({});
  const [probeNotice, setProbeNotice] = useState("");
  const [grantUserIds, setGrantUserIds] = useState<Record<string, string>>({});
  const [grantNotice, setGrantNotice] = useState("");
  const [editing, setEditing] = useState<CrawlerProxy | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("8080");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [clearCredentials, setClearCredentials] = useState(false);
  const [expiresAt, setExpiresAt] = useState("");
  const [bound, setBound] = useState(1);
  const [active, setActive] = useState(1);
  const activeCap = overview.max_active_accounts_cap || 1;
  const edit = (resource: CrawlerProxy | null) => {
    setEditing(resource);
    setName(resource?.display_name || "");
    setHost(resource?.endpoint.host || "");
    setPort(String(resource?.endpoint.port || 8080));
    setUsername("");
    setPassword("");
    setClearCredentials(false);
    setExpiresAt(resource?.expires_at ? new Date(resource.expires_at * 1000).toISOString() : "");
    setBound(resource?.max_bound_accounts || 1);
    setActive(resource?.max_active_accounts || 1);
    setError("");
    setOpen(true);
  };
  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      await onRefresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "代理操作失败");
    } finally {
      setBusy(false);
    }
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (active > activeCap) { setError(`最大活跃账号数不能超过平台并发上限 ${activeCap}`); return; }
    if (expiresAt.trim() && !Number.isFinite(Date.parse(expiresAt))) {
      setError("请输入有效的 ISO 时间，或留空标记未知");
      return;
    }
    await act(async () => {
      const input = {
        display_name: name.trim(),
        endpoint: { scheme: "http" as const, host: host.trim(), port: Number(port) },
        expires_at: expiresAt.trim() ? Date.parse(expiresAt) / 1000 : null,
        max_bound_accounts: bound,
        max_active_accounts: active,
        ...(username || password ? { credentials: { username, password } } : {})
      };
      if (editing) {
        await updateCrawlerProxy(editing.id, {
          ...input,
          expected_revision: editing.config_revision,
          clear_credentials: clearCredentials
        });
      } else {
        await createCrawlerProxy(input);
      }
      setPassword("");
      setUsername("");
      setOpen(false);
    });
  };
  return <section className="crawler-account-panel" aria-label="采集代理资源">
    <div className="crawler-account-panel-heading"><div><h2>抖音采集代理</h2>
      <p>{overview.enabled ? "先绑定代理再登录。代理故障时停止网络操作。" : "代理功能未启用；已要求代理的账号不会回退直连。"}</p>
      <p>入口不等于真实出口，住宅出口可能变化；出口样本不能证明永久固定。</p></div>
      {overview.can_manage ? <Button onClick={() => edit(null)}>添加代理</Button> : null}
    </div>
    {error ? <p role="alert" className="crawler-account-form-error">{error}</p> : null}
    {grantNotice ? <p role="status">{grantNotice}</p> : null}
    {probeNotice ? <p role="status">{probeNotice}</p> : null}
    {overview.items.length === 0 ? <p>暂无可用的已授权代理资源。</p> : overview.items.map((resource) => <div key={resource.id} className="crawler-account-status-preview">
      <div><strong>{resource.display_name}</strong><p>入口 HTTP {resource.endpoint.host}:{resource.endpoint.port}</p>
        <p>最近出口：{resource.observed_exit_ip || "尚未观测"} · {resource.observed_exit_at ? new Date(resource.observed_exit_at * 1000).toLocaleString() : "时间未知"}</p>
        <p>健康状态：{statusLabels[resource.status]} · 有效期：{resource.expires_at ? new Date(resource.expires_at * 1000).toLocaleString() : "未知"}</p>
        <p>绑定容量：{resource.bound_accounts ?? "未知"}/{resource.max_bound_accounts} · 活跃账号上限：{resource.max_active_accounts} · 凭据：{resource.has_credentials ? "已配置（不可读回）" : "未配置"}</p></div>
      {overview.can_manage ? <div>
        <Button disabled={busy} onClick={() => edit(resource)}>编辑代理</Button>
        {["maintenance", "unavailable", "exit_changed"].includes(resource.status) ?
          <Button disabled={busy} onClick={() => void act(() => recoverCrawlerProxy(resource.id, resource.config_revision))}>确认维护完成，运行前重新校验</Button> :
          <Button disabled={busy} onClick={() => void act(() => updateCrawlerProxy(resource.id, { expected_revision: resource.config_revision, status: "maintenance" }))}>进入维护</Button>}
        <p>恢复只清除该资源停止标记，不清除平台限速冷却；新运行仍需重新校验出口。</p>
        <label className="crawler-account-field">出口检查账号 ID
          <input aria-label={`出口检查账号 ID：${resource.display_name}`} value={probeAccountIds[resource.id] || ""} autoComplete="off"
            placeholder="已绑定且你有使用权的抖音账号 ID"
            onChange={(event) => setProbeAccountIds((current) => ({ ...current, [resource.id]: event.target.value }))} />
        </label>
        <Button disabled={busy || !(probeAccountIds[resource.id] || "").trim()} onClick={() => void act(async () => {
          setProbeNotice("");
          await probeCrawlerProxy(resource.id, probeAccountIds[resource.id].trim(), resource.config_revision);
          setProbeNotice("出口检查完成，仅记录观测样本；不会自动恢复采集。");
        })}>检查出口</Button>
        <p>检查使用该账号原浏览器身份，仅访问出口回显服务，不访问平台业务；管理员不能使用其他人的私有账号。</p>

        <Button disabled={busy} onClick={() => {
          if (window.confirm("删除代理资源？已有绑定的资源不能删除。")) {
            void act(() => deleteCrawlerProxy(resource.id, resource.config_revision));
          }
        }}>删除代理</Button>
        <label className="crawler-account-field">授权用户 ID
          <input aria-label={`授权用户 ID：${resource.display_name}`} value={grantUserIds[resource.id] || ""} autoComplete="off"
            onChange={(event) => setGrantUserIds((current) => ({ ...current, [resource.id]: event.target.value }))} />
        </label>
        <Button disabled={busy || !(grantUserIds[resource.id] || "").trim()} onClick={() => void act(async () => {
          setGrantNotice("");
          await grantCrawlerProxyUse(resource.id, grantUserIds[resource.id].trim());
          setGrantNotice(`已授予 ${resource.display_name} 的使用权`);
        })}>授予代理使用权</Button>
        <Button disabled={busy || !(grantUserIds[resource.id] || "").trim()} onClick={() => void act(async () => {
          setGrantNotice("");
          await revokeCrawlerProxyUse(resource.id, grantUserIds[resource.id].trim());
          setGrantNotice(`已撤销 ${resource.display_name} 的使用权`);
        })}>撤销代理使用权</Button>
        <p>仅授予该代理使用权；不会授予任何私有采集账号或代理管理权限。</p>

      </div> : null}
    </div>)}
    {open ? <div className="crawler-account-drawer-backdrop"><aside className="crawler-account-drawer" role="dialog" aria-modal="true" aria-label="代理资源编辑">
      <header className="crawler-account-drawer-header"><h2>{editing ? "编辑代理资源" : "添加代理资源"}</h2></header>
      <form className="crawler-account-form" onSubmit={submit}><div className="crawler-account-form-body">
        <label className="crawler-account-field">代理名称<input required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="crawler-account-field">HTTP 入口服务器<input required value={host} placeholder="host 或 IP，不含协议和凭据" onChange={(e) => setHost(e.target.value)} /></label>
        <label className="crawler-account-field">端口<input required type="number" min={1} max={65535} value={port} onChange={(e) => setPort(e.target.value)} /></label>
        <label className="crawler-account-field">代理用户名<input autoComplete="off" value={username} placeholder={editing ? "不填写则保持原凭据" : "可选"} onChange={(e) => setUsername(e.target.value)} /></label>
        <label className="crawler-account-field">代理密码<input type="password" autoComplete="new-password" value={password} placeholder={editing ? "只写入，不读回" : "可选"} onChange={(e) => setPassword(e.target.value)} /></label>
        {editing ? <label><input type="checkbox" checked={clearCredentials} onChange={(e) => setClearCredentials(e.target.checked)} />明确清除已有凭据</label> : null}
        <label className="crawler-account-field">订单有效期<input value={expiresAt} placeholder="ISO 时间；留空表示未知" onChange={(e) => setExpiresAt(e.target.value)} /></label>
        <label className="crawler-account-field">最大绑定账号数<input type="number" min={1} required value={bound} onChange={(e) => setBound(Number(e.target.value))} /></label>
        <label className="crawler-account-field">最大活跃账号数<input type="number" min={1} max={activeCap} required value={active} onChange={(e) => setActive(Number(e.target.value))} /></label>
        <p>绑定多个账号不代表平台允许共享。更改配置前应进入维护，等待登录和采集结束。</p>
        {error ? <p role="alert">{error}</p> : null}
      </div><footer className="crawler-account-drawer-footer"><Button disabled={busy} onClick={() => {
        setPassword("");
        setUsername("");
        setOpen(false);
      }}>取消</Button><Button type="submit" variant="primary" loading={busy}>保存代理</Button></footer></form>
    </aside></div> : null}
  </section>;
}
const statusLabels: Record<CrawlerProxy["status"], string> = { active: "已启用，运行前校验出口", maintenance: "维护中", unavailable: "连通异常", expired: "已到期", exit_changed: "出口变化待检查" };
