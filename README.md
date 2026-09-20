# Audit Agent — R2 冻结基线

当前发布功能包括抖音采集、调查会话与报告、流式输出、账号持久化 Profile，以及嵌入式交互登录（扫码、短信验证、取消浏览器原生提示）。当前前端是 **`Audit_assistant`**。

## 版本入口

| 内容 | 冻结版本 / 分支 |
| --- | --- |
| 应用运行代码 | `509d922af4d23dec9d488e7f1e3b95042b0c4bf1` |
| 应用冻结分支 | `codex/r2-interactive-login-20260918` |
| 应用冻结标签 | `douyin-r2-interactive-20260918-v3` |
| 配套 MediaCrawler | `efafe3186400b1020955c6acfdc201235b84a2d8` |
| 爬虫冻结分支 | `codex/douyin-v2-pagination-dedupe-20260914` |
| 爬虫冻结标签 | `douyin-pagination-fix-20260916` |
| 最新代码与启动说明入口 | `main` |
| 文档归档分支 | `codex/r2-frozen-handoff-20260920` |

[应用代码](https://github.com/Vicenta-cc/audit-agent-demo/tree/codex/r2-interactive-login-20260918) · [配套爬虫](https://github.com/Vicenta-cc/media-crawler/tree/codex/douyin-v2-pagination-dedupe-20260914)

`main` 已同步上述冻结代码与 README、启动文件归档，并保留原 main 的提交历史。业务源码仍对应冻结版本；线上运行提交没有因此改变。精确复现请使用冻结标签或完整提交号。

- [完整基线、环境与更新说明](docs/runtime/R2-FROZEN-BASELINE-20260920.md)
- [云端启动文件归档与使用边界](deploy/r2-frozen-20260918/README.md)
- [机器可读版本清单](docs/runtime/r2-frozen-baselines-20260920.json)

## 获取精确代码

以下命令在新目录执行，得到两个干净的冻结副本：

```bash
git clone --branch codex/r2-interactive-login-20260918 https://github.com/Vicenta-cc/audit-agent-demo.git audit-agent-r2
cd audit-agent-r2
git switch --detach 509d922af4d23dec9d488e7f1e3b95042b0c4bf1
cd ..
git clone --branch codex/douyin-v2-pagination-dedupe-20260914 https://github.com/Vicenta-cc/media-crawler.git media-crawler-r2
cd media-crawler-r2
git switch --detach efafe3186400b1020955c6acfdc201235b84a2d8
```

直接克隆仓库默认 main 即可查看最新版说明与启动归档；生产运行副本仍按完整冻结提交检出，避免提交校验与构建收据不一致。

## 启动环境

当前正式部署是 Linux 上独立的 R2 runtime，由 systemd 管理。应用与爬虫使用各自 Python 3.11.6 虚拟环境，Node v24.15.0；CloakBrowser 0.5.10，新登录 Profile 的 Chromium 版本为 145.0.7632.109.2。交互登录依赖 Xvfb、libX11/libXtst 和中文字体。版本值是已有服务器核验结果，不等于仓库已包含全部依赖。

已有正式服务器检查命令：

```bash
systemctl status xhs-audit-r2-abc xhs-audit-r2-edge-relay xhs-audit-r2-coder-asr --no-pager
curl --fail http://127.0.0.1:8198/api/acceptance-runtime
```

正式启动、停止、检查与迁移步骤见[启动归档说明](deploy/r2-frozen-20260918/README.md)。克隆仓库不会自动获得数据库、Profile、认证密钥、Hermes 环境、模型服务配置或已安装依赖，因此不是一条命令即可复现的容器镜像。

本地开发需要单独配置空闲端口、数据目录、应用 Python、爬虫 Python 和 Node。不要从本分支直接运行旧 `scripts/formal-runtime.sh` 或 `scripts/douyin-acceptance-runtime.sh`：它们分别属于旧冻结环境与旧 8027 验收环境。Linux 交互登录需要独立验证，不能将云端配置直接套到 macOS。

前端依赖安装与构建入口（在独立开发副本、已选择正确 Node 后）：

```bash
cd Audit_assistant
npm ci
npm run typecheck
npm run build
```

这只验证前端构建，不会初始化或启动完整业务系统。

## 更新约定

后续功能从 `509d922` 派生，保留已有云端融合历史。新 release 在独立目录验收，通过后切换代码与配套启动配置，沿用正式数据、账号、密钥、Profile 和 outputs。回退默认退代码及配置，不能用旧数据库覆盖上线后新增业务数据。

当前冻结版不应被视为已经完成应用用户隔离、七天账号有效期和每日三次任务额度；这些需求需要后续单独审阅与实现。

`DEPLOY_SERVER.md` 及较早文档中的 Windows 路径、8090 入口和旧前端说明属于历史开发资料，当前 R2 部署以本文链接的版本与环境清单为准。
