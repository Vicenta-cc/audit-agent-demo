import { expect, test } from '@playwright/test';
import { presentCreationAssistantContent } from '../src/features/investigation/workspaceRecovery';

test('resource availability tables preserve the generation consent question', () => {
  const answer = '### 资源现状\n\n| 资源类型 | 状态 | 说明 |\n|---|---|---|\n| 平台 | 抖音可用 | — |\n| 审核规则 | 无匹配 | 需要生成 |\n\n可以生成临时审核规则和搜索词，不会保存为正式后台资源。是否需要生成？';
  expect(presentCreationAssistantContent(answer)).toBe(answer);
});

test('resource read and save tables remain readable while internal diagnostics stay hidden', () => {
  const answer = '| 项目 | 草稿 | 状态 |\n|---|---|---|\n| 维汉婚恋规则 | 已修改 | 未保存 |';
  expect(presentCreationAssistantContent(answer)).toBe(answer);
  expect(presentCreationAssistantContent('| 项目 | 内容 |\n|---|---|\n| Draft ID | investigation-draft:secret |'))
    .toBe('当前操作暂时无法完成，请稍后重试。');
  expect(presentCreationAssistantContent('RESOURCE_STALE revision conflict'))
    .toBe('配置刚刚发生变化，已载入最新内容，请重新核对。');
});
