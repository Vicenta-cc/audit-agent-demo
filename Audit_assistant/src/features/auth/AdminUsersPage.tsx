import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, ShieldCheck, UserPlus, Users, RefreshCw, KeyRound, X } from "lucide-react";
import { Button } from "../../components/common/Button";
import { ConfirmDialog } from "../../components/feedback/ConfirmDialog";
import { apiRequest } from "../../services/apiClient";
import type { ApplicationUser } from "../../services/auth";
import { accountDate, AccountMenu, useApplicationAuth } from "./AuthBoundary";

type Change = { title: string; description: string; run: () => Promise<unknown> };
const userPath = (id: string) => `/api/admin/users/${encodeURIComponent(id)}`;

function displayStatus(target: ApplicationUser) {
  if (target.status === "disabled") return { kind: "disabled", label: "已禁用" };
  if (target.role === "admin") return { kind: "active", label: "有效" };
  if (!target.expires_at) return { kind: "pending", label: "待首次登录" };
  if (Date.parse(target.expires_at) <= Date.now()) return { kind: "expired", label: "已到期" };
  return { kind: "active", label: "有效" };
}

export function AdminUsersPage() {
  const { user } = useApplicationAuth();
  const [users, setUsers] = useState<ApplicationUser[]>([]);
  const [selected, setSelected] = useState<ApplicationUser | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [resetPassword, setResetPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [change, setChange] = useState<Change | null>(null);
  const passwordPanel = useRef<HTMLElement>(null);
  const passwordTrigger = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (selected) {
      passwordPanel.current?.scrollIntoView({ block: "start", behavior: "instant" });
      passwordPanel.current?.focus({ preventScroll: true });
    }
  }, [selected?.id]);
  const mutating = useRef(false);
  const refresh = async () => {
    const rows = await apiRequest<{items: ApplicationUser[]}>("/api/admin/users");
    setUsers(rows.items);
  };
  useEffect(() => {
    if (user?.role !== "admin") return;
    void refresh().catch(() => setError("无法加载账号，请检查网络或管理员权限后重试。"));
  }, [user?.id, user?.role]);
  const openPasswordSettings = (target: ApplicationUser, trigger: HTMLElement) => {
    // WebKit does not focus a button on pointer click. Preserve the actual
    // trigger so closing the panel reliably restores keyboard navigation.
    passwordTrigger.current = trigger;
    setSelected(target); setResetPassword(""); setError(""); setNotice("");
  };
  const execute = async (action: () => Promise<unknown>, success: string) => {
    if (mutating.current) return;
    mutating.current = true;
    setBusy(true); setError(""); setNotice("");
    try { await action(); setNotice(success); await refresh(); }
    catch (error) { setError(error instanceof Error ? error.message : "操作失败，请重试。"); }
    finally { mutating.current = false; setBusy(false); }
  };
  if (user?.role !== "admin") return <main className="account-admin"><h1>此页面仅限管理员</h1><Link to="/investigation">返回调查工作区</Link></main>;
  return <main className="account-admin">
    <Link className="admin-back-link" to="/investigation"><ArrowLeft size={14} />返回调查工作区</Link>
    <header className="admin-page-header"><div className="admin-page-heading"><span className="admin-page-icon"><ShieldCheck size={24} /></span><div><span className="admin-eyebrow">管理控制台</span><h1>应用账号管理</h1><p>管理应用账号的开通、有效期与登录密码。</p></div></div><div className="admin-current-account"><AccountMenu /></div></header>
    {!selected && error ? <p role="alert">{error}</p> : null}{!selected && notice ? <p role="status">{notice}</p> : null}
    <section className="admin-create-card"><div className="admin-section-heading"><div className="admin-section-title"><UserPlus size={18} /><h2>开通账号</h2></div><span className="admin-policy-tag">首次登录起 7 天有效</span></div><p className="admin-description">为新用户创建独立账号，首次成功登录后开始计算有效期。</p>
      <form onSubmit={event => { event.preventDefault(); void execute(async () => {
        await apiRequest("/api/admin/users", { method: "POST", body: JSON.stringify({ username: username.trim(), password, role: "user", validity_days: 7, activation_mode: "first_login" }) });
        setUsername(""); setPassword("");
      }, "账号已开通。"); }}>
        <label>新账号<input placeholder="输入账号名称（至少 3 位）" required minLength={3} maxLength={64} autoComplete="off" value={username} onChange={e => setUsername(e.target.value)} /></label>
        <label>初始密码<input placeholder="设置至少 12 位密码" required type="password" minLength={12} maxLength={256} autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} /></label>
        <Button type="submit" variant="primary" loading={busy}>开通七天账号</Button>
      </form>
      <p className="admin-form-hint">初始密码请通过安全渠道交给用户。</p>
    </section>
    <section className="admin-users-card"><div className="admin-section-heading"><div className="admin-section-title"><Users size={18} /><h2>用户列表</h2><span className="admin-count">{users.length}</span></div><Button size="small" variant="ghost" disabled={busy} onClick={() => void execute(async () => {}, "已刷新账号列表。") }><RefreshCw size={14} />刷新列表</Button></div>
      <div className="account-table"><table><thead><tr><th>用户</th><th>状态</th><th>有效期（北京时间）</th><th>操作</th></tr></thead><tbody>
        {users.map(target => <tr key={target.id} className={selected?.id === target.id ? "is-selected" : undefined}><td><div className="admin-user-identity"><span className={`admin-avatar ${target.role === "admin" ? "is-admin" : ""}`} aria-hidden="true">{target.role === "admin" ? <ShieldCheck size={17} /> : target.username.slice(0, 1)}</span><div><div className="admin-user-name">{target.username}{target.role === "admin" ? <span className="admin-role-tag">管理员</span> : null}</div><small className="account-id">{target.id}</small></div></div></td>
          <td><span className={`admin-status is-${displayStatus(target).kind}`}>{displayStatus(target).label}</span></td><td className="admin-expiry">{target.role === "admin" ? "长期有效" : accountDate(target.expires_at)}</td>
          <td><div className="account-actions">
            {target.role !== "admin" ? <Button size="small" variant="ghost" disabled={busy || !target.expires_at} title={!target.expires_at ? "首次登录后可续期" : undefined} onClick={() => setChange({ title: `为 ${target.username} 续期七天？`, description: "未过期账号从原到期时间延长；已过期账号从现在起七天。禁用状态不会因此解除，已失效的登录需要重新登录。", run: () => apiRequest(userPath(target.id), { method: "PATCH", body: JSON.stringify({ renew_days: 7 }) }) })}>续期七天</Button> : null}
            <Button size="small" variant="ghost" disabled={busy || target.id === user.id} onClick={() => setChange({ title: `${target.status === "active" ? "禁用" : "启用"} ${target.username}？`, description: target.status === "active" ? "禁用后现有登录失效，该用户不能再登录或继续发起操作。" : "启用不会延长有效期；已过期账号仍需续期。", run: () => apiRequest(userPath(target.id), { method: "PATCH", body: JSON.stringify({ status: target.status === "active" ? "disabled" : "active" }) }) })}>{target.status === "active" ? "禁用" : "启用"}</Button>
            <Button size="small" className="admin-manage-button" disabled={busy} onClick={event => openPasswordSettings(target, event.currentTarget)}>密码设置</Button>
          </div></td></tr>)}
      </tbody></table>{!users.length ? <p>暂无可显示的账号。</p> : null}</div>
    </section>
    {selected ? <section ref={passwordPanel} tabIndex={-1} className="admin-password-card" aria-label="用户密码设置"><div className="admin-section-heading"><div className="admin-section-title"><KeyRound size={18} /><h2>{selected.username} · 密码设置</h2></div><Button size="small" variant="ghost" aria-label="关闭用户密码设置" onClick={() => { setSelected(null); setResetPassword(""); passwordTrigger.current?.focus(); }}><X size={16} /></Button></div>
      {error ? <p role="alert">{error}</p> : null}{notice ? <p role="status">{notice}</p> : null}
      <p className="admin-description">设置新的应用登录密码后，该账号的现有登录将失效，需要重新登录。</p>
      <form onSubmit={event => { event.preventDefault(); setChange({ title: `重置 ${selected.username} 的密码？`, description: "该账号的现有登录将失效，用户需要使用新密码重新登录。", run: async () => { await apiRequest(userPath(selected.id), { method: "PATCH", body: JSON.stringify({ password: resetPassword }) }); setResetPassword(""); } }); }}>
          <label>新密码<input type="password" required minLength={12} maxLength={256} autoComplete="new-password" value={resetPassword} onChange={e => setResetPassword(e.target.value)} /></label><Button type="submit" disabled={busy}>重置密码</Button>
        </form>
    </section> : null}
    <ConfirmDialog open={Boolean(change)} title={change?.title || ""} description={change?.description || ""} confirmText="确认" onCancel={() => setChange(null)} onConfirm={() => { const pending = change; setChange(null); if (pending) void execute(pending.run, "操作已保存。"); }} />
  </main>;
}
