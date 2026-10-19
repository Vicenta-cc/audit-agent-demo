import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests',
  testMatch: [
    'analysisRecordTarget.spec.ts',
    'resourceConversationPresentation.spec.ts',
    'resourceMarkdownRendering.spec.tsx',
    'historicalReportPresentation.spec.tsx',
    'investigationStreamingLogic.spec.ts',
    'geographicRuntimePresentation.spec.tsx'
  ],
  workers: 1,
  reporter: 'line',
  outputDir: './test-results/logic'
});
