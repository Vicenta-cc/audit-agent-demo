import { createRoot } from "react-dom/client";
import { CrawlerAccountLoginDialog } from "../src/features/crawler-accounts/CrawlerAccountLoginDialog";
import "../src/styles/crawler-accounts.css";
import type { CrawlerAccount } from "../src/types/crawlerAccounts";
const account: CrawlerAccount = {
  id: "account-fixture",
  platform: "dy",
  displayName: "测试抖音账号",
  platformAccountId: "fixture",
  accessScope: "private",
  canManage: true,
  status: "login_required",
  lastValidatedAt: "",
  lastUsedAt: "",
  lastError: "",
  cooldownUntil: "",
  failureKind: "",
  hasAuthState: false,
  authStateUpdatedAt: "",
  createdAt: "",
  updatedAt: "",
};
createRoot(document.getElementById("root")!).render(
  <CrawlerAccountLoginDialog
    account={account}
    onSuccess={() => undefined}
    onClose={() => {
      document.getElementById("root")!.textContent = "登录窗口已关闭";
    }}
  />,
);
