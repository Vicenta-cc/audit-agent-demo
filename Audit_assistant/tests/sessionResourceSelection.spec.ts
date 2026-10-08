import { expect, test, type Page } from '@playwright/test';
import type { ResourceState, SelectionRequest } from '../src/services/sessionResources';

const items = [
  { key: 'edit/a/1', type: 'edit_version', kind: 'ruleset' as const, title: '旅游规则 A', version: 1, content_hash: 'a1', is_current: true },
  { key: 'edit/b/3', type: 'edit_version', kind: 'ruleset' as const, title: '旅游规则 B', version: 3, content_hash: 'b3', is_current: false },
  { key: 'edit/b/4', type: 'edit_version', kind: 'ruleset' as const, title: '旅游规则 B', version: 4, content_hash: 'b4', is_current: true },
  { key: 'edit/words/1', type: 'edit_version', kind: 'lexicon' as const, title: '旅游词库', version: 1, content_hash: 'words1', is_current: true },
];
async function mount(page: Page, options: { loseFirstReply?: boolean; conflict?: boolean; failRead?: boolean } = {}) {
  let state: ResourceState = { status: 'complete', items, next_cursor: '', selection: { status: 'unknown' } };
  const posts: SelectionRequest[] = [];
  const receipts = new Set<string>();
  await page.route('**/api/investigation-workspaces/selection-test/resource-state/detail?**', route => route.fulfill({ json: {
    content: { audit_goal: '已保存的审核目标', general_exemptions: [], categories: [] }
  } }));
  await page.route('**/api/investigation-workspaces/selection-test/resource-state?**', route =>
    options.failRead ? route.fulfill({ status: 503, json: { detail: { code: 'RESOURCE_STATE_UNAVAILABLE' } } }) : route.fulfill({ json: state }));
  await page.route('**/api/investigation-workspaces/selection-test/resource-selection', async route => {
    const body: SelectionRequest = route.request().postDataJSON();
    posts.push(body);
    if (options.conflict) return route.fulfill({ status: 409, json: { detail: { code: 'RESOURCE_SELECTION_CONFLICT' } } });
    if (!receipts.has(body.event_id)) {
      const item = items.find(i => i.key === body.key);
      state = { ...state, selection: { status: 'recorded', slots: [
        ...(state.selection.slots || []).filter(s => s.kind !== body.kind || s.purpose !== body.purpose),
        { ...body, status: body.key ? 'selected' : 'cleared', title: item?.title, version: item?.version },
      ] } };
      receipts.add(body.event_id);
    }
    if (options.loseFirstReply && posts.length === 1) return route.abort('failed');
    return route.fulfill({ json: { replayed: posts.length > 1 } });
  });
  await page.goto('/tests/sessionResourceSelection.html');
  await page.getByText('会话资源选择', { exact: true }).click();
  return { posts, change: (s: ResourceState) => { state = s; } };
}
const row = (page: Page, text: string) => page.locator('li').filter({ hasText: text });

test('explicit edit B and view A restore separately after reload; historical B cannot be edited', async ({ page }) => {
  const { posts } = await mount(page);
  await expect(page.getByText('尚未记录明确选择。')).toBeVisible();
  expect(posts).toHaveLength(0);
  await row(page, '旅游规则 A').getByText('查看版本内容', { exact: true }).click();
  await expect(page.getByText('已保存的审核目标')).toBeVisible();
  expect(posts).toHaveLength(0);
  await expect(row(page, '旅游规则 B · v3').getByRole('button', { name: '选为编辑对象' })).toHaveCount(0);
  await row(page, '旅游规则 B · v4').getByRole('button', { name: '选为编辑对象' }).click();
  await expect(page.getByLabel('已记录的选择')).toContainText('旅游规则 B · v4');
  await row(page, '旅游规则 A').getByRole('button', { name: '选为查看对象' }).click();
  await expect(page.getByLabel('已记录的选择')).toContainText('查看对象：旅游规则 A · v1');
  await page.reload();
  await page.getByText('会话资源选择', { exact: true }).click();
  await expect(page.getByLabel('已记录的选择')).toContainText('编辑对象：旅游规则 B · v4');
  await expect(page.getByLabel('已记录的选择')).toContainText('查看对象：旅游规则 A · v1');
  expect(posts).toHaveLength(2);
  await page.screenshot({ path: '/tmp/session-resource-selection-1b.png' });
});

test('lost reply retry keeps the event and version; reads never resubmit', async ({ page }) => {
  const { posts } = await mount(page, { loseFirstReply: true });
  await row(page, '旅游词库').getByRole('button', { name: '选为编辑对象' }).click();
  await expect(page.getByRole('alert')).toContainText('暂时无法确认');
  await page.getByRole('button', { name: '刷新资源' }).click();
  await expect(page.getByLabel('已记录的选择')).toContainText('旅游词库');
  expect(posts).toHaveLength(1);
  await page.getByRole('button', { name: '重试记录' }).click();
  await expect(page.getByText('选择已记录。')).toBeVisible();
  expect(posts).toHaveLength(2);
  expect(posts[1]).toEqual(posts[0]);
});

test('stale tab is not automatically retried or reported as selected', async ({ page }) => {
  const { posts } = await mount(page, { conflict: true });
  await row(page, '旅游规则 B · v4').getByRole('button', { name: '选为编辑对象' }).click();
  await expect(page.getByRole('alert')).toContainText('已变化');
  await expect(page.getByRole('button', { name: '重试记录' })).toHaveCount(0);
  await expect(page.getByLabel('已记录的选择')).toContainText('尚未记录明确选择');
  expect(posts).toHaveLength(1);
});

test('read failure is not an empty resource list and offers no mutation', async ({ page }) => {
  const { posts } = await mount(page, { failRead: true });
  await expect(page.getByRole('alert')).toContainText('读取失败');
  await expect(page.getByText('本会话暂无可选择的编辑稿。')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '选为编辑对象' })).toHaveCount(0);
  expect(posts).toHaveLength(0);
});

test('clear is explicit and carries the latest slot event', async ({ page }) => {
  const { posts } = await mount(page);
  await row(page, '旅游规则 B · v4').getByRole('button', { name: '选为编辑对象' }).click();
  await expect(page.getByLabel('已记录的选择')).toContainText('旅游规则 B · v4');
  await page.getByRole('button', { name: '取消选择' }).click();
  await expect(page.getByLabel('已记录的选择')).toContainText('未选择');
  expect(posts[1].expected_event_id).toBe(posts[0].event_id);
  expect(posts[1].key).toBe('');
});
