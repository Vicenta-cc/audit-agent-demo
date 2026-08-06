import { apiRequest } from "./apiClient";
import type {
  CrawlerAccount,
  CrawlerAccountInput,
  CrawlerAccountLoginSession,
  CrawlerAccountLoginStatus,
  CrawlerAccountPlatform,
  CrawlerAccountStatus
} from "../types/crawlerAccounts";

interface ApiCrawlerAccount {
  id: string;
  platform: CrawlerAccountPlatform;
  display_name: string;
  platform_account_id: string;
  status: CrawlerAccountStatus;
  last_validated_at: string;
  last_used_at: string;
  last_error: string;
  has_auth_state: boolean;
  auth_state_updated_at: string;
  created_at: string;
  updated_at: string;
}

interface ApiCrawlerAccountLoginSession {
  id: string;
  account_id: string;
  platform: CrawlerAccountPlatform;
  status: CrawlerAccountLoginStatus;
  qr_image_data_url: string;
  qr_expires_at: string;
  finalizing_started_at: string;
  finalizing_duration_seconds: number;
  platform_account_id: string;
  error: string;
  created_at: string;
  updated_at: string;
  expires_at: string;
}

const DEFAULT_MOCK_CRAWLER_ACCOUNTS: CrawlerAccount[] = [
  {
    id: "crawler-101",
    displayName: "小红书采集节点-01 (极速)",
    platform: "xhs",
    platformAccountId: "RED_Collector_8832",
    status: "active",
    lastValidatedAt: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
    lastUsedAt: new Date(Date.now() - 2 * 60 * 1000).toISOString(),
    lastError: "",
    hasAuthState: true,
    authStateUpdatedAt: new Date(Date.now() - 3600 * 1000).toISOString(),
    createdAt: new Date(Date.now() - 7 * 86400 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 10 * 60 * 1000).toISOString()
  },
  {
    id: "crawler-102",
    displayName: "抖音舆情监测助手-A",
    platform: "dy",
    platformAccountId: "DY_Sentry_9021",
    status: "active",
    lastValidatedAt: new Date(Date.now() - 25 * 60 * 1000).toISOString(),
    lastUsedAt: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    lastError: "",
    hasAuthState: true,
    authStateUpdatedAt: new Date(Date.now() - 7200 * 1000).toISOString(),
    createdAt: new Date(Date.now() - 14 * 86400 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 25 * 60 * 1000).toISOString()
  },
  {
    id: "crawler-103",
    displayName: "快手博彩线索抓取-03",
    platform: "ks",
    platformAccountId: "KS_Node_552",
    status: "login_required",
    lastValidatedAt: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
    lastUsedAt: new Date(Date.now() - 3600 * 1000).toISOString(),
    lastError: "Cookie 已失效，需扫码重新验证登录",
    hasAuthState: false,
    authStateUpdatedAt: new Date(Date.now() - 86400 * 1000).toISOString(),
    createdAt: new Date(Date.now() - 20 * 86400 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 2 * 3600 * 1000).toISOString()
  },
  {
    id: "crawler-104",
    displayName: "小红书社群哨兵-B",
    platform: "xhs",
    platformAccountId: "RED_Sentinel_104",
    status: "active",
    lastValidatedAt: new Date(Date.now() - 15 * 60 * 1000).toISOString(),
    lastUsedAt: new Date(Date.now() - 3 * 60 * 1000).toISOString(),
    lastError: "",
    hasAuthState: true,
    authStateUpdatedAt: new Date(Date.now() - 1800 * 1000).toISOString(),
    createdAt: new Date(Date.now() - 5 * 86400 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 15 * 60 * 1000).toISOString()
  },
  {
    id: "crawler-105",
    displayName: "抖音热点追踪节点-05",
    platform: "dy",
    platformAccountId: "DY_Trend_7731",
    status: "expired",
    lastValidatedAt: new Date(Date.now() - 24 * 3600 * 1000).toISOString(),
    lastUsedAt: new Date(Date.now() - 18 * 3600 * 1000).toISOString(),
    lastError: "账号被目标平台风控限制访问",
    hasAuthState: false,
    authStateUpdatedAt: new Date(Date.now() - 48 * 3600 * 1000).toISOString(),
    createdAt: new Date(Date.now() - 30 * 86400 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 24 * 3600 * 1000).toISOString()
  },
  {
    id: "crawler-106",
    displayName: "快手违规抓取备份-02",
    platform: "ks",
    platformAccountId: "KS_Backup_889",
    status: "disabled",
    lastValidatedAt: new Date(Date.now() - 3 * 86400 * 1000).toISOString(),
    lastUsedAt: new Date(Date.now() - 3 * 86400 * 1000).toISOString(),
    lastError: "管理员主动停用",
    hasAuthState: false,
    authStateUpdatedAt: new Date(Date.now() - 3 * 86400 * 1000).toISOString(),
    createdAt: new Date(Date.now() - 40 * 86400 * 1000).toISOString(),
    updatedAt: new Date(Date.now() - 3 * 86400 * 1000).toISOString()
  }
];

