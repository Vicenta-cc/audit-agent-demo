# 抖音 CloakBrowser 采集线收敛与支线清理清单

更新时间：2026-09-16

## 目标

把 8027 已验收的抖音 CloakBrowser 采集能力整理成可审查、可测试、可提交的干净变更，同时不修改正式冻结基线、正式运行环境、Report A/B/C、Report D 支线、小红书或快手代码。

本文件是提交范围和支线清理的白名单。当前脏工作区不能整体提交，也不能通过整分支合并替代逐项审阅。

## 权威基线与保留对象

### 应用仓库

- 正式冻结基线：`2134c7b53c572b9981a0cbd9a2a281ed7ca6eb3a`
- 分支：`codex/unified-frozen-20260913`
- 标签：`unified-baseline-20260913`
- 冻结 worktree：`xhs-audit-agent-unified-frozen-20260913`
- 抖音集成提交顶点：`78a8d38effd5540da9fcdcc1d56af144f6618b7a`
- 干净集成 worktree：`xhs-audit-agent-v2-integration`
- 当前验收开发 worktree：`xhs-audit-agent-unified-scheduler-20260915`

`78a8d38` 是 `2134c7b` 的线性后代。最终干净提交应以 `78a8d38` 为起点，只迁入本文件列出的最终变更。

### MediaCrawler 仓库

- 登录存储冻结提交：`d9aa0c10acdd6de8701303afe34a1c0f9cf2d42e`
- 当前抖音集成提交顶点：`79aa4ca69b423cca3d73e99096ffff3f54c0a864`
- 当前验收开发 worktree：`MediaCrawler-douyin-v2-pagination-dedupe-20260914`

最终干净提交应以 `79aa4ca` 为起点，只迁入抖音及其必要共享基础设施。

### 明确保持不变

- 正式 runtime：`xhs-audit-agent-formal-20260911`
- 正式前端 3198、后端 8198 和正式数据库
- 任务 `20c597f1f6b3`、K2 配置及原审核结果
- Report A/B/C
- Report D 分支、草稿、检查点和后续发布流程
- 原 8017 环境
- 8027 验收数据在最终提交完成前保留，仅作为证据，不作为 Git 源码来源

## 最终必须保留的功能

### 应用侧

1. 登录与采集统一使用后台无头 CloakBrowser。
2. 每个账号使用独立持久 profile、固定指纹种子和互斥锁。
3. 登录态加密保存并可在服务重启后复用。
4. `verify` 与真正登录失效分离；`verify` 只进入冷却，不清除认证状态。
5. 存在可用备用账号时只进行一次受控切换；备用账号再次 `verify` 后停止本轮，避免循环换号。
6. 平台共享 429、网络故障和媒体故障不触发账号失效或换号。
7. 暂停/继续保持同一任务 ID、检查点和已采集数量，不超过 `max_notes`。
8. 页面展示的状态、执行账号、冷却原因和已抓取主内容数量与数据库一致。
9. 采集子进程和 CloakBrowser 进程在完成、暂停或失败后被清理。

### MediaCrawler 侧

1. 搜索与博主分页去重、精确配额和连续无新增退出。
2. 历史完整内容跳过；详情、媒体或原文件不完整时重新采集。
3. 详情、图片、视频失败写入明确状态，不能显示为完整成功。
4. 评论严格遵守每帖上限，到达上限后停止请求。
5. API、媒体和短链接请求共用持久调度预算；429 写共享冷却。
6. 帖子处理频率、请求频率和媒体频率不重复叠加。
7. 评论 JSONL 以 `comment_id` 跨进程幂等写入。
8. 平台 `verify` 作为独立控制错误向应用层传播。

## 生产代码白名单

### 应用仓库

只审阅并迁入以下生产代码：

