import { NavLink } from "react-router-dom";

export function ConfigTabs() {
  return (
    <nav className="config-tabs" aria-label="配置底座资源类型">
      <NavLink to="/config/policies" className={({ isActive }) => `config-tab${isActive ? " is-active" : ""}`}>
        研判方案
      </NavLink>
      <NavLink to="/config/lexicons" className={({ isActive }) => `config-tab${isActive ? " is-active" : ""}`}>
        黑话库
      </NavLink>
    </nav>
  );
}
