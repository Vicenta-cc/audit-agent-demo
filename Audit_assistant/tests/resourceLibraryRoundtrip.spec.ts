import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { ruleSetView, rulesContent, type RulesContent, type Resource } from '../src/services/resourceLibrary';

const fixture = JSON.parse(readFileSync(new URL('../../tests/fixtures/recruitment_fraud_ruleset.json', import.meta.url), 'utf8')) as RulesContent;
const mapping = [{ source_file: 'original.json', source_locator: 'rule-1', migrated_semantics: 'preserve provenance' }];
function resource(): Resource<RulesContent> {
  const content = structuredClone(fixture);
  content.schema_version ??= 0;
  for (const c of content.categories) {
    c.description ??= '';
    for (const r of c.rules) { r.rule_exemptions ??= []; r.enabled ??= true; }
  }
  content.categories.reverse();
  content.categories[0].rules.reverse();
  content.categories[0].rules[0].source_mappings = mapping;
  content.categories[0].rules[0].application_stages.reverse();
  content.general_exemptions[0].source_mappings = mapping;
  content.general_exemptions[0].enabled = false;
  return { id: 'rule', kind: 'ruleset', content, version: 1, published_version: 1, editable: true };
}
test('untouched editor roundtrip preserves every stored field', () => {
  const original = resource();
  expect(rulesContent(ruleSetView(original), original.content)).toEqual(original.content);
});
test('rename changes only name, retaining ordering, stages and source mappings', () => {
  const original = resource(); const view = ruleSetView(original); view.name = '新名称';
  expect(rulesContent(view, original.content)).toEqual({ ...original.content, name: '新名称' });
});
test('editing a rule and general exemption preserves their provenance', () => {
  const original = resource(); const view = ruleSetView(original);
  const id = original.content.categories[0].rules[0].rule_id;
  view.categories.flatMap(c => c.rules).find(r => r.id === id)!.content = '新条件';
  view.generalExemptions[0].description = '新豁免条件';
  const expected = structuredClone(original.content);
  expected.categories[0].rules[0].hit_condition = '新条件'; expected.general_exemptions[0].condition = '新豁免条件';
  expect(rulesContent(view, original.content)).toEqual(expected);
});
test('adding a rule leaves existing order values intact', () => {
  const original = resource(); const view = ruleSetView(original);
  view.categories[0].rules.push({ ...view.categories[0].rules[0], id: 'new.rule', name: '新增规则' });
  const output = rulesContent(view, original.content);
  const cat = output.categories.find(c => c.category_id === view.categories[0].id)!;
  const old = original.content.categories.find(c => c.category_id === cat.category_id)!;
  expect(cat.rules.slice(0, -1)).toEqual(old.rules);
  expect(cat.rules.at(-1)!.order).toBeGreaterThan(Math.max(...old.rules.map(r => r.order)));
});

test('intentional rule reorder survives saving and reload', () => {
  const original = resource(); const view = ruleSetView(original);
  const cat = view.categories[0];
  cat.rules.reverse();
  const output = rulesContent(view, original.content);
  expect(ruleSetView({ ...original, content: output }).categories[0].rules.map(r => r.id)).toEqual(cat.rules.map(r => r.id));
  expect(output.categories.find(c => c.category_id !== cat.id)).toEqual(original.content.categories.find(c => c.category_id !== cat.id));
});
test('new rule with empty optional editor notes uses its condition', () => {
  const original = resource(); const view = ruleSetView(original);
  view.categories[0].rules.push({ ...view.categories[0].rules[0], id: 'new.rule', notes: '', content: '新增条件' });
  const output = rulesContent(view, original.content);
  expect(output.categories.flatMap(c => c.rules).find(r => r.rule_id === 'new.rule')!.adjudication_notes).toBe('新增条件');
});
test('deleting a rule preserves remaining stored order and metadata', () => {
  const original = resource(); const view = ruleSetView(original);
  const removed = view.categories[0].rules.pop()!.id;
  const expected = structuredClone(original.content);
  expected.categories.forEach(c => { c.rules = c.rules.filter(r => r.rule_id !== removed); });
  expect(rulesContent(view, original.content)).toEqual(expected);
});

test('opening a published rule and clicking save sends no mutation', async ({ page }) => {
  const saved = { ...resource(), published_revision_id: 'published-1' };
  const writes: string[] = [];
  await page.route('**/api/**', async route => {
    const req = route.request(); const path = new URL(req.url()).pathname;
    if (req.method() !== 'GET') writes.push(path);
    await route.fulfill({ json: path === '/api/resource-library/ruleset/rule' ? saved : { items: [{ id: 'rule' }], has_more: false } });
  });
  await page.goto('/');
  await page.evaluate(async () => {
    const load = (path: string) => import(path);
    const React = (await load('/node_modules/.vite/deps/react.js')).default;
    const { mountPresentation } = await load('/tests/presentationMount.tsx');
    const { KnowledgeCenterPage } = await load('/src/pages/KnowledgeCenterPage.tsx');
    const host = document.createElement('div'); document.body.replaceChildren(host);
    mountPresentation(host, React.createElement(KnowledgeCenterPage, { initialRuleSetId: 'rule' }));
  });
  await page.getByRole('button', { name: '保存发布', exact: true }).click();
  await expect(page.getByText('内容未修改，仍使用当前已发布版本。')).toBeVisible();
  expect(writes).toEqual([]);
});