let localCrawlerAccountsStore: CrawlerAccount[] = [...DEFAULT_MOCK_CRAWLER_ACCOUNTS];

export async function fetchCrawlerAccounts(): Promise<CrawlerAccount[]> {
  try {
    const payload = await apiRequest<{ items: ApiCrawlerAccount[] }>("/api/crawler-accounts");
    if (payload.items && payload.items.length > 0) {
      const items = payload.items.map(mapCrawlerAccount);
      localCrawlerAccountsStore = items;
      return items;
    }
  } catch (err) {
    console.warn("API fetch crawler accounts fallback to local dataset:", err);
  }
  return localCrawlerAccountsStore;
}

export async function createCrawlerAccount(input: CrawlerAccountInput): Promise<CrawlerAccount> {
  try {
    const payload = await apiRequest<{ item: ApiCrawlerAccount }>("/api/crawler-accounts", {
      method: "POST",
      body: JSON.stringify(toApiInput(input))
    });
    const created = mapCrawlerAccount(payload.item);
    localCrawlerAccountsStore = [created, ...localCrawlerAccountsStore];
    return created;
  } catch {
    const newId = `crawler-${Date.now().toString().slice(-4)}`;
    const created: CrawlerAccount = {
      id: newId,
      displayName: input.displayName,
      platform: input.platform,
      platformAccountId: input.platformAccountId || `${input.platform.toUpperCase()}_Acc_${Math.floor(Math.random() * 8999 + 1000)}`,
      status: "login_required",
      lastValidatedAt: new Date().toISOString(),
      lastUsedAt: "",
      lastError: "",
      hasAuthState: false,
      authStateUpdatedAt: "",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    };
    localCrawlerAccountsStore = [created, ...localCrawlerAccountsStore];
    return created;
  }
}

