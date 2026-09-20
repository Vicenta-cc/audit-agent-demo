import { defineConfig } from "@playwright/test";
import base from "./playwright.config";
export default defineConfig({
  ...base,
  testMatch: ["authBoundary.spec.ts", "taskLifecycle.spec.ts"],
  outputDir: "/tmp/xhs-phase5-webkit-results",
  use: { baseURL: "http://127.0.0.1:4173", browserName: "webkit" }
});
