import { apiRequest } from "./apiClient";
import type { CrawlerProxy, CrawlerProxyInput, CrawlerProxyOverview } from "../types/crawlerProxies";
export const fetchCrawlerProxies = () => apiRequest<CrawlerProxyOverview>("/api/crawler-proxies");
export async function createCrawlerProxy(input: CrawlerProxyInput): Promise<CrawlerProxy> {
  return (await apiRequest<{ item: CrawlerProxy }>("/api/crawler-proxies", { method: "POST", body: JSON.stringify(input) })).item;
}
export async function updateCrawlerProxy(id: string, input: Partial<CrawlerProxyInput> & { expected_revision: number; status?: CrawlerProxy["status"]; clear_credentials?: boolean }): Promise<CrawlerProxy> {
  return (await apiRequest<{ item: CrawlerProxy }>(`/api/crawler-proxies/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(input) })).item;
}
export async function deleteCrawlerProxy(id: string, expectedRevision: number): Promise<void> {
  await apiRequest(`/api/crawler-proxies/${encodeURIComponent(id)}?expected_revision=${expectedRevision}`, { method: "DELETE" });
}

export async function grantCrawlerProxyUse(proxyId: string, userId: string): Promise<void> {
  await apiRequest(`/api/admin/users/${encodeURIComponent(userId)}/grants/crawler-proxy/${encodeURIComponent(proxyId)}`, {
    method: "PUT", body: JSON.stringify({ permission: "use" })
  });
}

export async function revokeCrawlerProxyUse(proxyId: string, userId: string): Promise<void> {
  await apiRequest(`/api/admin/users/${encodeURIComponent(userId)}/grants/crawler-proxy/${encodeURIComponent(proxyId)}/use`, {
    method: "DELETE"
  });
}

export async function recoverCrawlerProxy(id: string, expectedRevision: number): Promise<CrawlerProxy> {
  return (await apiRequest<{ item: CrawlerProxy }>(`/api/crawler-proxies/${encodeURIComponent(id)}/recover`, {
    method: "POST", body: JSON.stringify({ expected_revision: expectedRevision })
  })).item;
}

export async function probeCrawlerProxy(id: string, accountId: string, expectedRevision: number): Promise<void> {
  await apiRequest(`/api/crawler-proxies/${encodeURIComponent(id)}/probe`, {
    method: "POST", body: JSON.stringify({ account_id: accountId, expected_revision: expectedRevision })
  });
}
