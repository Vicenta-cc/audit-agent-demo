import { apiRequest } from "./apiClient";
import type {
  CrawlerAccount,
  CrawlerAccountAccessScope,
  CrawlerAccountInput,
  CrawlerAccountLoginSession,
  CrawlerAccountLoginStatus,
  CrawlerAccountPlatform,
  CrawlerAccountStatus,
  SharedCrawlerPoolSummary
} from "../types/crawlerAccounts";

interface ApiCrawlerAccount {
  id: string;
  platform: CrawlerAccountPlatform;
  display_name: string;
  platform_account_id: string;
  access_scope?: CrawlerAccountAccessScope;
  can_manage?: boolean;
  status: CrawlerAccountStatus;
  last_validated_at: string;
  last_used_at: string;
  last_error: string;
  cooldown_until: string;
  failure_kind: string;
  has_auth_state: boolean;
  auth_state_updated_at: string;
  created_at: string;
  updated_at: string;
}

interface ApiSharedCrawlerPoolSummary {
  total?: number;
  ready?: number;
  busy?: number;
  unavailable?: number;
  by_platform?: Partial<Record<CrawlerAccountPlatform, {
    total?: number;
    ready?: number;
    busy?: number;
    unavailable?: number;
  }>>;
}

interface ApiCrawlerAccountLoginSession {
  id: string;
  account_id: string;
  platform: CrawlerAccountPlatform;
  status: CrawlerAccountLoginStatus;
  interactive?: boolean;
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

export async function fetchCrawlerAccounts(): Promise<CrawlerAccount[]> {
  return (await fetchCrawlerAccountOverview()).accounts;
}

export async function fetchCrawlerAccountOverview(): Promise<{
  accounts: CrawlerAccount[];
  sharedPool: SharedCrawlerPoolSummary;
}> {
  const payload = await apiRequest<{
    items: ApiCrawlerAccount[];
    shared_pool?: ApiSharedCrawlerPoolSummary;
  }>("/api/crawler-accounts");
  return {
    accounts: (payload.items || []).map(mapCrawlerAccount),
    sharedPool: mapSharedPool(payload.shared_pool)
  };
}

export async function createCrawlerAccount(input: CrawlerAccountInput): Promise<CrawlerAccount> {
  const payload = await apiRequest<{ item: ApiCrawlerAccount }>("/api/crawler-accounts", {
    method: "POST",
    body: JSON.stringify(toApiInput(input))
  });
  return mapCrawlerAccount(payload.item);
}

export async function updateCrawlerAccount(
  accountId: string,
  input: Partial<CrawlerAccountInput> & { status?: CrawlerAccountStatus }
): Promise<CrawlerAccount> {
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
  return mapCrawlerAccount(payload.item);
}

export async function deleteCrawlerAccount(accountId: string): Promise<void> {
  await apiRequest(`/api/crawler-accounts/${encodeURIComponent(accountId)}`, {
    method: "DELETE"
  });
}

export function loginControlHeaders(): Record<string, string> {
  const key = "crawler-login-control-token";
  let token = window.sessionStorage.getItem(key);
  if (!token) {
    token = crypto.randomUUID() + crypto.randomUUID();
    window.sessionStorage.setItem(key, token);
  }
  return { "X-Login-Token": token };
}

export async function startCrawlerAccountLogin(accountId: string): Promise<CrawlerAccountLoginSession> {
  const payload = await apiRequest<{ item: ApiCrawlerAccountLoginSession }>(
    `/api/crawler-accounts/${encodeURIComponent(accountId)}/login-sessions`,
    { method: "POST", headers: loginControlHeaders() }
  );
  return mapLoginSession(payload.item);
}

export async function fetchCrawlerAccountLoginSession(sessionId: string): Promise<CrawlerAccountLoginSession> {
  const payload = await apiRequest<{ item: ApiCrawlerAccountLoginSession }>(
    `/api/crawler-account-login-sessions/${encodeURIComponent(sessionId)}`,
    { headers: loginControlHeaders() }
  );
  return mapLoginSession(payload.item);
}

export async function cancelCrawlerAccountLoginSession(sessionId: string): Promise<void> {
  await apiRequest(`/api/crawler-account-login-sessions/${encodeURIComponent(sessionId)}`, {
    headers: loginControlHeaders(),
    method: "DELETE"
  });
}

function toApiInput(input: CrawlerAccountInput) {
  return {
    platform: input.platform,
    display_name: input.displayName,
    platform_account_id: input.platformAccountId,
    access_scope: input.accessScope
  };
}

function mapCrawlerAccount(item: ApiCrawlerAccount): CrawlerAccount {
  return {
    id: item.id,
    platform: item.platform,
    displayName: item.display_name,
    platformAccountId: item.platform_account_id,
    accessScope: item.access_scope || "private",
    canManage: item.can_manage ?? true,
    status: item.status,
    lastValidatedAt: item.last_validated_at,
    lastUsedAt: item.last_used_at,
    lastError: item.last_error,
    cooldownUntil: item.cooldown_until,
    failureKind: item.failure_kind,
    hasAuthState: item.has_auth_state,
    authStateUpdatedAt: item.auth_state_updated_at,
    createdAt: item.created_at,
    updatedAt: item.updated_at
  };
}

function mapSharedPool(item?: ApiSharedCrawlerPoolSummary): SharedCrawlerPoolSummary {
  const byPlatform = Object.fromEntries(
    Object.entries(item?.by_platform || {}).map(([platform, summary]) => [platform, {
      total: Number(summary?.total || 0),
      ready: Number(summary?.ready || 0),
      busy: Number(summary?.busy || 0),
      unavailable: Number(summary?.unavailable || 0)
    }])
  ) as SharedCrawlerPoolSummary["byPlatform"];
  return {
    total: Number(item?.total || 0),
    ready: Number(item?.ready || 0),
    busy: Number(item?.busy || 0),
    unavailable: Number(item?.unavailable || 0),
    byPlatform
  };
}

function mapLoginSession(item: ApiCrawlerAccountLoginSession): CrawlerAccountLoginSession {
  return {
    id: item.id,
    accountId: item.account_id,
    platform: item.platform,
    status: item.status,
    interactive: item.interactive || false,
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
