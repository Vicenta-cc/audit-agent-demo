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
  route_mode?: string;
  route_state?: string;
  paused_until?: number | null;
  pause_reason?: string;
  recovery_state?: string;
  shared_exit_generation?: number;
  collection_ready?: boolean;
  collection_block_reason?: string;
  proxy_id?: string | null;
  proxy_required?: boolean;
  needs_proxy_login_validation?: boolean;
  validated_proxy_binding_revision?: number;
  validated_proxy_config_revision?: number;
  binding_revision?: number;
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
  protocol_version?: number;
  operation_id?: string;
  cleanup_state?: string;
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
  error: string | { message?: string; code?: string } | null;
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

export function loginControlHeaders(accountId?: string): Record<string, string> {
  const key = accountId ? `crawler-login-control-token:${accountId}` : "crawler-login-control-token";
  let token = window.sessionStorage.getItem(key);
  if (!token && accountId) {
    token = window.sessionStorage.getItem("crawler-login-control-token");
    if (token) window.sessionStorage.setItem(key, token);
  }
  if (!token) {
    token = crypto.randomUUID() + crypto.randomUUID();
    window.sessionStorage.setItem(key, token);
  }
  return { "X-Login-Token": token };
}

export function renewLoginControlToken(accountId: string): void {
  window.sessionStorage.setItem(`crawler-login-control-token:${accountId}`, crypto.randomUUID() + crypto.randomUUID());
  const prefix = `crawler-account-login-intent:${accountId}:`;
  for (let index = window.sessionStorage.length - 1; index >= 0; index--) {
    const key = window.sessionStorage.key(index);
    if (key?.startsWith(prefix)) window.sessionStorage.removeItem(key);
  }
}

export async function claimCrawlerAccountLogin(accountId: string): Promise<CrawlerAccountLoginSession> {
  const payload = await apiRequest<{ item: ApiCrawlerAccountLoginSession }>(
    `/api/crawler-accounts/${encodeURIComponent(accountId)}/login-session-claims`,
    { method: "POST", headers: loginControlHeaders(accountId), body: "{}" },
  );
  return mapLoginSession(payload.item);
}

export async function startCrawlerAccountLogin(accountId: string, intent: "check_or_login" | "reauth" | "recover" = "check_or_login", sourceOperationId?: string, browserMode?: "compatibility"): Promise<CrawlerAccountLoginSession> {
  const intentKey = `crawler-account-login-intent:${accountId}:${intent}:${sourceOperationId || "new"}:${browserMode || "default"}`;
  const key = window.sessionStorage.getItem(intentKey) || crypto.randomUUID();
  window.sessionStorage.setItem(intentKey, key);
  const payload = await apiRequest<{ item: ApiCrawlerAccountLoginSession }>(
    `/api/crawler-accounts/${encodeURIComponent(accountId)}/login-sessions`,
    { method: "POST", headers: { ...loginControlHeaders(accountId), "Idempotency-Key": key }, body: JSON.stringify({ supported_protocols: browserMode === "compatibility" ? [2] : [2, 1], intent, ...(browserMode ? { browser_mode: browserMode } : {}), ...(sourceOperationId ? { source_operation_id: sourceOperationId } : {}) }) }
  );
  const session = mapLoginSession(payload.item);
  window.sessionStorage.removeItem?.(intentKey);
  return session;
}

export async function fetchCrawlerAccountLoginSession(sessionId: string, accountId?: string): Promise<CrawlerAccountLoginSession> {
  if (!sessionId.trim()) throw new Error("登录会话标识无效，请重新打开登录窗口");
  const payload = await apiRequest<{ item: ApiCrawlerAccountLoginSession }>(
    `/api/crawler-account-login-sessions/${encodeURIComponent(sessionId)}`,
    { headers: loginControlHeaders(accountId) }
  );
  return mapLoginSession(payload.item);
}

export async function cancelCrawlerAccountLoginSession(sessionId: string, accountId?: string): Promise<{ cleanupComplete: boolean }> {
  if (!sessionId.trim()) throw new Error("登录会话标识无效，请重新打开登录窗口");
  const result = await apiRequest<{ item?: { cleanup_state?: string } }>(`/api/crawler-account-login-sessions/${encodeURIComponent(sessionId)}`, {
    headers: loginControlHeaders(accountId),
    method: "DELETE"
  });
  return { cleanupComplete: result.item?.cleanup_state === "complete" };
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
    routeMode: item.route_mode,
    routeState: item.route_state,
    pausedUntil: item.paused_until ? new Date(item.paused_until * 1000).toISOString() : "",
    pauseReason: item.pause_reason,
    recoveryState: item.recovery_state,
    sharedExitGeneration: item.shared_exit_generation,
    collectionReady: item.collection_ready,
    collectionBlockReason: item.collection_block_reason,
    proxyId: item.proxy_id,
    proxyRequired: item.proxy_required,
    needsProxyLoginValidation: item.needs_proxy_login_validation ?? false,
    validatedProxyBindingRevision: item.validated_proxy_binding_revision,
    validatedProxyConfigRevision: item.validated_proxy_config_revision,
    bindingRevision: item.binding_revision,
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
  const terminalOperation = item.protocol_version === 2 && Boolean(item.operation_id)
    && ["success", "failed", "expired", "cancelled"].includes(item.status)
    && item.cleanup_state === "complete";
  if (!item.id && !terminalOperation) throw new Error("登录服务未返回有效会话，请重新打开登录窗口");
  return {
    v2: item.protocol_version === 2 ? item as unknown as import("./crawlerLoginV2").LoginSnapshot : undefined,
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
    error: typeof item.error === "string" ? item.error : item.error?.message || "",
    createdAt: item.created_at,
    updatedAt: item.updated_at,
    expiresAt: item.expires_at
  };
}
