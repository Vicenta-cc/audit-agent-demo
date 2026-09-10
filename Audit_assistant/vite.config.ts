import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { resolveM3ApiProxyTarget } from "./src/runtimeBoundary";

export default defineConfig(({ mode, command }) => {
  const apiTarget = command === "serve"
    ? resolveM3ApiProxyTarget(loadEnv(mode, ".", "").VITE_API_PROXY_TARGET)
    : undefined;
  return {
    base: "/",
    plugins: [react()],
    server: apiTarget ? {
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true
        }
      }
    } : undefined,
    build: {
      outDir: "dist",
      emptyOutDir: true
    }
  };
});
