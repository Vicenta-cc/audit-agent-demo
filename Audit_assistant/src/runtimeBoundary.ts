export function resolveM3ApiProxyTarget(value: string | undefined): string {
  const target = value?.trim() || "";
  if (!target) {
    throw new Error(
      "VITE_API_PROXY_TARGET must be set explicitly for the authoritative M3 backend"
    );
  }
  let parsed: URL;
  try {
    parsed = new URL(target);
  } catch {
    throw new Error("VITE_API_PROXY_TARGET must be an absolute backend URL");
  }
  if (parsed.port === "8000") {
    throw new Error(
      "VITE_API_PROXY_TARGET points to legacy backend port 8000; configure the M3 backend instead"
    );
  }
  return target.replace(/\/+$/, "");
}
