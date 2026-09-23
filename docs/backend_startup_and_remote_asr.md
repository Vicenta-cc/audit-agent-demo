# 后端启动与远程 ASR 接入手册

这是早期单后端启动及 ASR 连接参考，不是当前多用户系统的完整启动指南。当前前端为 `Audit_assistant`，入口 `/investigation`，另需独立 worker 和 Hermes。新部署优先阅读[当前版本启动交接](runtime/CURRENT-STARTUP.md)；下文 `/saas` 属于旧入口。

## 1. 运行结构

本地电脑负责：

- 跑 Web 后端 `backend.main`
- 展示 SaaS 页面
- 调用 MediaCrawler 抓取内容
- 下载图片、视频，抽取视频音频
- 保存任务、产出和证据文件到 `outputs/`

服务器负责可选的重模型能力：

- Dolphin ASR：`POST /api/inference/transcribe`
- MMS ASR 复核：`POST /api/inference/mms-transcribe`
- HY-MT 翻译：`POST /api/inference/translate`
- VLM/LLM/OCR：同一个 `backend.inference_server` 也支持这些接口

本地后端和服务器 ASR 的交互方式是：本地先用 ffmpeg 从视频抽出音频，然后把音频作为 `multipart/form-data` 的 `audio` 文件上传到远端 ASR 服务。远端返回 `text`、`segments` 等 JSON，本地再把原始结果保存到任务目录。

## 2. 第一次准备

需要先安装：

- Python 3.10 或更高版本
- Git
- ffmpeg，用于视频抽音频
- 可访问服务器的 SSH key，如果 ASR 服务只能通过 SSH 隧道访问

拉代码并准备环境：

```bash
git clone <repo-url>
cd xhs-audit-agent-demo
cp .env.example .env
```

编辑 `.env`，至少填好：

```env
DASHSCOPE_API_KEY=你的百炼或 DashScope key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_TEXT_MODEL=qwen3.6-plus
QWEN_VL_MODEL=qwen3.6-plus
```

不要把 `.env` 提交到 Git。

如果任务启用视频审核，还必须显式配置 ffmpeg 和 Dolphin。它们不属于
DashScope/Qwen，也不会因为机器上另一个旧后端已经连上 Dolphin 而自动继承：

```env
FFMPEG_PATH=/absolute/path/to/ffmpeg
USE_REMOTE_ASR=true
REMOTE_ASR_BASE_URL=http://127.0.0.1:19001
REMOTE_INFERENCE_API_KEY=<和远端 INFERENCE_API_KEY 一致的运行时密钥>
ASR_ENGINE=dolphin
```

API 与 worker 必须从同一份 `.env` 或受控 runtime `secrets.env` 启动。真实密钥不得写入
README、提交记录、构建收据或示例文件。

## 3. 只启动本地后端

推荐直接用脚本：

```bash
./scripts/start-backend.sh
```

脚本会自动创建 `.venv`，安装 `requirements.txt`，然后启动：

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

启动后打开：

```text
http://127.0.0.1:8000/saas
```

常用参数：

```bash
BACKEND_PORT=8010 ./scripts/start-backend.sh
RELOAD=true ./scripts/start-backend.sh
INSTALL_DEPS=false ./scripts/start-backend.sh
```

Windows 可以手动执行：

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

## 4. 接入服务器 ASR

### 方式 A：ASR 服务可直接访问

如果服务器的 ASR 端口能被本地电脑直接访问，在 `.env` 里配置：

```env
USE_REMOTE_ASR=true
REMOTE_ASR_BASE_URL=http://<server-host>:9001
REMOTE_INFERENCE_API_KEY=<和服务器 INFERENCE_API_KEY 一致的 key>

ASR_ENGINE=dolphin
ASR_LANGUAGE=ug
DOLPHIN_LANG_SYM=ug
DOLPHIN_REGION_SYM=CN
DOLPHIN_WORD_TIMESTAMP=true

TRANSLATION_ENABLED=true
ASR_TRANSLATE_ENGINE=qwen_text
ASR_TRANSLATE_MODEL=qwen3.7-max
```

