export type CrawlerAccountPlatform = "xhs" | "dy" | "ks";
export type CrawlerAccountStatus = "active" | "login_required" | "expired" | "disabled";
export type CrawlerAccountLoginStatus =
  | "starting"
  | "waiting_scan"
  | "finalizing"
  | "success"
  | "failed"
  | "expired"
  | "cancelled";

export interface CrawlerAccount {
  id: string;
  platform: CrawlerAccountPlatform;
  displayName: string;
  platformAccountId: string;
  status: CrawlerAccountStatus;
  lastValidatedAt: string;
  lastUsedAt: string;
  lastError: string;
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
}
