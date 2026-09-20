import { Bell, ShieldCheck } from "lucide-react";
import { NavLink, useLocation } from "react-router-dom";
import { AccountMenu } from "../../features/auth/AuthBoundary";
import { navigationItems } from "../../app/router";

export function TopNavigation() {
  const location = useLocation();

  // Hide top navigation in full-screen Investigation Workspace and embedded sub-pages
  if (
    location.pathname.startsWith("/investigation") ||
    location.pathname.startsWith("/admin/users") ||
    location.pathname.startsWith("/knowledge-center") ||
    location.pathname.startsWith("/users") ||
    location.pathname.startsWith("/crawler-accounts") ||
    location.pathname.startsWith("/rule-assistant") ||
    location.pathname.startsWith("/tasks/chat")
  ) {
    return null;
  }

  return (
    <header className="top-navigation">
      <div className="brand-block">
        <div className="brand-mark" aria-hidden="true">
          <ShieldCheck size={25} strokeWidth={2.2} />
        </div>
        <div>
          <div className="brand-title">内容巡查研判平台</div>
          <div className="brand-subtitle">风险内容 · 证据固化 · 用户归并</div>
        </div>
      </div>

      <nav className="primary-nav" aria-label="主导航">
        {navigationItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) => `primary-nav-link${isActive ? " is-active" : ""}`}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="top-actions">
        <button className="icon-button" type="button" aria-label="通知">
          <Bell size={20} />
        </button>
        <AccountMenu />
      </div>
    </header>
  );
}
