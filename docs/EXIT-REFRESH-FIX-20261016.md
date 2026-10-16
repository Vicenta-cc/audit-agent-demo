# 出口记录过期修复与本地验证记录

日期：2026-10-16
状态：本地修复已应用；二维码登录、真实等待超过五分钟、自动复查及单条采集已验证。本提交将修复与验证文档纳入 Audit 发布候选；远端发布状态以 Git 分支指向为准。

## 修复内容

原问题：本地 `APP_AUTH_MODE=disabled` 的新调查任务跳过正常调度入口，先调用动态账号校验；出口观测过期时，可用账号为空，导致任务在自动复查之前失败。

本次修改：

- `backend/investigation_creation/worker.py`：在新建 M3 任务的动态账号校验前，调用现有 `AccountRotationManager.refresh_authorized()`。仅作用于免鉴权、调度准入关闭、启用抖音采集的路径。刷新期间维持 worker 心跳及所有权校验。
- `backend/main.py`：使用现有只读 `_stale_route_candidate()` 判定，将可复查的过期状态标记为 `EXIT_OBSERVATION_STALE`，仍保持 `collection_ready=false`，不在 GET 接口执行网络探测。
- `Audit_assistant/src/features/crawler-accounts/CrawlerAccountsPage.tsx`：分别显示“采集网络待校验，任务启动时自动检查”和“共享出口不可用，采集已暂停”。
- 新运行环境补齐 Camoufox 固定模型 `model-2/2026` 的三个资产文件；使用旧已验证环境中的文件逐项核对当前 SDK 固定 SHA-256 后安装，`is_pinned()` 通过。没有修改模型校验代码或关闭校验。

保护保持开启：账号授权、账号租约、持久化刷新声明、出口执行租约、出口变化检查及风险暂停条件均复用原实现。未修改 TTL，未启用直连降级，未清除账号风险状态。

## 本次发布范围

发布版本保留 `8ebdb25` 的完整累积代码及其后 `a8868b8` 的登录状态换行，再加入本次出口复查修复、测试和本文档。没有退回旧 release 后仅挑选两项修复。

以下四个工作区文件的未提交改动不纳入本次发布：`TaskSuggestionCard.tsx`、`confirmationView.ts`、`investigationPresentation.spec.ts`、`taskSuggestionKeywordEditing.spec.ts`。它们保留在原工作区。本次没有修改或推送 MediaCrawler 源码。

## 代码与运行环境

- Audit 基于提交 `8ebdb25112f76959f6f6b8ea625814c6d433a2dd` 的本地工作区补丁。
- Audit 工作区：`/Users/ext.wanghongtao6/.codex/worktrees/new-media-app-integration/xhs-audit-agent-demo`。
- MediaCrawler：`7bd9d117cd1b0753990ba754c58b8c12de8f4ca5`，本次没有修改 MediaCrawler 业务源码。
- 运行资产安装位置：`/Users/ext.wanghongtao6/Documents/Codex/projects/MediaCrawler-camoufox-runtime/.venv`。
- 独立验证页：<http://127.0.0.1:3501/crawler-accounts>；验证 API：`127.0.0.1:8501`。
- 验证使用新账号记录、独立数据库和浏览器 profile；没有覆盖原本地账号数据。请求限流数据库和资源锁仍复用现有配置。
- 原本地页面 <http://127.0.0.1:3499/crawler-accounts> 的 API 和 worker 已在确认没有活动调查、采集或登录后重新启动，加载本地修复。原账号 API 已返回新的过期分类。
- 本次没有改变整机 TUN、代理节点或原服务的网络配置。

## 真实验证结果