然后启动本地后端：

```bash
CHECK_REMOTE_ASR=true ./scripts/start-backend.sh
```

`CHECK_REMOTE_ASR=true` 只请求一次远端 `/api/inference/health`，用于确认健康接口可达；部分服务的健康接口不要求鉴权，因此不能证明转写凭据正确。必须再用相同环境实际转写短音频。
真实视频任务验收必须使用这个检查，不能只确认 `19001` 端口处于监听状态：端口监听不代表
Dolphin 模型已加载、鉴权一致或返回合同正确。检查失败时不要启动真实采集任务。

### 方式 B：ASR 服务只监听服务器本机

如果服务器端口没有对公网开放，用 SSH 隧道：

```bash
ssh -p <ssh-port> -N -L 19001:127.0.0.1:9001 <user>@<server-host>
```

另开一个终端，在 `.env` 里配置：

```env
USE_REMOTE_ASR=true
REMOTE_ASR_BASE_URL=http://127.0.0.1:19001
REMOTE_INFERENCE_API_KEY=<和服务器 INFERENCE_API_KEY 一致的 key>

ASR_ENGINE=dolphin
ASR_LANGUAGE=ug
DOLPHIN_LANG_SYM=ug
DOLPHIN_REGION_SYM=CN
```

启动本地后端：

```bash
CHECK_REMOTE_ASR=true ./scripts/start-backend.sh
```

也可以用已有的一键本地栈脚本，它会建虚拟环境、开 SSH 隧道、后台启动后端：

```bash
SERVER=<user>@<server-host> SSH_PORT=<ssh-port> ENABLE_REMOTE_INFERENCE=false ENABLE_DOLPHIN=true ./scripts/start-stack.sh
```

停止本地后台服务和隧道：

```bash
./scripts/stop-stack.sh
```

### 方式 C：Dolphin 运行在 Coder workspace

Coder workspace 内的 Dolphin 监听 `127.0.0.1:19001` 时，可以用 Coder CLI 转发：

```bash
coder login https://<coder-deployment>/
coder port-forward <workspace> --tcp 19001:19001
```

需要自动重连时，使用 ASR guard：

```bash
ASR_TUNNEL_TRANSPORT=coder \
CODER_WORKSPACE=<workspace> \
REMOTE_ASR_PORT=19001 \
bash scripts/asr-guard.sh start
```

本地后端仍配置为：

```env
USE_REMOTE_ASR=true
REMOTE_ASR_BASE_URL=http://127.0.0.1:19001
REMOTE_ASR_REQUEST_RETRIES=3
REMOTE_ASR_RETRY_BACKOFF_SECONDS=2
```

ASR 调用只会重试连接失败、超时、HTTP 408/429 和 5xx。鉴权失败、非重试型 4xx 及结果合同错误仍会 fail closed。

## 5. 启动服务器 ASR 服务

如果服务器已经按项目脚本配置好，直接从本地执行：

```bash
SERVER=<user>@<server-host> SSH_PORT=<ssh-port> ./scripts/remote-stack.sh status
SERVER=<user>@<server-host> SSH_PORT=<ssh-port> ./scripts/remote-stack.sh start
```

默认端口约定：

- Dolphin ASR：服务器 `9001`，本地隧道 `19001`
- MMS ASR：服务器 `9004`，本地隧道 `19004`
- HY-MT 翻译：服务器 `9002`，本地隧道 `19002`
- VLM/LLM 推理：服务器 `9000`，本地隧道 `19000`

如果只需要 Dolphin ASR，可以关闭其他远端服务：

```bash
SERVER=<user>@<server-host> SSH_PORT=<ssh-port> ENABLE_REMOTE_INFERENCE=false ENABLE_DOLPHIN=true ENABLE_MMS_ASR=false ENABLE_HYMT=false ./scripts/remote-stack.sh start
```

服务器上实际跑的是：

