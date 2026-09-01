import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { resolveM3ApiProxyTarget } from "./src/runtimeBoundary";

export default defineConfig(({ mode }) => {
  const apiTarget = resolveM3ApiProxyTarget(loadEnv(mode, ".", "").VITE_API_PROXY_TARGET);
  return {
    base: "/",
    plugins: [react()],
    server: {
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true
        }
      }
    },
    build: {
      outDir: "dist",
      emptyOutDir: true
    }
  };
});
