import { defineConfig } from '@playwright/test';
export default defineConfig({ testDir: './tests', testMatch: 'resourceConversationPresentation.spec.ts', workers: 1, reporter: 'line', outputDir: './test-results/logic' });