```bash
python -m uvicorn backend.inference_server:app --host 0.0.0.0 --port 9001
```

启动这个服务时，服务器环境里要有：

```env
INFERENCE_API_KEY=<服务端鉴权 key，可为空>
ASR_ENGINE=dolphin
ASR_LANGUAGE=ug
DOLPHIN_MODEL_DIR=/mnt/workspace/models/dolphin
DOLPHIN_LANG_SYM=ug
DOLPHIN_REGION_SYM=CN
```

本地 `.env` 的 `REMOTE_INFERENCE_API_KEY` 要和服务器的 `INFERENCE_API_KEY` 一致。如果服务器 `INFERENCE_API_KEY` 为空，则远端接口不做鉴权。

## 6. 手动测试 ASR 接口

健康检查：

```bash
curl -H "X-Inference-Key: $REMOTE_INFERENCE_API_KEY" \
  http://127.0.0.1:19001/api/inference/health
```

上传一段音频测试转写：

```bash
curl -X POST \
  -H "X-Inference-Key: $REMOTE_INFERENCE_API_KEY" \
  -F "audio=@/path/to/audio.wav" \
  http://127.0.0.1:19001/api/inference/transcribe
```

如果使用 MMS ASR：

```env
USE_REMOTE_MMS_ASR=true
REMOTE_MMS_ASR_BASE_URL=http://127.0.0.1:19004
MMS_TARGET_LANG=uig-script_arabic
```

测试：

```bash
curl -X POST \
  -H "X-Inference-Key: $REMOTE_INFERENCE_API_KEY" \
  -F "audio=@/path/to/audio.wav" \
  http://127.0.0.1:19004/api/inference/mms-transcribe
```

## 7. 后端内部调用链

核心代码位置：

- 本地后端入口：`backend/main.py`
- 远端推理服务入口：`backend/inference_server.py`
- 本地调用远端 ASR 的客户端：`backend/audit_agent/remote_inference.py`
- 视频抽音频和 ASR 调度：`backend/audit_agent/video_processor.py`
- 任务流水线和 ASR 结果落盘：`backend/audit_agent/pipeline.py`

调用链：

```text
任务开始
  -> 下载/读取视频
  -> ffmpeg 抽音频
  -> USE_REMOTE_ASR=true 时 POST REMOTE_ASR_BASE_URL/api/inference/transcribe
  -> 远端 Dolphin 返回 text + segments
  -> 本地可选做 MMS 复核、ASR 翻译
  -> 原始 ASR JSON 保存到 outputs/<job_id>/.../asr_raw.json
  -> 前端读取任务结果展示
```

## 8. 常见问题

`401 Invalid inference key`：本地 `REMOTE_INFERENCE_API_KEY` 和服务器 `INFERENCE_API_KEY` 不一致。

`USE_REMOTE_ASR=true but REMOTE_ASR_BASE_URL ... is empty`：本地没有配置 ASR URL，或者脚本启动时没有读到 `.env`。

`Connection refused`：远端 ASR 没启动，或 SSH 隧道没开。先跑 `/api/inference/health`。

视频任务没有 ASR 文本：检查 ffmpeg 是否安装，任务日志里是否有“音频抽取失败”。

新环境里视频全部立即失败，但旧环境正常：检查新 API 和新 worker 的实际进程环境。最常见原因是
手动执行裸 `uvicorn` 或 worker 命令时，只注入了 DashScope/DMX Key，漏掉
`FFMPEG_PATH`、`USE_REMOTE_ASR=true`、`REMOTE_ASR_BASE_URL`、
`REMOTE_INFERENCE_API_KEY` 和 `ASR_ENGINE=dolphin`。修正环境并重启新 API 与 worker；
不要把旧服务的进程环境当成全局配置。

端口被占用：换端口启动，例如 `BACKEND_PORT=8010 ./scripts/start-backend.sh`，或先停掉旧服务。

模型很慢或首次卡住：服务器第一次加载 Dolphin/MMS 模型会比较慢，先看服务器 `outputs/service-logs/` 下的日志。
