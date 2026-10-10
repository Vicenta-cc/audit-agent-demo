export interface CrawlerProxy {
  id: string;
  display_name: string;
  platform: "dy";
  provider: "wukong" | "manual";
  endpoint: { scheme: "http"; host: string; port: number };
  has_credentials: boolean;
  config_revision: number;
  status: "active" | "maintenance" | "unavailable" | "expired" | "exit_changed";
  expires_at: number | null;
  max_bound_accounts: number;
  max_active_accounts: number;
  bound_accounts?: number;
  observed_exit_ip?: string | null;
  observed_exit_at?: number | null;
}
export interface CrawlerProxyInput {
  display_name: string;
  endpoint: CrawlerProxy["endpoint"];
  credentials?: { username: string; password: string };
  provider?: CrawlerProxy["provider"];
  expires_at?: number | null;
  max_bound_accounts: number;
  max_active_accounts: number;
}
export interface CrawlerProxyOverview { items: CrawlerProxy[]; enabled: boolean; can_manage: boolean; max_active_accounts_cap?: number }
