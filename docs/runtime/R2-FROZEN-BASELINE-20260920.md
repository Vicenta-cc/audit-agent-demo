# R2 基线与环境交接（2026-09-20）

本文区分应用代码、控制器版本与实际运行环境。2026-09-20 核对时，腾讯云 R2 运行应用 `509d922` / Crawler `efafe318`；本地同端口服务属于不同历史环境。本文与启动文件归档没有修改线上运行状态。

## 当前版本与历史版本

| 用途 | 应用提交 | Crawler 提交 | 环境说明 |
| --- | --- | --- | --- |
| 当前正式 R2 | `509d922` | `efafe318` | 流式输出、云端融合修复、嵌入式交互登录 |
| 上一版流式发布 | `f58232e` | `efafe318` | 已在当前代码历史中 |
| 更早的统一报告发布 | `b9fc78c` | `efafe318` | 历史发布，不是当前运行代码 |
| 本地旧正式冻结 | `2134c7b` | 由原 runtime 固定 | 本地 3198/8198；保留原配置与数据 |
| 本地 R2 验收副本 | `71b29b8` | `efafe318` | 本地 3199/8199；不是当前正式版 |
| 旧 8027 验收快照 | `bf5117a` | `6d2c85b` | 前端 frontend-v2；核对时未启动 |
| 旧 8027 控制器 | `a925969` | 固定验收目标仍为 `6d2c85b` | 控制器提交不等于应用验收快照 |

`78bae84` 是旧 8027 功能历史的一部分。`cd62a96` 所在 demo 开发工作树以及各目录的未提交改动，不属于本次冻结发布。历史版本上传用于追溯，不表示适合覆盖当前服务。

## 本地源目录

- 当前应用：`/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-r2-interactive-login-20260918`；冻结分支 `codex/r2-interactive-login-20260918`，核对时干净。
- 当前爬虫仓库：`/Users/ext.wanghongtao6/Documents/Codex/projects/MediaCrawler-douyin-v2-pagination-dedupe-20260914`；冻结分支 `codex/douyin-v2-pagination-dedupe-20260914`。目录有未跟踪实验文件，发布必须使用精确提交的干净副本。
- 本地旧正式配置：`/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-formal-20260911/config.json`。
- 旧 8027 配置：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/collection-reliability-live-20260915/runtime.json`。
- 本地旧 R2 验收：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/douyin-ops-controls-20260916/runtime.json`。

这些路径用于识别原环境，不是其他机器的默认安装路径。

## 当前云端环境

| 项目 | 路径 / 值 |
| --- | --- |
| runtime | `/mnt/datadisk0/xhs-audit-agent-r2-abc/runtime` |
| 应用 | `/mnt/datadisk0/xhs-audit-agent-r2-abc/releases/r2-interactive-20260918/application` |
| Crawler | `/mnt/datadisk0/xhs-audit-agent-r2-abc/releases/r2-abc-20260916/crawler` |
| 前端 | 应用目录下 `Audit_assistant` |
| 前后端端口 | `127.0.0.1:3198` / `127.0.0.1:8198` |
| 应用 Python | `runtime/venv-app/bin/python`，3.11.6 |
| 爬虫 Python | `runtime/venv-crawler/bin/python`，3.11.6 |
| Node | `/usr/bin/node`，v24.15.0 |
| 业务数据 / 输出 | `runtime/data` / `runtime/data/outputs` |
| 浏览器 Profile | `runtime/browser-profiles` |
| 主服务 | `xhs-audit-r2-abc` |
| 相关服务 | `xhs-audit-r2-edge-relay`、`xhs-audit-r2-coder-asr` |

控制器固定路径、提交、入口指纹、Crawler 指纹和前端构建摘要，启动时清理继承环境，核验 API/前端身份并管理本环境 PID。它不是完全密封的依赖镜像。

完整环境还依赖私有 `environment.json`、`secrets.env`、数据加密密钥、数据库、Hermes 配置/凭据及安装环境；这些不随 Git 推送。应用解释器须能导入 `hermes_cli` 0.20.4。保留原来的环境恢复/部署流程，不要仅凭 requirements.txt 假定能完整复现。

## 登录与验证依据

9 月 18 日发布支持在账号页内嵌云端登录画面。二维码、短信输入及持久化 Profile 保存完成后，独立无头浏览器验证通过。原生“打开客户端”提示不可见时，可点击“取消浏览器提示”恢复网页点击。

发布时记录：后端专项 30 passed / 4 skipped；前端专项 13 passed；类型检查和构建通过；真实扫码短信登录后的无头搜索返回 15 条。以上是发布历史验证，本次文档同步没有重复真实采集。

旧 8027 的 45 passed / 71 passed 等收据属于 9 月 16 日旧快照，不用于证明当前正式版本。

## 后续 release

1. 从应用 `509d922` 创建干净开发分支；Crawler 以 `efafe318` 为配套起点。
2. 在独立 staging 准备数据副本、独立账号/Profile、解释器与空闲端口。当前云端控制器写死 3198/8198，不能原样用于并行 staging。
3. 构建 `Audit_assistant`，完成变更相关测试与运行身份、登录、任务、报告、流式链路验证。
4. 切换前对最新正式数据、Profile、密钥、运行配置及构建收据做一致性备份，确认任务与登录会话可安全停止。
5. 更新应用路径、必要的 runtime-support、提交标签、入口指纹、构建收据，再通过既有服务控制流程切换。仅更改应用目录不足以保证环境一致。
6. 验证服务和公网构建版本、API/worker/前端身份、原有数据与账号保留情况。
7. 回退优先退代码及配置；如包含数据库迁移，另行设计兼容与回退，不能直接恢复旧库覆盖新数据。

9 月 18 日切换前快照：`runtime/archives/r2-interactive-before-20260918-193214`。该旧快照只用于追溯，下次发布须重新备份。

本地完整操作交接：`/Users/ext.wanghongtao6/Documents/Codex/deployment-preparation/r2-interactive-release-20260918/RELEASE-HANDOFF-20260918.md`。仓库内说明仅包含版本、操作边界与启动文件，不上传业务收据中的账号明细和 Profile。
