import { defineConfig } from "@playwright/test";
export default defineConfig({
 testDir: "./tests", testMatch: "*.real.acceptance.ts", workers: 1,
 timeout: 60000, reporter: "line",
 outputDir: "/tmp/xhs-full-real-acceptance",
 use: { baseURL: "http://127.0.0.1:3398", launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }, screenshot: "only-on-failure" }
});