```text
backend/audit_agent/account_rotation.py
backend/audit_agent/config.py
backend/audit_agent/crawler_account_store.py
backend/audit_agent/crawler_adapter.py
backend/audit_agent/crawler_browser.py
backend/audit_agent/crawler_login_manager.py
backend/audit_agent/pipeline.py
backend/audit_agent/request_scheduler.py
backend/main.py
frontend-v2/src/features/crawler-accounts/CrawlerAccountLoginDialog.tsx
frontend-v2/src/features/crawler-accounts/CrawlerAccountsPage.tsx
frontend-v2/src/features/monitor-tasks/TaskActionsMenu.tsx
frontend-v2/src/features/monitor-tasks/TaskDetailDrawer.tsx
frontend-v2/src/features/monitor-tasks/TaskRow.tsx
frontend-v2/src/services/crawlerAccounts.ts
frontend-v2/src/services/jobs.ts
frontend-v2/src/styles/crawler-accounts.css
frontend-v2/src/types/crawlerAccounts.ts
frontend-v2/src/types/jobs.ts
scripts/crawler_account_login.py
```

### MediaCrawler 仓库

只审阅并迁入以下生产代码：

```text
cmd_arg/arg.py
config/base_config.py
config/dy_config.py
media_platform/douyin/client.py
media_platform/douyin/core.py
media_platform/douyin/exception.py
store/douyin/_store_impl.py
tools/async_file_writer.py
tools/collection_status.py
tools/persistent_request_gate.py
```

8027 与源仓库的 `media_platform/douyin/core.py` 当前只有注释差异，没有运行逻辑差异。最终以经过审阅的源文件为准，不能反向复制整个 8027 目录。

## 必须保留的自动测试

### 应用仓库

```text
tests/conftest.py
tests/test_crawl_resume_control.py
tests/test_crawler_account_store.py
tests/test_crawler_adapter_execution_settings.py
tests/test_crawler_browser_integration.py
tests/test_crawler_collection_failures.py
tests/test_crawler_login_manager.py
tests/test_job_request_validation.py
tests/test_pipeline_verify_rotation.py
tests/test_shared_request_execution.py
```

这些测试应覆盖后台浏览器、账号 profile、登录态恢复、暂停/继续、失败契约、共享调度、账号冷却、一次换号和双账号连续 `verify`。

### MediaCrawler 仓库

```text
tests/conftest.py
tests/test_api_limits.py
tests/test_douyin_media_download.py
tests/test_douyin_pagination_dedupe.py
tests/test_douyin_request_scheduling.py
tests/test_persistent_request_gate.py
```

当前 `tests/test_collection_reliability.py` 同时包含抖音、小红书和快手用例，不能原样进入抖音限定提交。应把其中抖音相关用例迁入新的 `tests/test_douyin_collection_reliability.py`，然后仅提交该抖音测试文件。

最终测试记录必须写明：

- 运行命令和 Python/Node 环境；
- 通过、失败、跳过数量；
- 是否包含真实网络；
- 真实未触发的负路径不能写成实测通过；
- 100 条任务停在 12 条且双账号 `verify`，不能改写为 100 条通过。

2026-09-16 提交前门禁结果：应用主流程 `45 passed`；应用浏览器集成 `4 passed, 3 skipped`；MediaCrawler 抖音测试 `71 passed`；`frontend-v2` 生产构建通过。跳过项和最初的环境执行错误已在最终验收报告中单独说明。

## 必须保留的文档

最终只需要三份权威文档：

1. `docs/douyin-only-acceptance-scope-20260915.md`：范围和完成标准。
2. `docs/douyin-live-acceptance-report-20260916.md`：真实任务、测试结果、失败项和剩余风险。
3. 本文件：基线血缘、提交白名单和支线清理规则。

以下文档属于阶段性设计或诊断记录，不直接作为最终权威结论：

```text
docs/douyin-account-browser-integration-20260915.md
docs/unified-request-scheduler-20260915.md
MediaCrawler/docs/douyin-cloak-account-failover-20260915.md
MediaCrawler/docs/douyin-cloak-stress-20260915.md
MediaCrawler/docs/douyin-cloakbrowser-check-20260915.md
MediaCrawler/docs/douyin-pagination-check-20260915.md
MediaCrawler/docs/douyin-session-pair-check-20260915.md
```

