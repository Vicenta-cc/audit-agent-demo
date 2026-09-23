# 当前多用户版本：启动与环境交接

## 先明确边界

- 应用仓库：`Vicenta-cc/audit-agent-demo`，当前前端 `Audit_assistant`，入口 `/investigation`。
- 运行组件：前端、API、独立调查 worker、MediaCrawler/浏览器、Hermes、ffmpeg、远端 Dolphin，以及两个独立模型供应商配置。
- GitHub push 只更新源码，不会升级本地/云端进程、数据库、密钥或浏览器登录态。
- 不要直接运行旧 `start-stack.sh`：它含历史服务器及重模型启动假设。旧 formal、8027、R2 控制器各有专属环境，不可互换。
- 下列新机器步骤是代码入口与配置交接，不代表已在每种 OS 全新安装验收。当前真实验证环境是 macOS 3398/8398；Linux 浏览器登录及 systemd 需独立验收。

## 为什么之前会反复失败

不是缺一个 Key，而是源码、解释器、进程环境和外部依赖没有始终作为同一个发布单元管理：

1. 多套旧启动入口与新前端混用，甚至进入旧提交。
2. API/worker/问答子进程依赖临时 shell 的 PATH/PYTHONPATH，重启后环境丢失。
3. Dolphin Key 曾放在旧环境 JSON，单拷贝 secrets.env 没带走；health 成功也不能证明鉴权成功。
4. Python/爬虫虚拟环境和 ffmpeg 引用其他工作树；虚拟环境 Python 链接被解析后还可能绕过 venv。
5. 仅验证端口或 HTTP 健康，没有实际抽音频、转写、问答检查。

仓库已有 `.env.example`、Hermes 固定源版本与环境控制器，但没有跨平台依赖完整锁定和通用一键初始化。不能把“有配置文件”当成“已可重现部署”。

## 新机器准备

1. 克隆应用 main，记录完整 `git rev-parse HEAD`，每次升级使用独立干净目录，不修改运行中的源码。
2. 建应用自己的 venv。Hermes 要求 Python >=3.11,<3.14；本次本地使用 Python 3.12。使用绝对 venv Python，不要解析其符号链接。

   ```bash
   python3.12 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt -r requirements-hermes.txt
   .venv/bin/python -m pip check
   ```

   Hermes 声明源提交见 `requirements-hermes.txt`，预期版本 0.20.4。若安装解析失败应解决依赖冲突，不能跳过 Hermes 或借用别的工作树 venv。Python 依赖仍有范围版本，需另保存目标机器的依赖清单；这不是完整锁文件。
3. 独立准备 `Vicenta-cc/media-crawler`，当前配套提交 `efafe3186400b1020955c6acfdc201235b84a2d8`。按该仓库说明安装自己的 venv、浏览器依赖并验证抖音登录；不能只安装应用 requirements。
4. 选定 Node 绝对路径，记录版本；在 `Audit_assistant` 执行 `npm ci`、`npm run typecheck`、`npm run build`。npm 锁文件不能替代 Node 版本清单。
5. 安装 ffmpeg，准备一个本地正常短视频作启动探针。Linux 交互登录另需显示/浏览器系统依赖，参考历史部署资料但不要直接复制旧业务配置。
6. 在仓库外建立 runtime 根目录，包含 data、outputs、hermes、日志与受限权限的配置文件。不同环境绝不共用活动 SQLite、浏览器 profile 或 Hermes 会话目录。

## 一份配置，三个密钥角色

从 `.env.example` 建立自己的 runtime.env；以下路径均替换为实际绝对路径，不照抄本机 `/Users/...`。文件权限设为 600，不能上传 Git。API 和 worker 使用完全相同文件；前端不能获得这些 Key。

```env
XHS_AUDIT_DATA_DIR=/absolute/runtime/data
XHS_AUDIT_OUTPUTS_DIR=/absolute/runtime/outputs
APP_AUTH_DB=/absolute/runtime/data/investigation_creation.sqlite3
HERMES_HOME=/absolute/runtime/hermes
CRAWLER_BROWSER_PROFILE_ROOT=/absolute/runtime/data/crawler_browser_profiles
MEDIACRAWLER_DIR=/absolute/media-crawler
CRAWLER_LOGIN_PYTHON=/absolute/media-crawler/.venv/bin/python
APP_AUTH_MODE=required
# 仅本机 HTTP 使用 false；公网 HTTPS 必须 true。
APP_AUTH_COOKIE_SECURE=false

# 普通对话、内容审核、报告与问答
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_API_KEY=REPLACE_WITH_PRIVATE_AUDIT_KEY
# 规则、关键词、黑话库生成；不是审核 Key
RESOURCE_GENERATION_BASE_URL=https://www.dmxapi.cn/v1
RESOURCE_GENERATION_API_KEY=REPLACE_WITH_PRIVATE_RESOURCE_KEY
# 模型名及视觉专用模型从 .env.example 选择，并验证账号实际可调用。

FFMPEG_PATH=/absolute/bin/ffmpeg
ASR_ENGINE=dolphin
USE_REMOTE_ASR=true
REMOTE_ASR_BASE_URL=http://127.0.0.1:19001
REMOTE_INFERENCE_API_KEY=REPLACE_WITH_PRIVATE_DOLPHIN_KEY
ASR_TRANSLATE_ENABLE_THINKING=false
INVESTIGATION_MAX_POSTS=30
```

