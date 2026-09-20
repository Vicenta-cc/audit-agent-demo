import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dedicated preview server: deliberately has no production API proxy.
export default defineConfig({
  plugins: [react(), {
    name: "block-demo-api-network",
    configureServer(server) {
      server.middlewares.use("/api", (_req, res) => {
          res.writeHead(403, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ message: "假数据预览不连接后端" }));
      });
    }
  }],
  server: { host: "127.0.0.1", port: 4175, strictPort: true }
});
