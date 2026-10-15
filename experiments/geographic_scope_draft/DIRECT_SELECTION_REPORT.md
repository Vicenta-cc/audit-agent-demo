# P1 地域直接选择与 AI 状态同步报告

日期：2026-10-15。

后续实测更新：已按用户要求启动前端并通过香港专用出口调用真实 DeepSeek，修改地域后只说“继续”，模型读到“吉林省”及 revision 2，规则/词库和相关资源表不变。详见 [真实模型验证报告](direct-selection-live/README.md)。下文“本轮未请求真实 DeepSeek”描述的是最初独立测试阶段。

## 实施位置与范围

- 工作树：`/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo`。
- 分支：`codex/geographic-relevance-p1`；HEAD 为 `a4997d3969beab3d499a218e7a7aee025fea46d9`。
- 延续已有未提交 P1 成果；没有 reset、切换分支、commit 或推送。
- 对照快照：`checkpoint/resource-tools-before-generation-fix-20261015`（`5b597631bfb785026f422635208c27e2ad92f787`），未用快照覆盖当前文件。
- 本轮没有更改资源生成协议、模型配置、P0 或 P2 执行能力。

## 最终行为

1. 后端名录返回“吉林”待澄清时，卡片直接显示问题及“吉林省”“吉林市（吉林省）”“其他地域”。待澄清时不能继续生成任务配置。
2. 点击候选通过专用后端接口保存同一草案，不创建 AI 对话轮，也不伪造用户消息、模型回答或工具回执。
3. “其他地域”及“修改调查地域”提供名称输入，后端仍用现有名录校验。接受省级地域、地级市和直辖市；区县、街道不自动扩大到所属城市。未知或歧义名称返回可公开的澄清原因、候选，不覆盖原有有效配置。
4. 只允许当前会话最新、未确认草案修改；前后端均阻止与运行中的对话冲突。请求带预期 revision 和 operation_id；重复请求返回当前状态，不重放旧地域。过期 revision 返回冲突并刷新。
5. 草案更新与真实界面操作记录在同一事务提交。失败全部回滚；内部来源保留实际操作 ID 和当时消息序号，公开响应不暴露内部来源字段。
6. 下一轮从存储读取当前草案，将草案 ID、最新 revision、地域、澄清状态和选择策略注入临时系统上下文。历史聊天及旧工具回执不代表当前配置；pending/cleared 不把恢复用的旧地域当有效范围。缓存 Agent 每轮刷新，结束或异常后清除临时上下文。
7. UI 修改前的旧引用不能覆盖更新后的地域；之后用户明确提出的新地域仍可走正常对话修改。未发生 UI 修改的草案保留原有历史引用能力。
8. 地域接口只替换两个地域字段；规则、黑话库、调查目标、平台、召回配置和既有选择策略保持原值。

接口：`POST /api/investigation-workspaces/{session_id}/drafts/{draft_id}/relevance-scope`。

```json
{"expected_revision": 2, "operation_id": "<本次操作的唯一标识>", "target_text": "吉林省"}
```

## 核心文件

| 范围 | 文件 |
| --- | --- |
| 直接保存、名录校验、事务与幂等 | `backend/investigation_creation/scope_selection.py`（新增）、`store.py`、`service.py`、`backend/api/investigation_creation.py` |
| UI 来源和旧引用保护 | `backend/investigation_creation/scope_intent.py` |
| AI 当前状态同步 | `backend/investigation_creation/conversation.py::_turn_context` |
| 统一澄清与编辑区 | `Audit_assistant/src/features/investigation/RelevanceScopeEditor.tsx`（新增）、`TaskSuggestionCard.tsx`、`TaskConfirmationCard.tsx` |
| 最新卡权限、保存后恢复 | `InvestigationCenterArea.tsx`、`InvestigationPage.tsx`、`Audit_assistant/src/services/investigationCreation.ts` |
| 展示及样式 | `confirmationView.ts`、`suggestionPresentation.ts`、`Audit_assistant/src/styles/investigation-workspace.css` |
| 新增后端验证 | `tests/test_geographic_scope_selection.py`、`tests/test_geographic_scope_context.py` |
| 前端验证 | `Audit_assistant/tests/geographicScopeDisplay.spec.ts` |

以上为本轮实施文件；工作树另有原先尚未提交的 P1 文件，不能把整个 `git status` 当成本轮新增改动。

## 独立验证与证据边界

