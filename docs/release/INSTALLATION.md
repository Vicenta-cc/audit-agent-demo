# 新机器安装与运行

以根目录 README 的安装器为唯一默认入口。旧的 `dc53807` 固定目录启动说明、3128/3148 双后端方案和其他历史脚本仅作开发记录，不是本交付的启动方式。

## 安装器执行的步骤

```bash
git submodule update --init --recursive
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-hermes.txt
uv venv --python 3.12 external/MediaCrawler/.venv
uv pip install --python external/MediaCrawler/.venv/bin/python -r external/MediaCrawler/requirements.txt
external/MediaCrawler/.venv/bin/python -m playwright install chromium
pnpm --dir Audit_assistant install --frozen-lockfile
pnpm --dir Audit_assistant build
cp demo/demo.env.example .env.demo.local
.venv/bin/python scripts/demo.py init
```

上面是首次手动安装示例；已有配置时不要执行 `cp` 覆盖自己的配置。安装器会保留现有配置。

Hermes 固定为 `e624e9fde561e1add9388384012b295fde669ade` / 0.20.4，以子模块源码的 editable 方式安装。该版本上游拒绝 wheel/sdist 构建，不能直接 `pip install git+...`。本项目的适配在 `.hermes/plugins/xhs-investigation`、`backend/hermes_runtime` 与 `hermes_m0` 中，未要求修改 Hermes 核心源码。

MediaCrawler 固定为 `5f428d1071522ce1e011ceedeceffaa858404949`，来自 Vicenta-cc/media-crawler。必须使用它自己的 `.venv`：其 Pydantic、Playwright 等依赖与主项目不同，不能装进同一个环境。本安装采用其 `requirements.txt`，不是另外执行 `uv sync` 重新选择依赖。

## 环境配置

`.env.demo.local` 中的相对路径相对于仓库根目录解析；`DEMO_DATA_DIR` 默认 `./data/demo`。所有业务 SQLite 数据库、账号加密 Key、Hermes 状态、输出文件与启动日志都放到该目录下。

- `DEMO_API_PORT=8158`、`DEMO_WEB_PORT=3158`：可以改成其他空闲端口，两者不能相同。
- `DASHSCOPE_API_KEY`：必须自行填写；安装器不复制开发者密钥。
- `QWEN_TEXT_MODEL` / `QWEN_REPORT_MODEL` / 视觉模型：账号必须拥有对应服务权限。Hermes 产品适配固定使用 qwen3.7-plus，不应把修改普通审核模型配置误解为切换 Hermes 问答模型。
- `WHISPER_DEVICE=cpu`、`WHISPER_COMPUTE_TYPE=int8`：默认可在普通 CPU 运行，不依赖作者的 GPU 服务。`FFMPEG_PATH` 未填写时使用 imageio-ffmpeg 提供的可执行文件。
- 远程 ASR 可配置 `USE_REMOTE_ASR=true`、`REMOTE_ASR_BASE_URL`、`REMOTE_INFERENCE_API_KEY`；不要把不可访问的个人隧道地址写到公共模板。

启动器先读取指定配置，再强制绑定当前代码、数据、输出、Crawler Python、密钥文件和端口。它不继承终端里遗留的业务环境变量，也不加载根目录其他 `.env`；代理环境变量可继续使用。前端进程只接收本地 API 端口等必要环境变量，浏览器不接收模型 Key。

自定义配置文件示例：

```bash
.venv/bin/python scripts/demo.py init --config /absolute/path/to/demo.local.env
.venv/bin/python scripts/demo.py start --config /absolute/path/to/demo.local.env
```

## 预下载 Whisper

联网时可提前下载，避免首次视频任务等待：

```bash
.venv/bin/python -c 'from faster_whisper import WhisperModel; WhisperModel("base", device="cpu", compute_type="int8")'
```

模型下载受 Hugging Face 网络可用性影响；无法访问时配置已部署的远程 ASR，或者通过该库支持的本地模型目录配置 `WHISPER_MODEL`。

## 保存、备份和恢复

`init` 幂等导入 A/B，不清空已生成报告、会话和账号；发现档案哈希不同或未标识的既有数据库会拒绝覆盖。请使用一个新目录，不要直接指向作者的生产数据目录。

停止服务后备份**整个 `DEMO_DATA_DIR`**，包括 `audit_index.sqlite3`、其他 SQLite 文件、`outputs/`、`crawler_auth.key`、`hermes/` 和 `demo-seed/`。只复制 SQLite 而漏掉账号密钥，会导致已保存登录态无法解密；只复制数据库而漏掉输出文件，会丢失新报告的本地媒体。

恢复时将完整备份放回配置的数据目录，使用相同代码版本启动。移动项目后 editable Hermes 路径也会变，应重新运行安装器。正常关闭不删除对话；磁盘损坏、手动清理或备份缺失不在持久化保证范围内。

`startup-logs/api.log`、`web.log`、`worker.log` 用于排错。日志、账号、Key、运行结果和 `.env.demo.local` 均不提交 Git。

## 常见故障

| 现象 | 处理 |
| --- | --- |
| 子模块为空 / Hermes 不可导入 | 执行 `git submodule update --init --recursive`，再安装依赖 |
| Python 依赖冲突 | 主项目与 MediaCrawler 分开 `.venv`，按安装器依赖文件安装 |
| A/B 不显示 | 检查 `init` 是否成功、seed 哈希和 `status`，不要复制未经裁剪的旧库 |
| Key 存在但问答失败 | 查看 API/Turn 日志，检查模型权限、余额、网络及限流；`check` 不测外部调用 |
| 创建任务提示采集不可用 | 到“采集账号”添加账号并登录；A/B 不要求这一步，新采集要求 |
| 验证码或账号失效 | 保留失败记录，停止该任务，人工完成官方登录验证后再创建任务；不自动无限重试 |
| 视频审核首次很慢 | 先下载 Whisper；检查视频时长、评论数、ASR 与模型响应时间 |
| 端口被占用 | 核对已运行环境；自行在原终端关闭，或明确修改本环境端口；脚本不会接管 |
| 普通 HTTP 200 但环境不对 | 使用 `scripts/demo.py status`，它检查代码/数据/Python/端口及前端代理身份 |

代码支持任务级故障记录，不额外部署自动重启抓取的定时器。启动器任一子进程退出会停止本次其他进程；重新 `start` 前先检查任务状态与原因，避免把进程重启误当作安全的业务重试。
