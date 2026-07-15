import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/saas-v2/",
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true
  }
});