| 项目 | 结果 |
| --- | --- |
| 新账号二维码 | 正常出现，用户实际扫码并确认 |
| 登录结果 | 服务端账号 active，前端显示登录成功 |
| 开始等待时刻 | 2026-10-16T19:18:00 +08:00 |
| 启动过期验证时刻 | 2026-10-16T19:23:31 +08:00 |
| 真实闲置时长 | 331.59 秒，约 5 分 32 秒 |
| TTL 配置 | 300 秒，没有缩短 |
| 验证前条件 | 代理及共享出口观测均已自然过期；账号不在可用列表中 |
| 前端过期状态 | “采集网络待校验，任务启动时自动检查” |
| worker 自动复查 | 通过，观测时间更新，出口 IP 与原记录一致 |
| 真实采集 | 关键词“风景”，成功返回 1 条并写入 JSONL |
| 重新扫码 | 复查和采集过程中不需要 |
| 安全开关 | `CRAWLER_SAFETY_ENABLED=true`，`CRAWLER_DIRECT_DEGRADE_ENABLED=false` |
| 完成时刻 | 2026-10-16T19:24:52 +08:00 |

采集记录的公开内容标题：秋天是大自然打翻的调色盘，金黄橙红落满人间。
内容 ID：`7693069731972813818`
JSONL SHA-256：`23ecc519dd85b27ca3315af1793c0e6bffe2f8a8e39ddc978ad1abd2ed310246`。

验证脚本使用真实 worker 配置校验入口、真实账号存储、真实线路探测及真实 MediaCrawler 搜索。没有伪造检查时间、网络结果或账号可用性。脚本为隔离测试使用了复制的调查运行记录；模型供应商校验被明确替换为测试回调，并在 worker 检查后直接调用实际采集适配器。因此该结果证明登录、过期复查和采集链路，不代表完整前端调查、模型分析和报告生成已端到端验收。

## 回归与复核

- 新增 worker 回归：6 项通过，涵盖成功刷新、刷新失败仍拦截、共享暂停仍拦截、禁用账号仍拦截，以及正常鉴权路径不额外刷新。
- 账号安全 API：13 项通过，涵盖过期与真实保护的区分，以及列表查询无网络探测、无写入。
- 新增前端状态用例通过，TypeScript 检查通过，`git diff --check` 通过。
- 独立代码复核未发现本次补丁引入的实质性缺陷或保护放宽。
- M3 与安全测试组合：142 通过、11 失败；相同 11 项在原 worker 基线下也失败，主要为既有测试账号 fixture 未满足当前身份资格。
- 扩展安全测试：110 通过、9 失败；干净基线复现相同 9 项。原因是默认 MediaCrawler 路径不存在，显式指定正确路径后，这 9 项及一个相关 ACL 用例共 10 项全部通过。
- 正确路径下的整组重跑在多进程同步中停滞并被终止，不能宣称整组回归全通过。
- 前端相关文件：8 通过、1 失败；该失败在干净基线复现，原因是既有测试未 mock 新的登录 claim 接口。

## 边界与部署提示

本次修改针对新任务的本地免鉴权入口。正常鉴权调度路径、历史任务崩溃恢复分类路径保持原状；后者的过期预校验仍需单独覆盖，不能将本次验收扩大为所有恢复场景已修复。

未测试评论、媒体下载、多账号并发、其他平台及 Linux 部署。远端必须使用正式鉴权及对应平台的完整浏览器资产，不能照搬本地免鉴权配置或 macOS 浏览器路径。历史 release tag 保留原指向。本次发布沿用完整集成历史，远端部署仍需执行目标环境验收。

## 本地证据

证据目录：`/Users/ext.wanghongtao6/.codex/runtime/exit-refresh-verification-20261016`，含：

- `source-manifest.json`：六个修改文件的哈希及基线提交。
- `exit-refresh-fix.patch`：仅包含本次六个源码和测试文件的补丁。
- `result.json`、`output-verification.json`：实际时长、复查结果、采集数量及产物校验。
- `verify_after_idle.py`：本次隔离验证脚本。
- `login-success.png`、`idle-stale.png`、`after-idle-success.png`：登录、自然过期、复查后可用的页面证据。
- `main-service-activation.json`：原本地 API/worker 加载修复的记录。
- `safety-regression.log`、`safety-baseline-8ebdb251.log`、`safety-failing-cases-explicit-media-path.log`：扩展测试及基线对照。

证据中的数据库、浏览器 profile 和登录材料仅留在本地验证目录，不应提交到仓库。