如其中仍有最终实现所需的设计说明，应合并进本文件或最终验收报告；不要把相互冲突、已经过时的阶段性结论全部提交。

## 明确不进入最终提交

### 跨平台代码

```text
config/xhs_config.py
media_platform/xhs/core.py
tests/test_xhs_pagination_dedupe.py
```

以及任何快手、小红书生产代码或仅用于它们的测试。共享模块若包含跨平台改动，必须按代码块拆分，仅保留抖音所需部分。

### 临时诊断工具

以下内容默认不进入产品提交：

```text
scripts/douyin_auth_restore_repro.py
scripts/douyin_cloak_stress.py
scripts/douyin_live_session_one_page.py
scripts/douyin_pagination_probe.py
scripts/douyin_session_pair_probe.py
tests/test_douyin_cloak_stress.py
tests/test_douyin_live_session_one_page.py
tests/test_douyin_probe_manual_resume.py
tests/test_douyin_probe_response_format.py
```

它们用于一次性实网诊断、可见浏览器实验或响应格式分析。若后续决定长期维护，应放入独立的诊断工具提交，不能与产品修复混在一起。

### 运行时和敏感数据

不得提交：

- `.xhs-audit-crawler.lock`；
- SQLite 数据库、WAL/SHM、请求调度状态；
- Cookie、localStorage、认证密文、二维码和账号 profile；
- `outputs/`、媒体文件、截图、原始响应、stdout/stderr 和运行日志；
- 8027 整体副本；
- 任何 key、endpoint 私密配置或正式数据库副本。

## 支线保留与清理规则

### 永久保留，不属于本轮清理范围

- `codex/unified-frozen-20260913` 及其 worktree；
- `codex/report-d-minimal-20260914` 及其 worktree；
- 正式 runtime、Report A/B/C/D 和所有现有正式数据；
- 与本轮抖音验收无关的 M3、报告、资源库和历史审核 worktree。

### 暂时保留，完成干净提交后再处理

- `xhs-audit-agent-v2-integration`：干净的应用起点；
- `xhs-audit-agent-unified-scheduler-20260915`：当前最终补丁来源；
- `MediaCrawler-douyin-v2-pagination-dedupe-20260914`：当前爬虫补丁来源；
- 8027 隔离环境：最终干净候选复验前保留。

### 待审阅后可清理的抖音支线

- `xhs-audit-agent-cloak-accounts-20260915`：干净，但分支不是 `2134c7b` 的后代；最终能力迁入干净主线后可删除 worktree，再决定是否保留分支标签。
- `xhs-audit-agent-headless-login-20260915`：仍有 4 个未提交文件；必须先比较并确认其修复已进入最终主线，不能直接删除。
- `MediaCrawler-unified-scheduler-20260915`：仍有 19 个未提交文件，并混有小红书、快手改动；默认不合并，审阅确认无唯一抖音修复后再删除。
- `MediaCrawler-cdn-reliability`：其提交已经是 `79aa4ca` 的祖先；最终分支稳定后可删除多余 worktree，提交历史仍保留。

清理顺序必须是：建立干净候选 → 迁入白名单 → 测试 → 与 8027 行为复核 → 提交 → 备份必要证据 → 删除 worktree → 最后再决定是否删除分支。禁止用 `git reset --hard`、`git checkout --` 或强制删除处理脏 worktree。

## 最终提交建议

应用和 MediaCrawler 是两个仓库，应分别提交：

1. MediaCrawler：抖音分页、完整性、共享限速和幂等恢复。
2. 应用：CloakBrowser 账号运行、暂停恢复、账号冷却/切换和页面状态。
3. 应用文档：范围、真实验收报告和本收敛清单。

每个提交都必须来自干净起点、显式暂存文件，不使用 `git add -A`，不从 8027 目录提交，也不直接合并旁路实验分支。
