# Audit Agent Demo

本地内容巡查演示：查看既有 Report A/B 并追问证据，通过自然语言创建单条采集任务，完成审核、生成新报告并继续问答。

默认是一套前端、一套 API、一个 worker、一个数据目录；不需要复制本项目作者的数据库、虚拟环境或绝对路径。A/B 来自冻结的真实历史报告，并非模型每次重新生成。模型 Key 和平台登录由使用者自己配置。

## 安装（macOS / Linux）

准备 Git、Python 3.12（也可由 uv 自动安装）、[uv](https://docs.astral.sh/uv/getting-started/installation/)、Node.js 22+ 和 pnpm 11。首次安装需要能访问 GitHub、Python/npm 包源及 Playwright 下载源。

```bash
git clone --recurse-submodules git@github.com:Vicenta-cc/audit-agent-demo.git
cd audit-agent-demo
./scripts/install-demo.sh
```

没有 GitHub SSH Key 时，可将克隆地址改为 `https://github.com/Vicenta-cc/audit-agent-demo.git`。若仓库为私有，需要仓库访问权限。不要使用 GitHub 的 Download ZIP：它不会包含两个子模块源码。

安装器会分别创建主项目和 MediaCrawler 的 Python 环境，安装固定 Hermes 源码、Chromium、构建前端，并向全新的 `data/demo` 导入 A/B。FFmpeg 由 Python 依赖提供。Linux 如缺少 Chromium 系统库，根据 Playwright 的提示安装系统依赖；需要管理员权限时自行运行 `external/MediaCrawler/.venv/bin/python -m playwright install-deps chromium`。

## 配置和启动

编辑安装器生成的 `.env.demo.local`，填入有效的 `DASHSCOPE_API_KEY`。该 Key 需要能调用配置的 Qwen 文本、报告和视觉模型。

```bash
.venv/bin/python scripts/demo.py check
.venv/bin/python scripts/demo.py start
```

打开 **http://127.0.0.1:3158/investigation**。API 默认端口为 8158。`start` 保持终端运行；Ctrl+C 同时停止本次 API、前端和 worker，保留报告、对话、账号和进度数据。另一终端查看：

```bash
.venv/bin/python scripts/demo.py status
```

`check` 只核对本地依赖、提示词、A/B 数据完整性和 Key 是否填写，不会调用模型或抓取。`start` 会验证 API/前端身份和 A/B 接口后才启动 worker；端口被其他服务占用时失败，不杀其他进程、不自动换端口。重复启动同一环境会检查现有实例，不再启动第二个 worker。

## 使用流程

1. 左侧打开“我好累心好累监控任务”（A）或“麦热依姆古丽监控任务2”（B）。点击“查看完整报告”，或直接提问“总结主要发现”“展开代表帖子和直接证据”。A/B 问答只需模型 Key，不需要平台登录。
2. 创建新任务前，打开左侧“采集账号”，添加抖音账号并完成界面提供的登录流程。登录态仅加密保存到本地数据目录，不随项目分发。
3. 点击“新建调查”，例如输入：**“在抖音用‘猫咪打哈欠’这一个搜索词采集并审核 1 条公开帖子及评论，检查人身辱骂，普通玩笑不算风险。生成本次任务的规则并形成报告。”**
4. 按对话完善方案、选择平台、生成任务配置，检查预览后确认执行。自然语言描述会先形成草稿，**不会跳过确认直接开始抓取**。
5. 在同一窗口查看采集、研判和报告状态。报告发布后直接继续询问样本、结论和证据；刷新页面或重启后仍可查看。

默认最多审核 **1 条帖子、每帖最多 300 条一级评论、不采集楼中楼**。评论数是上限，并不保证平台返回足量。审核和问答都调用真实模型；全 pass 可以生成报告，缺失的媒体或失败的审核不能冒充 pass。

## 数据与依赖

- [完整安装、备份与排错说明](docs/release/INSTALLATION.md)
- [A/B 演示数据包范围](demo/seed/README.md)
- [单条与批量配置、K 规则的区别](docs/release/BATCH_EXTENSION.md)
- [依赖版本与验证记录](docs/release/ACCEPTANCE.md)

默认不携带民族 K RuleSet 到可编辑资源库。K 审核代码和提示词兼容能力保留，只有任务明确选用相应 RuleSet 才会生效；“抓 1 条/抓多条”不会自动选择 K。A/B 历史结果不随新任务规则变化。

ASR 默认使用 CPU Whisper `base`，首次使用会下载模型；离线运行前应提前缓存。你也可以在本地配置中填写自己的远程 ASR 服务。平台验证码、登录过期、模型余额、限流和远程服务故障需要分别处理；本项目不会绕过验证，也不承诺平台服务始终可用。

本演示只监听 `127.0.0.1`，使用本地单用户权限。不要直接作为多租户公网 SaaS 部署。保留 MediaCrawler 与 Hermes 各自的许可证和使用要求。