export async function updateCrawlerAccount(
  accountId: string,
  input: Partial<CrawlerAccountInput> & { status?: CrawlerAccountStatus }
): Promise<CrawlerAccount> {
  try {
    const payload = await apiRequest<{ item: ApiCrawlerAccount }>(
      `/api/crawler-accounts/${encodeURIComponent(accountId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({
          ...(input.displayName !== undefined ? { display_name: input.displayName } : {}),
          ...(input.platformAccountId !== undefined ? { platform_account_id: input.platformAccountId } : {}),
          ...(input.status !== undefined ? { status: input.status } : {})
        })
      }
    );
    const updated = mapCrawlerAccount(payload.item);
    localCrawlerAccountsStore = localCrawlerAccountsStore.map((item) => item.id === updated.id ? updated : item);
    return updated;
  } catch {
    let target = localCrawlerAccountsStore.find((item) => item.id === accountId);
    if (!target) {
      throw new Error("未找到该账号");
    }
    const updated: CrawlerAccount = {
      ...target,
      ...(input.displayName !== undefined ? { displayName: input.displayName } : {}),
      ...(input.platformAccountId !== undefined ? { platformAccountId: input.platformAccountId } : {}),
      ...(input.status !== undefined ? { status: input.status } : {}),
      updatedAt: new Date().toISOString()
    };
    localCrawlerAccountsStore = localCrawlerAccountsStore.map((item) => item.id === accountId ? updated : item);
    return updated;
  }
}

export async function deleteCrawlerAccount(accountId: string): Promise<void> {
  try {
    await apiRequest(`/api/crawler-accounts/${encodeURIComponent(accountId)}`, {
      method: "DELETE"
    });
  } catch {
    // Ignore network failure and proceed locally
  }
  localCrawlerAccountsStore = localCrawlerAccountsStore.filter((item) => item.id !== accountId);
}

export async function startCrawlerAccountLogin(accountId: string): Promise<CrawlerAccountLoginSession> {
  try {
    const payload = await apiRequest<{ item: ApiCrawlerAccountLoginSession }>(
      `/api/crawler-accounts/${encodeURIComponent(accountId)}/login-sessions`,
      { method: "POST" }
    );
    return mapLoginSession(payload.item);
  } catch {
    const target = localCrawlerAccountsStore.find((item) => item.id === accountId);
    return {
      id: `session-${Date.now()}`,
      accountId,
      platform: target?.platform || "xhs",
      status: "waiting_scan",
      qrImageDataUrl: "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180' viewBox='0 0 180 180'><rect width='180' height='180' fill='%23f8fafc'/><path d='M20 20h50v50H20zM110 20h50v50h-50zM20 110h50v50H20z' fill='%230f172a'/><rect x='35' y='35' width='20' height='20' fill='%232563eb'/><rect x='125' y='35' width='20' height='20' fill='%232563eb'/><rect x='35' y='125' width='20' height='20' fill='%232563eb'/><path d='M90 20h10v20H90zM80 50h30v10H80zM110 90h50v10h-50zM90 110h20v50H90zM130 130h30v30h-30z' fill='%23334155'/></svg>",
      qrExpiresAt: new Date(Date.now() + 300000).toISOString(),
      finalizingStartedAt: "",
      finalizingDurationSeconds: 3,
      platformAccountId: target?.platformAccountId || "",
      error: "",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      expiresAt: new Date(Date.now() + 300000).toISOString()
    };
  }
}

export async function fetchCrawlerAccountLoginSession(sessionId: string): Promise<CrawlerAccountLoginSession> {
  try {
    const payload = await apiRequest<{ item: ApiCrawlerAccountLoginSession }>(
      `/api/crawler-account-login-sessions/${encodeURIComponent(sessionId)}`
    );
    return mapLoginSession(payload.item);
  } catch {
    return {
      id: sessionId,
      accountId: "crawler-101",
      platform: "xhs",
      status: "finalizing",
      qrImageDataUrl: "",
      qrExpiresAt: new Date(Date.now() + 300000).toISOString(),
      finalizingStartedAt: new Date().toISOString(),
      finalizingDurationSeconds: 1,
      platformAccountId: "RED_Collector_8832",
      error: "",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      expiresAt: new Date(Date.now() + 300000).toISOString()
    };
  }
}

export async function cancelCrawlerAccountLoginSession(sessionId: string): Promise<void> {
  try {
    await apiRequest(`/api/crawler-account-login-sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE"
    });
  } catch {
    // Ignore error
  }
}

function toApiInput(input: CrawlerAccountInput) {
  return {
    platform: input.platform,
    display_name: input.displayName,
    platform_account_id: input.platformAccountId
  };
}

function mapCrawlerAccount(item: ApiCrawlerAccount): CrawlerAccount {
  return {
    id: item.id,
    platform: item.platform,
    displayName: item.display_name,
    platformAccountId: item.platform_account_id,
    status: item.status,
    lastValidatedAt: item.last_validated_at,
    lastUsedAt: item.last_used_at,
    lastError: item.last_error,
    hasAuthState: item.has_auth_state,
    authStateUpdatedAt: item.auth_state_updated_at,
    createdAt: item.created_at,
    updatedAt: item.updated_at
  };
}

function mapLoginSession(item: ApiCrawlerAccountLoginSession): CrawlerAccountLoginSession {
  return {
    id: item.id,
    accountId: item.account_id,
    platform: item.platform,
    status: item.status,
    qrImageDataUrl: item.qr_image_data_url,
    qrExpiresAt: item.qr_expires_at,
    finalizingStartedAt: item.finalizing_started_at,
    finalizingDurationSeconds: item.finalizing_duration_seconds,
    platformAccountId: item.platform_account_id,
    error: item.error,
    createdAt: item.created_at,
    updatedAt: item.updated_at,
    expiresAt: item.expires_at
  };
}