Dolphin Key 必须匹配远端 `INFERENCE_API_KEY`，不是 DMX/DashScope Key。新部署直接把它放入自己的私有配置，不应永久依赖旧电脑的 environment.json。`ASR_LANGUAGE`/`DOLPHIN_LANG_SYM` 按语种选择，不能盲用旧维语默认。`INVESTIGATION_MAX_POSTS` 是允许的帖子上限，不会自动把草案或每帖评论数改成目标值；任务确认卡仍须核对 10/30/300 等参数。关闭 ASR 翻译 thinking 也不代表所有阶段都关闭 thinking，须核对各阶段日志。

## Dolphin 连接与真实检查

可直连受保护服务，或使用自己有权限的 SSH 转发：

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 19001:127.0.0.1:9001 USER@YOUR_ASR_SERVER
```

远端端口由服务管理员提供，9001 仅为示例。Coder 转发方法见[ASR 手册](../backend_startup_and_remote_asr.md)。隧道也必须持久托管；启动 API 不会自动建立隧道。

必须在实际 API/worker 环境做以下检查，不能只看端口或 health：

1. ffmpeg 用固定路径从探针视频抽取 3 秒音频。
2. `DemoAudioProcessor.transcribe` 用实际 Key 完成转写；用 `AuditPipeline._validated_authoritative_transcript` 验证，确认 asr_engine/provider 为 dolphin。
3. 普通审核 Key 完成一个小 JSON 请求，单独验证资源生成配置，不能以其中之一成功代替另一组。
4. 检查 Hermes、OpenCV、pydantic_core 及 `backend.hermes_runtime.turn_worker` 可导入，并在前端实际完成一轮问答。

本机固定控制器 `check` 已实现抽取/真实转写、普通模型请求、数据库检查及依赖导入；它只检查资源生成 Key 的配置存在，并不实际调用 DMX。

## 新机器开发启动入口

以下仅本机开发。先在每个后端终端使用干净 shell，并显式加载同一份配置。配置内容须为可信、shell 兼容的 KEY=value，禁止 source 来历不明的文件。

```bash
env -i HOME="$HOME" PATH=/usr/bin:/bin:/usr/sbin:/sbin /bin/bash --noprofile --norc
cd /absolute/audit-agent
set -a
source /absolute/runtime/runtime.env
set +a
export PATH="/absolute/audit-agent/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONPATH="/absolute/audit-agent:/absolute/audit-agent/hermes_m0"
```

仅首次空环境，在上述环境初始化管理员，密码交互输入：

```bash
/absolute/audit-agent/.venv/bin/python scripts/manage_app_users.py create-admin admin
```

API 终端：

```bash
/absolute/audit-agent/.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8398
```

worker 终端先执行相同的环境初始化，再运行：

```bash
/absolute/audit-agent/.venv/bin/python -m backend.investigation_creation.worker --workers 1
```

前端另开干净终端，不加载后端 runtime.env：

```bash
cd /absolute/audit-agent/Audit_assistant
VITE_API_PROXY_TARGET=http://127.0.0.1:8398 /absolute/node/bin/node \
  node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3398 --strictPort
```

打开 `http://127.0.0.1:3398/investigation`，登录应用，再登录自己的采集账号。不能把 Vite 构建时的代理变量当成生产反向代理；正式部署静态产物还须配置同源 `/api` 转发。

开发终端关闭进程会结束。长期运行应由 macOS LaunchAgent 或 Linux systemd 分别托管 API、worker、前端/网关与隧道，固定 WorkingDirectory、绝对 Python、PATH/PYTHONPATH、同一配置来源；不要依靠临时 shell/nohup 偶然存活。

## 已有本机 3398/8398 环境

使用[本机控制器交接](LOCAL-3398-8398-20260923.md)的 `check → start → status`，不要改用上面的手动命令叠加启动。该控制器限定 macOS/3398/8398，并要求已有数据库、探针媒体及登录 Cookie，不是新机器初始化器，也不能用于 Linux。

升级前确认没有活动任务；按确切服务标签停止本环境，提交/选择新 SHA，更新 runtime.json 的源码 pin，再 check/start/status。认证 status 失败时先处理登录，不关闭鉴权。保留原数据与旧代码作回滚，不能用旧 DB 覆盖新数据。

## 发布验收与剩余架构工作

一次上线必须记录：应用/爬虫 SHA、Python/Node/Hermes 版本、依赖清单、前端构建、解释器路径、非敏感配置摘要、进程身份，以及真实 Dolphin 和问答收据。保存 Key 所在位置，不保存 Key 值。

最低验收：登录 → 小任务创建并由 worker 执行 → 图片/视频/评论审核 → 报告 → 报告问答 → 定向重启后再问答。先小样本再跑 30 帖；HTTP 200 不能替代这条验收链。

后续建议收敛为统一配置 schema、各 OS 的无密钥模板、全新环境初始化器、完整依赖锁定及公共 preflight；所有入口执行相同检查，缺关键依赖时启动失败。这些尚未全部实现，本次文档不能被当成已完成通用一键部署的声明。
