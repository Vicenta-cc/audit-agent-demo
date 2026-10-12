export type CrawlerAccountPlatform = "xhs" | "dy" | "ks";
export type CrawlerAccountStatus = "active" | "login_required" | "expired" | "disabled";
export type CrawlerAccountAccessScope = "private" | "public";
export type CrawlerAccountLoginStatus =
  | "starting"
  | "waiting_scan"
  | "scanned"
  | "interactive"
  | "finalizing"
  | "success"
  | "failed"
  | "expired"
  | "cancelled";

export interface CrawlerAccount {
  collectionReady?: boolean;
  collectionBlockReason?: string;
  proxyId?: string | null;
  proxyRequired?: boolean;
  needsProxyLoginValidation?: boolean;
  validatedProxyBindingRevision?: number;
  validatedProxyConfigRevision?: number;
  bindingRevision?: number;
  id: string;
  platform: CrawlerAccountPlatform;
  displayName: string;
  platformAccountId: string;
  accessScope: CrawlerAccountAccessScope;
  canManage: boolean;
  status: CrawlerAccountStatus;
  lastValidatedAt: string;
  lastUsedAt: string;
  lastError: string;
  cooldownUntil: string;
  failureKind: string;
  hasAuthState: boolean;
  authStateUpdatedAt: string;
  createdAt: string;
  updatedAt: string;
}

export interface CrawlerAccountLoginSession {
  id: string;
  accountId: string;
  platform: CrawlerAccountPlatform;
  status: CrawlerAccountLoginStatus;
  interactive?: boolean;
  v2?: import("../services/crawlerLoginV2").LoginSnapshot;
  qrImageDataUrl: string;
  qrExpiresAt: string;
  finalizingStartedAt: string;
  finalizingDurationSeconds: number;
  platformAccountId: string;
  error: string;
  createdAt: string;
  updatedAt: string;
  expiresAt: string;
}

export interface CrawlerAccountInput {
  platform: CrawlerAccountPlatform;
  displayName: string;
  platformAccountId: string;
  accessScope: CrawlerAccountAccessScope;
}

export interface SharedCrawlerPoolSummary {
  total: number;
  ready: number;
  busy: number;
  unavailable: number;
  byPlatform: Partial<Record<CrawlerAccountPlatform, {
    total: number;
    ready: number;
    busy: number;
    unavailable: number;
  }>>;
}
