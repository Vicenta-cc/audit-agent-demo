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
  cooldown_until: string;
  failure_kind: string;
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

export async function fetchCrawlerAccounts(): Promise<CrawlerAccount[]> {
  const payload = await apiRequest<{ items: ApiCrawlerAccount[] }>("/api/crawler-accounts");
  return (payload.items || []).map(mapCrawlerAccount);
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

export async function startCrawlerAccountLogin(accountId: string): Promise<CrawlerAccountLoginSession> {
  const payload = await apiRequest<{ item: ApiCrawlerAccountLoginSession }>(
    `/api/crawler-accounts/${encodeURIComponent(accountId)}/login-sessions`,
    { method: "POST" }
  );
  return mapLoginSession(payload.item);
}

export async function fetchCrawlerAccountLoginSession(sessionId: string): Promise<CrawlerAccountLoginSession> {
  const payload = await apiRequest<{ item: ApiCrawlerAccountLoginSession }>(
    `/api/crawler-account-login-sessions/${encodeURIComponent(sessionId)}`
  );
  return mapLoginSession(payload.item);
}

export async function cancelCrawlerAccountLoginSession(sessionId: string): Promise<void> {
  await apiRequest(`/api/crawler-account-login-sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE"
  });
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
    cooldownUntil: item.cooldown_until,
    failureKind: item.failure_kind,
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
