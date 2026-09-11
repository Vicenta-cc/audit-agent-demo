# M3 独立完整实验环境

本机日常入口：**http://127.0.0.1:3178/investigation**。

系统服务已安装，登录当前 macOS 用户后自动启动；关闭启动命令所在终端不影响服务。只导入 A、B，不等待或导入 C。规则词库操作仍走自然语言，未增加资源编辑面板。

## 环境绑定

| 项目 | 固定选择 |
| --- | --- |
| Worktree | `/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-m3-resource-lifecycle-experiment` |
| 分支 | `codex/m3-resource-lifecycle-experiment` |
| 服务 | 完整 `backend.main`、静态前端及代理、真实 investigation creation worker |
| API / 前端 | `127.0.0.1:8178` / `127.0.0.1:3178` |
| 配置、私密凭据及数据 | 本 worktree 的 `outputs/m3-environment/`，Git 忽略 |
| 数据库和报告输出 | `outputs/m3-environment/data/` |
| A/B 归档 | `outputs/m3-environment/archives/`，导入前核对固定 SHA-256 |
| 采集代码及浏览器状态 | `outputs/m3-environment/crawler/`，代码副本，不复制已有浏览器状态 |
| Python | product baseline 的 `.venv-r0-2-1/bin/python`，Hermes 0.20.4 |
| Node | Codex 已安装的 Node 运行时，路径写入私有 `config.json` |
| Qwen | 用户授权付费 Token Plan 地址，Key 只在私有 `secrets.env` |
| 系统服务 | `com.xhs-audit.m3-resource-experiment` |

这里只复用已安装的依赖运行时，不共用 K2/3168 的数据库、任务队列、账号、采集密钥文件或浏览器状态。配置中的数据目录、采集目录必须指向本实验；保留端口被显式拒绝。启动器不会按端口杀进程、自动换端口或接管其他服务。

## 日常操作

正常情况下只打开上面的固定网址。需要维护时，在本 worktree 执行：

```bash
./scripts/m3-experiment.sh check
./scripts/m3-experiment.sh status
```

`check` 检查 Hermes、提示词、密钥存在性、A/B 归档指纹和导入记录、采集器关键源码实际可导入、运行文件及前端构建对应的源码指纹，不调用模型或采集。

`status` 同时核对 API、前端代理及前端自身的环境身份；实际读取 A/B 报告接口，并核对 worker 心跳。普通 HTTP 200 不算成功。

更新实验代码后：

```bash
./scripts/m3-experiment.sh build
./scripts/m3-experiment.sh restart
```

`restart` 仅重启本实验的系统服务，等待前后端、报告与 worker 都就绪后才返回成功。`build` 不重启服务；构建后必须 `restart`。代码变化但未构建时，启动器拒绝启动。API 和前端分别固定启动时的构建身份，避免旧 API 冒充新构建。

维护时停止及重新安装/启动本实验服务：

```bash
./scripts/m3-experiment.sh stop
./scripts/m3-experiment.sh install-service
```

`install-service` 用于尚未加载或已停止的本实验服务。已经正常运行时无需再次安装。也可以用 `start` 前台启动：已有同一环境时验证后复用，否则终端退出时关闭本次子进程。前台模式不用于日常托管。

日志：`outputs/m3-environment/logs/{supervisor,api,frontend,worker}.log`。请保留完整运行目录，不要删除数据库、重新初始化或复制其他 worktree 的配置来修复启动问题。

## 前端连接及可用范围

前端先检查后台环境身份和 A/B 报告可读性，再呈现调查工作区。连接错误时展示明确错误及重新连接按钮，不将其他后台或缺少 A/B 的环境当成可用实验。

目前具备 A/B 报告渲染、真实报告问答及持久会话；完整 API 包含新 M3 自然语言资源读改存和调查创建能力。K2 规则由版本库中的规则包发布到本实验自己的规则库。补入了条件评论翻译、冻结评论覆盖率问答和临时规则恢复文案修复。

**采集限制：**用户已在本实验单独登录抖音账号；真实“采集→审核→新报告→问答”现已验证，详见 [真实全流程验收](M3_REAL_E2E_VALIDATION_20260911.md)。账号登录状态仍由平台决定，不使用 K2/3168 的会话或 cookie。默认每关键词最多 1 帖、分析上限 1 帖、每帖最多 3 条一级评论，低并发限制保存在本实验配置中。

## 验收记录（2026-09-11）

- 后台专项回归：45 项通过，覆盖 K2 评论翻译、pass 调查和 M3 资源生命周期。
- 环境隔离及恢复测试：11 项通过，覆盖外部目录/保留端口拒绝、错误身份、A/B 缺失、源码变化、活跃端口拒绝及已关闭连接的端口复用。
- 对话展示逻辑：2 项通过；TypeScript 检查和 Vite 生产构建通过。仍存在原有大 chunk 体积提示。
- 浏览器中打开 A/B，分别完成真实 Qwen 问答；A 耗时 34.66 秒，B 耗时 33.39 秒（后台完整轮次时间）。各仅一次，不用于推断新旧性能差异。此前的配对性能测量见 `M3_FLOW_AND_LATENCY_VALIDATION_20260911.md`。
- A 问答正确使用 201 篇分析规模；B 问答区分帖子与评论风险，保留不能据评论推断作者认同、行为模式或民族身份的边界。
- 详细答案、耗时及恢复记录在 `evidence/m3-environment/acceptance.json`；实际前端截图在同目录。

## 首次配置或恢复到其他机器

这份配置绑定本机绝对路径，不可直接复制到另一个 worktree。一次性配置工具为 `scripts/provision_m3_local.py`；输入明确的 A/B 不可变归档、模型/媒体服务配置来源、采集代码和依赖路径，通过无回显输入设置实验 Key。

```bash
python scripts/provision_m3_local.py \
  --source-runtime /path/to/explicit-service-configuration \
  --crawler-source /path/to/crawler-code \
  --archive-a /path/to/immutable-report-a.sqlite3 \
  --archive-b /path/to/immutable-report-b.sqlite3 \
  --python /path/to/verified/python \
  --node /path/to/node
./scripts/m3-experiment.sh init
./scripts/m3-experiment.sh build
./scripts/m3-experiment.sh check
./scripts/m3-experiment.sh install-service
```

依赖需事先安装；本工具不负责依赖安装或独立账号登录。目标运行目录已存在时拒绝覆盖；`init` 已成功时也拒绝再次初始化。来源运行目录只用于读取服务配置，不复制活跃数据库或任务队列。A/B 归档严格按指定指纹复制；新机器如归档不符需先明确报告来源，不能跳过校验。