后端成功用例合计 **501**（分组执行，不是全仓测试总数）：

| 组 | 结果 | 主要内容 |
| --- | --- | --- |
| 新增地域直接保存 | 21 passed | 四种正式/临时规则词库组合、资源数据库逻辑 dump 不变、非法输入不写、事务失败回滚、幂等、跨会话、版本/忙碌、旧引用与新请求、重启、公开投影 |
| 新增 AI 上下文 + 既有 turn context | 10 passed | 仅“继续”、默认/多轮/连续模式、缓存 Agent、pending/clear、会话隔离、异常恢复、不伪造历史、不注入整份资源 |
| 地域草案、意图、统一配置 | 125 passed | `test_geographic_draft_configuration.py`、`test_geographic_scope_intent.py`、`test_unified_draft_configuration.py` |
| 名录及资源/规则/词库回归 | 345 passed | 名录3组、resource lifecycle/generation/selections、ruleset proposals/presentation/approval/ref handoff、continuous resource session、investigation rounds、resource save recovery/edit entry |

其中“修改后的地域同步给 AI”的验证使用真实创建服务、SQLite 草案和生产 `execute_turn`，在 Agent 调用边界捕获本轮输入。界面先保存“吉林省”，用户只发“继续”，捕获到的上下文包含同一草案、新 revision 和“吉林省”；无须用户复述地域。另有缓存 Agent 先看到“克拉玛依市”、保存后看到“吉林省”的覆盖。

**本轮没有请求真实 DeepSeek。** 上述测试证明新状态进入模型调用输入，不能据此承诺外部模型每次都会正确执行后续工具。

后端环境：使用主项目现有 `.venv/bin/python`。资源回归首轮 336 passed、9 failed；9 个失败全部是测试环境缺少 `hermes_cli` 导入路径。补齐下列已有源码路径后，该模块 9 passed，没有为此修改业务代码：

```sh
PYTHONPATH=hermes_m0:/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/external/hermes-agent \
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python \
-m pytest -q tests/test_continuous_resource_session.py --tb=short
```

新增后端核心验证可单独运行：

```sh
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python \
-m pytest -q tests/test_geographic_scope_selection.py tests/test_geographic_scope_context.py tests/test_turn_system_context.py
```

前端：地域、创建流程、混合规则 Proposal、Proposal 展示首轮 **95 passed**；补齐已确定地域的修改回归后，新增用例独立 **1 passed**，最终地域全量 **34 passed**（前端合计 96 个不同用例通过）。候选点击、手填、非法区县、同 operation_id 重试、409、忙碌、历史卡只读、双击、刷新、390px 输入均覆盖，断言没有 AI turn 或规则/词库写请求。API 使用隔离 fixtures，不依赖外部模型。额外覆盖已确定“克拉玛依市”后改填“朝阳区”失败、完整 configuration/revision 不变、取消恢复、再改“吉林省”成功且刷新保留。

前端复现（工作目录 `Audit_assistant`，默认 `playwright.config.ts`）：

```sh
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
npm test -- tests/geographicScopeDisplay.spec.ts tests/investigationCreation.spec.tsx tests/mixedProposalPresentation.spec.ts tests/proposalPresentation.spec.ts
```

最终地域全量日志：`direct-selection-evidence/geographic-scope-34-playwright.log`；新增已确定地域编辑日志：`direct-selection-evidence/geographic-resolved-edit-playwright.log`。

`VITE_API_PROXY_TARGET=http://127.0.0.1:8010 npm run build` 通过（包含 TypeScript 检查）。仅有既有大包提示；后端仅既有 `python_multipart` 弃用提示。`git diff --check` 通过。

前端截图：`direct-selection-clarification.png`。已人工检查问题、按钮、其他地域入口和阻断提示。

## 审查与限制

- 独立只读审查后端直接选择、事务、公开 API、来源水位、AI 上下文：0 项可操作问题；审查者另外运行新增后端两组 29 passed。
- 独立只读审查前端 9 个文件：0 项可操作问题；审查者另外运行 TypeScript 检查通过。审查指出的已确定地域修改交互覆盖缺口已补齐并通过。
- 未进行多进程并发压力测试；已覆盖顺序冲突、忙碌和双击/重试。
- 本轮没有启动真实调查、采集、Run/Job；地域选择成功不代表 P2 地域执行能力已开放。
- 没有新增依赖。原始方案中的“按钮发送聊天消息”已由本次明确的直接保存方案替代。
