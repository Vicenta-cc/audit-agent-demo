import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Button } from "../../components/common/Button";
import { ApiError, apiRequest, clearAuthenticationState } from "../../services/apiClient";
import { bootstrapAuthentication, login, logout, type ApplicationUser } from "../../services/auth";
import "./auth.css";

type AuthValue = { user: ApplicationUser | null; signOut: () => Promise<void> };
const AuthContext = createContext<AuthValue>({ user: null, signOut: async () => {} });
export const useApplicationAuth = () => useContext(AuthContext);
const CHANGE_KEY = "xhs-audit:auth-change";
export function accountDate(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "首次登录起七天";
}

export function AuthBoundary({ children }: { children: ReactNode }) {
  const routeLocation = useLocation();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"loading" | "disabled" | "required" | "error">("loading");
  const [user, setUser] = useState<ApplicationUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const generation = useRef(0);
  const rechecking = useRef(false);
  const authenticationRequest = useRef<AbortController | null>(null);
  const channel = useRef<BroadcastChannel | null>(null);
  const lastAnnouncement = useRef("");
  const announce = () => {
    const marker = crypto.randomUUID();
    lastAnnouncement.current = marker;
    try { localStorage.setItem(CHANGE_KEY, marker); } catch { /* Broadcast and server checks remain available. */ }
    channel.current?.postMessage(marker);
  };
  const invalidate = (message: string) => {
    generation.current += 1;
    authenticationRequest.current?.abort();
    clearAuthenticationState(); setUser(null); setPassword(""); setChecking(false); setNotice(message);
  };
  useEffect(() => {
    let mounted = true;
    const controller = new AbortController();
    authenticationRequest.current = controller;
    const version = generation.current;
    void apiRequest<{enabled: boolean}>("/api/auth/config", { signal: controller.signal }).then(async config => {
      if (typeof config.enabled !== "boolean") throw new Error("invalid auth configuration");
      if (!mounted) return;
      if (!config.enabled) { setMode("disabled"); setChecking(false); return; }
      setMode("required");
      try {
        const current = await bootstrapAuthentication(controller.signal);
        if (mounted && version === generation.current) setUser(current);
      } catch (error) {
        if (mounted) setNotice(error instanceof ApiError && error.code === "ACCOUNT_EXPIRED" ? "账号已到期，请联系管理员续期后重新登录。" : error instanceof ApiError && error.status === 401 ? "请登录后使用。" : "登录状态核对失败，请重试登录。");
      } finally { if (mounted) setChecking(false); }
    }).catch(() => { if (mounted) { setMode("error"); setChecking(false); } });
    return () => { mounted = false; controller.abort(); };
  }, []);
  useEffect(() => {
    if (mode !== "required") return;
    const expired = (event: Event) => invalidate((event as CustomEvent).detail === "ACCOUNT_EXPIRED" ? "账号已到期，请联系管理员续期后重新登录。" : "登录已失效或身份发生变化，请重新登录。");
    const receiveChange = (marker: unknown) => {
      if (typeof marker !== "string" || marker === lastAnnouncement.current) return;
      lastAnnouncement.current = marker;
      invalidate("其他标签页已切换登录状态，请重新登录确认身份。");
    };
    const changed = (event: StorageEvent) => { if (event.key === CHANGE_KEY) receiveChange(event.newValue); };
    if (typeof BroadcastChannel !== "undefined") {
      channel.current = new BroadcastChannel(CHANGE_KEY);
      channel.current.onmessage = event => receiveChange(event.data);
    }
    window.addEventListener("application-auth-expired", expired);
    window.addEventListener("storage", changed);
    return () => { channel.current?.close(); channel.current = null; window.removeEventListener("application-auth-expired", expired); window.removeEventListener("storage", changed); };
  }, [mode]);
  useEffect(() => {
    if (!user) return;
    const expiresAt = user.role === "admin" ? null : user.expires_at;
    const remaining = expiresAt ? Date.parse(expiresAt) - Date.now() : 0;
    const timer = expiresAt ? window.setTimeout(() => {
      if (Date.now() >= Date.parse(expiresAt)) invalidate("账号已到期，请联系管理员续期后重新登录。");
    }, Math.max(0, Math.min(remaining, 2147483647))) : undefined;
    const refresh = async () => {
      if (rechecking.current) return;
      rechecking.current = true;
      const version = generation.current;
      try {
        const result = await apiRequest<{user: ApplicationUser}>("/api/auth/me");
        if (version !== generation.current) return;
        if (result.user.id !== user.id || result.user.role !== user.role) invalidate("登录身份已变化，请重新登录。");
        else if (result.user.expires_at !== user.expires_at) setUser(result.user);
      } catch (error) {
        if (version === generation.current && error instanceof ApiError && (error.status === 401 || error.code === "ACCOUNT_EXPIRED")) invalidate(error.code === "ACCOUNT_EXPIRED" ? "账号已到期，请联系管理员续期后重新登录。" : "登录已失效，请重新登录。");
      } finally { rechecking.current = false; }
    };
    const visible = () => { if (document.visibilityState === "visible") void refresh(); };
    document.addEventListener("visibilitychange", visible);
    const poll = window.setInterval(refresh, 30000);
    window.addEventListener("focus", refresh);
    return () => { clearTimeout(timer); clearInterval(poll); window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", visible); };
  }, [user]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); if (busy) return;
    setBusy(true); setNotice(""); const version = ++generation.current;
    try {
      const controller = new AbortController();
      authenticationRequest.current = controller;
      const result = await login(username.trim(), password, controller.signal);
      if (version !== generation.current) { clearAuthenticationState(); return; }
      announce(); setUser(result); setPassword("");
      if (result.role !== "admin" && routeLocation.pathname.startsWith("/admin/")) navigate("/investigation", { replace: true });
    } catch (error) {
      if (version === generation.current) setNotice(error instanceof ApiError && error.code === "ACCOUNT_EXPIRED" ? "账号已到期，请联系管理员续期后重新登录。" : error instanceof ApiError && error.status === 401 ? "账号或密码不正确，或账号已被禁用。" : "登录失败，请检查网络后重试。");
    } finally { setBusy(false); }
  };
  const signOut = async () => {
    if (busy) return;
    setBusy(true);
    // Stop rendering user-owned components while the logout request is pending.
    setChecking(true);
    try { await logout(); invalidate("已退出登录。"); announce(); }
    catch { invalidate("退出请求未确认，请检查网络后重新登录。"); announce(); }
    finally {
      navigate("/investigation", { replace: true });
      setBusy(false);
    }
  };
  if (mode === "disabled") return <>{children}</>;
  if (mode === "error") return <main className="auth-screen"><section className="auth-card"><h1>暂时无法连接工作台</h1><p>无法核对登录配置，请检查服务或网络后重试。</p><Button onClick={() => location.reload()}>重新连接</Button></section></main>;
  if (checking) return <main className="auth-screen" role="status">正在核对登录状态…</main>;
  if (!user) return <main className="auth-screen"><form className="auth-card" onSubmit={submit}>
    <h1>登录调查工作区</h1><p>使用管理员为你开通的应用账号登录。</p>
    <label>账号<input autoComplete="username" required minLength={3} maxLength={64} value={username} onChange={e => setUsername(e.target.value)} /></label>
    <label>密码<input type="password" autoComplete="current-password" required maxLength={256} value={password} onChange={e => setPassword(e.target.value)} /></label>
    {notice ? <p role="alert">{notice}</p> : null}
    <Button type="submit" variant="primary" loading={busy}>登录</Button>
    <small>账号默认首次登录起七天有效；到期请联系管理员。不开放自行注册。</small>
  </form></main>;
  return <AuthContext.Provider value={{ user, signOut }}><div key={`${user.id}:${user.role}`}>{children}</div></AuthContext.Provider>;
}

export function AccountMenu({ compact = false }: { compact?: boolean }) {
  const { user, signOut } = useApplicationAuth();
  if (!user) return null;
  const content = <div className="application-account" aria-label="应用登录账号">
    <strong>{user.username}</strong><small>{user.role === "admin" ? "管理员 · 长期有效" : `有效至 ${accountDate(user.expires_at)}（北京时间）`}</small>
    <div>{user.role === "admin" ? <Link to="/admin/users">应用账号管理</Link> : null}<button type="button" onClick={() => void signOut()}>退出登录</button></div>
  </div>;
  return compact ? <details className="inv-toolbar-menu"><summary>{user.username}<span aria-hidden="true">⌄</span></summary><div className="inv-toolbar-popover">{content}</div></details> : content;
}
