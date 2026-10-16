import { expect, test } from '@playwright/test';
import type { InvestigationRunProjection } from '../src/types/investigationCreation';
import { mapInvestigationRunState } from '../src/features/investigation/investigationRunState';
import { formatRunFailureMessage } from '../src/features/investigation/runPresentation';

const interrupted = {
  status: 'INTERRUPTED', crawl_status: 'stopped', analysis_status: 'completed',
  error_code: 'pipeline_result_unknown', error_message: 'PROXY_BUSY',
  task_stats: { completed_analysis_count: 1 }, available_actions: { resume_crawl: false },
} as InvestigationRunProjection;

test('unconfirmed capture does not promise a retry or blame account login', () => {
  const run = { ...interrupted, recovery_blocker: 'geographic_capture_reconciliation_required' };
  expect(mapInvestigationRunState(run).label).toBe('调查已中断，采集记录待核对');
  const message = formatRunFailureMessage(run);
  expect(message).toContain('已完成的审核结果会保留');
  expect(message).not.toContain('登录');
  expect(message).not.toContain('PROXY_BUSY');
});

test('proven preflight deferral remains resumable without claiming capture success', () => {
  const run = { ...interrupted, error_code: 'geographic_preflight_deferred', available_actions: { resume_crawl: true } };
  expect(mapInvestigationRunState(run).label).toContain('可恢复');
  expect(formatRunFailureMessage(run)).toContain('后续采集暂未开始');
});

test('real account failure retains its specific recovery guidance', () => {
  expect(formatRunFailureMessage({ ...interrupted, error_code: 'crawler_account_login_required' })).toContain('登录状态');
});
