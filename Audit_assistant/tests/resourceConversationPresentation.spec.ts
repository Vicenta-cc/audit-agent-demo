import { expect, test } from '@playwright/test';
import { presentCreationAssistantContent } from '../src/features/investigation/workspaceRecovery';
import { formatCreationErrorMessage } from '../src/features/investigation/confirmationView';

test('resource availability tables preserve the generation consent question', () => {
  const answer = '### 资源现状\n\n| 资源类型 | 状态 | 说明 |\n|---|---|---|\n| 平台 | 抖音可用 | — |\n| 审核规则 | 无匹配 | 需要生成 |\n\n可以生成临时审核规则和搜索词，不会保存为正式后台资源。是否需要生成？';
  expect(presentCreationAssistantContent(answer)).toBe(answer);
});

test('successful resource answers keep their content when metadata rows are present', () => {
  const answer = '已完成临时词库与现有规则读取。\n\n| 字段 | 值 |\n|---|---|\n| 会话 edit_id | lexicon-edit:private |\n| 标题 | 维汉婚恋词库 |\n\n变体和标签不进入搜索。\n\n| 词条 | 类型 |\n|---|---|\n| 维汉通婚 | 主词 |\n| 维汉婚恋 | 变体 |\n\n应用阶段：comment_audit、fusion_audit。';
  const shown = presentCreationAssistantContent(answer);
  expect(shown).toContain('已完成临时词库与现有规则读取');
  expect(shown).toContain('| 维汉婚恋 | 变体 |');
  expect(shown).not.toContain('lexicon-edit:private');
  expect(shown).not.toContain('暂时无法保存');
});

test('provider content rejection is distinguished from a storage failure', () => {
  const raw = 'HTTP 400: data: {"error":{"code":"data_inspection_failed"}}';
  expect(presentCreationAssistantContent(raw)).toContain('模型服务的内容检查拒绝');
  expect(formatCreationErrorMessage(raw)).not.toContain('请稍后重试');
});

test('resource read and save tables remain readable while internal diagnostics stay hidden', () => {
  const answer = '| 项目 | 草稿 | 状态 |\n|---|---|---|\n| 维汉婚恋规则 | 已修改 | 未保存 |';
  expect(presentCreationAssistantContent(answer)).toBe(answer);
  expect(presentCreationAssistantContent('| 项目 | 内容 |\n|---|---|\n| Draft ID | investigation-draft:secret |'))
    .toBe('当前操作暂时无法完成，请稍后重试。');
  expect(presentCreationAssistantContent('RESOURCE_STALE revision conflict'))
    .toBe('配置刚刚发生变化，已载入最新内容，请重新核对。');
});

test('internal runtime branding is never shown as the assistant identity', () => {
  const shown = presentCreationAssistantContent('你好！我是 Hermes Agent 0.20.4，可以协助配置调查。');
  expect(shown).toBe('你好！我是研判助手，可以协助配置调查。');
  expect(shown).not.toContain('Hermes');
});
