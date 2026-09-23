# Audit Agent — 多用户候选版本

当前 `main` 在多用户候选提交 **`5873ea7570397f4b6f0c07dcec214e8541d7b2e0`** 基础上，已继续合入多用户验收修复、可配置 1–30 帖采集与统一报告问答，以及资源生成模型分流；原 main 历史及 R2 启动文件归档均保留。当前前端是 **`Audit_assistant`**，配套 MediaCrawler 为 `efafe3186400b1020955c6acfdc201235b84a2d8`。

本版包含应用账号登录和数据隔离、管理员用户管理、普通账号首次登录起七天有效、私有采集账号、每日三次任务额度、多用户调度与账号资源锁、任务结束流程，以及报告正文展示评论账号 Top 5、抽屉查看全部账号。保留 R2 的流式输出和嵌入式交互登录。

**版本边界：** `5873ea7` 是初始多用户候选快照；当前 main 包含其后续调查验收与报告能力提交。管理员无限日额度、最低 8 位密码属于另一条后续提交 `3fe7ebe`，当前 main 仍未包含；管理员仍受每日三次限制，创建/重置密码最低 12 位。推送 main 本身不会自动部署或回退任何运行环境。

## 获取与配置多用户版本

**新部署请先读：[当前版本启动交接](docs/runtime/CURRENT-STARTUP.md)。** 当前业务入口是 `Audit_assistant` 的 `/investigation`，不是旧 `/saas`。文档区分新机器初始化、本机固定控制器、Linux 托管和升级；API 能启动不代表 worker、报告问答或 Dolphin 已通过验收。

```bash
git clone https://github.com/Vicenta-cc/audit-agent-demo.git audit-agent-multi-user
cd audit-agent-multi-user
git switch main
git rev-parse HEAD
# 将核验过的完整 SHA 固定在部署清单；不要为启动当前版切回历史候选。
```

应用与 worker 必须使用同一套明确配置的数据路径和认证数据库。按 `.env.example` 配置 `APP_AUTH_MODE=required`、`APP_AUTH_DB`、Cookie、CORS、任务容量以及采集环境，不能直接把旧 R2 的免登录配置用于公开多用户环境。

### 模型阶段与密钥隔离

新环境必须同时配置两组用途不同的模型变量，不能把一枚 Key 填到另一组变量中：

| 阶段 | 环境变量 | 用途 |
| --- | --- | --- |
| 资源生成 | `RESOURCE_GENERATION_API_KEY`、`RESOURCE_GENERATION_BASE_URL`、`RESOURCE_GENERATION_MODEL` | 仅在用户明确要求新生成审核规则、黑话库或关键词时使用；当前接入 DMX。 |
| 常规运行 | `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`QWEN_TEXT_MODEL` | 调查对话的其他回合、内容审核、报告生成和报告问答；当前使用 DashScope/Qwen。 |
| 视频语音 | `FFMPEG_PATH`、`USE_REMOTE_ASR`、`REMOTE_ASR_BASE_URL`、`REMOTE_INFERENCE_API_KEY`、`ASR_ENGINE` | 本地 ffmpeg 抽音频，再交给远端 Dolphin；不是 DMX 或 DashScope 的一部分。 |

资源生成阶段缺少自己的 Key 时会明确失败，不会回退并消耗常规 Key。真实 Key 只放在部署环境的 `.env` 或受控 runtime 的 `secrets.env`，禁止提交到 Git。启动前应运行环境检查；检查只报告两组配置是否齐全，不输出 Key。新任务帖子上限只通过 `INVESTIGATION_MAX_POSTS` 调整，当前建议值为 `30`，以后恢复为 `10` 时只改这个变量。

包含视频审核的环境不能只配置两组模型 Key。Dolphin 的最小本地配置如下；`REMOTE_INFERENCE_API_KEY` 必须和远端服务的 `INFERENCE_API_KEY` 一致：

```env
FFMPEG_PATH=/absolute/path/to/ffmpeg
USE_REMOTE_ASR=true
REMOTE_ASR_BASE_URL=http://127.0.0.1:19001
REMOTE_INFERENCE_API_KEY=<runtime secret only>
ASR_ENGINE=dolphin
```

`19001` 可以是 SSH 或 Coder 转发到远端 Dolphin 的本地端口。启动含视频审核的 API 和 worker 前，必须在同一个环境中执行：

```bash
CHECK_REMOTE_ASR=true ./scripts/start-backend.sh
```

该脚本只做 health 检查，不能证明转写接口的鉴权或实际 Dolphin 推理成功；还必须完成短音频真实转写，见[当前版本启动交接](docs/runtime/CURRENT-STARTUP.md)。API 与 worker 必须读取同一份 ASR 和 ffmpeg 配置，不能假设新进程会继承另一套旧服务的环境变量。直接连接、SSH 隧道和 Coder 转发方式见[后端启动与远程 ASR 接入手册](docs/backend_startup_and_remote_asr.md)。

在配置好的独立环境中，通过以下命令初始化首个管理员（交互输入密码，仓库没有预设正式密码）：

```bash
python scripts/manage_app_users.py create-admin admin
```

已有用户数据库的更新不应重新初始化或覆盖数据库。下面的 R2 控制器与端口说明仅作为历史归档；多用户发布需要匹配当前代码、构建收据、数据目录和 systemd 配置。

- [每日任务准入与额度](docs/multi-user-phase3-task-admission.md)
- [并发调度与资源锁](docs/multi-user-phase4-concurrency.md)
- [计次及任务结束流程](docs/multi-user-task-lifecycle-v2.md)

## 历史 R2 冻结基线

以下记录用于旧版本复现，不代表当前 main 的业务版本。

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

[应用代码](https://github.com/Vicenta-cc/audit-agent-demo/tree/codex/r2-interactive-login-20260918) · [配套爬虫](https://github.com/Vicenta-cc/media-crawler/tree/codex/douyin-v2-pagination-dedupe-20260914)

旧 R2 的 README、启动文件归档和历史提交继续保留。精确复现旧版请使用对应冻结标签或完整提交号。

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

历史 R2 部署是 Linux 上独立的 R2 runtime，由 systemd 管理。应用与爬虫使用各自 Python 3.11.6 虚拟环境，Node v24.15.0；CloakBrowser 0.5.10，新登录 Profile 的 Chromium 版本为 145.0.7632.109.2。交互登录依赖 Xvfb、libX11/libXtst 和中文字体。版本值是已有服务器核验结果，不等于仓库已包含全部依赖。

历史 R2 服务器检查命令：

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
VITE_API_PROXY_TARGET=http://127.0.0.1:8010 npm run build
```

这只验证前端构建，不会初始化或启动完整业务系统。

## 更新约定

多用户候选从 `509d922` 派生到 `5873ea7`，保留已有云端融合历史。新 release 在独立目录验收，通过后切换代码与配套启动配置，沿用正式数据、账号、密钥、Profile 和 outputs。回退默认退代码及配置，不能用旧数据库覆盖上线后新增业务数据。

历史 `509d922` 没有应用用户隔离、七天账号有效期和每日三次任务额度；当前 main 合入的 `5873ea7` 已包含这些功能。

`DEPLOY_SERVER.md` 及较早文档中的 Windows 路径、8090 入口和旧前端说明属于历史开发资料，当前 R2 部署以本文链接的版本与环境清单为准。
