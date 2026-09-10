# 历史交付规划（归档）

此文件保留最初发布规划，不作为当前安装指南。已完成的安装入口见 [INSTALLATION.md](INSTALLATION.md)，已验证范围见 [ACCEPTANCE.md](ACCEPTANCE.md)，默认范围见根 README。下面的“待补”“未上传”和旧绝对路径均是历史状态。

> 仓库状态更新：本源码已整理到 Vicenta-cc/audit-agent-demo；主应用来源 f6d19f8，MediaCrawler 固定为子模块 5f428d1。默认范围与缺少的历史语料/A-B 数据包以根 README 为准。下文是此前的详细发布计划，不能将历史“未上传”状态或双环境拓扑当成当前默认安装要求。

# GitHub 完整项目交付与两种运行配置

> 最新交付范围：默认只交付 Report A/B 查看与问答、用户新建单帖任务及新报告问答。使用一套前端、一套后端、一个 worker、一个数据目录；不要求部署批量后台或双环境网关。以下双环境/批量内容仅作为可选扩展参考，详见 [BATCH_EXTENSION.md](BATCH_EXTENSION.md)。现有双环境启动器仍需整理为单环境发布入口，不能把方案当作已完成部署。

> 审核配置与采集数量独立：单帖任务选用 K 专用发布版本时同样使用 K Prompt 和评论/融合 thinking=true；新生成的普通 RuleSet 不会自动继承 K 特制模板及思考参数。A/B 报告问答走独立问答流程，不因 K 审核参数自动切换。已存在任务使用其冻结快照，不追溯更改。具体边界见扩展说明。

核对日期：2026-09-10。代码功能基准：`f6d19f8`，分支 `codex/ethnic-collection-trial`。本文是发布实施规范及接收者操作说明，不表示仓库已经上传，也不表示下文标为“待补”的安装能力已经存在。

## 一、交付目标

接收者应能够克隆固定版本，在自己的环境安装依赖、配置自己的模型密钥及采集账号，然后运行“单条演示”或“完整批量任务”，查看对话、评论依据及报告。无需访问原开发者的其他项目目录、虚拟环境、Codex 附件或历史数据库。

应交付一套代码、两种运行配置，而不是两个会持续分叉的代码仓库。完整源码不等于复制开发者整个硬盘；外部模型服务、个人登录凭据、历史任务数据须明确单独提供或由接收者配置。

## 二、GitHub 至少要包含什么

| 内容 | 交付要求与当前情况 |
| --- | --- |
| 应用源码 | 以 f6d19f8 的全部已跟踪文件为基础；包含 backend、Audit_assistant、hermes_m0、脚本、测试及应用使用的资源。不要仅复制 backend 和前端两个目录。 |
| K 规则与 Prompt | `backend/rulesets/bundles/ethnic_k_v1/` 四个文件及编译接入代码已纳入 Git。专用规则 ID：`ruleset.ethnic-content-review.k-trial.v1`。必须通过正常发布/编译流程，不能只复制数据库中的名称。 |
| 召回词库种子 | **待补**可发布的 17 词 JSON 及幂等导入脚本。当前主库里的词库不会随 Git 克隆；种子不能仅放在被忽略的 artifacts。 |
| 数据初始化 | **待补**从空目录初始化全部必需数据库、输出目录、规则资源和路由登记的统一入口。禁止依赖现有会话 ID、假造已完成任务或直接拷贝生产库充当初始化。 |
| Python 依赖 | 已有 requirements.txt、requirements-hermes.txt；Hermes 固定源提交见运行时清单。发布前从新虚拟环境验证并补全依赖锁定，不能把“在旧虚拟环境里能跑”当作依赖完整。 |
| 前端依赖 | Audit_assistant/package.json 和 pnpm-lock.yaml；补记经过验证的 Node/pnpm 版本。不要提交 node_modules。 |
| 采集器 | **待补**MediaCrawler 的实际源版本、上游地址、依赖、必要修改补丁及安装方法。目前未发现跟踪的 external/MediaCrawler 或 Git 子模块，不能承诺递归 clone 即可获得。 |
| 模型/媒体依赖 | 说明 FFmpeg/ffprobe、浏览器及系统依赖；按实际启用模式列出 ASR、OCR、翻译、视觉和文本服务的接口、版本或模型来源。应用源码不会自动包含远端推理服务器。 |
| 配置模板 | .env.example 已有；**待补**无本机路径和真实密钥的启动配置模板、单条/批量配置。 |
| 启动与停止 | 已有 scripts/collection_trial.py，但仅适配 macOS launchd 和已有数据；**待补**可移植初始化、配置和明确的启动/停止流程。Linux 需另做 systemd/容器适配并验证，不能直接称支持。 |
| 有界任务及监控 | **待迁移**当前夜间 run_once.py、guard.py 的可复用逻辑，移出 artifacts，消除固定任务 ID、绝对路径，支持持久化失败计数。不要依赖 Codex 定期检查作为项目唯一监控。 |
| 演示报告 | 若需开箱展示 A/B，提供脱敏演示数据及合法可分发的证据资源，并通过导入流程建立会话/报告。否则允许空项目启动，不应强制要求历史 A/B 存在。 |
| 文档与许可 | 根 README 指向本说明，写清许可、第三方声明、安装验证、故障定位、数据迁移和当前质量限制。当前旧 README 含 Windows 开发路径，不能直接作为本版安装文档。 |

仓库应增加明确的忽略规则：`.env.collection-trial.json`、`*.local.json`、`*.local.env`、真实账号密钥与浏览器登录状态。现有 `.gitignore` 忽略 data、outputs、artifacts、虚拟环境及 node_modules，但仅忽略 `.env` 不足以覆盖所有本机配置名。不要用 `git add -f artifacts` 或上传整个 data 目录解决缺文件问题。

## 三、两种模式的准确区别

以下是现有启动器实际参数，不是猜测：

| 配置 | 单条演示 demo | 完整任务 production |
| --- | --- | --- |
| M3_POSTS_PER_KEYWORD | 1 | 20 |
| M3_ANALYZE_LIMIT | 1 | 340 |
| M3_COMMENTS_PER_POST | 300 | 1000 |
| 当前采集并发 | 1 | 1 |
| 次级评论 | false | false |
| 数据 | 独立 demo 目录 | 独立 production 目录 |
| 目的 | 单帖完成采集、研判与展示 | 17 词，每词最多 20 帖的有限任务 |

“只抓一条”是一个帖子，不是一条评论。演示验收应使用一个关键词，并同时确认实际任务快照中的 max_notes=1、analyze_limit=1、max_posts_per_keyword=1，避免仅改分析上限而采集更多帖子。

完整任务应确认：17 词快照、max_posts_per_keyword=20、总帖数上限340、analyze_limit=340、max_comments=1000、max_concurrency=1、get_sub_comment=false。按内容 ID 在任务内去重，实际数量可少于上限。此模式不是无限循环，也不保证恰好运行24小时。

K 两种模式可以使用相同的判断配置：qwen3.7-plus；评论 thinking=true/max_tokens=6000；融合 thinking=true/max_tokens=3000；单次超时120秒；超时同参数补试一次。不是所有模型调用都必须开启思考，语音翻译等必须保留各自参数，不能用全局断言误拦截。

K 融合只接收风险评论候选，超过20条取代表样本，全部逐条判断和统计保留，不能改写评论判断。其他模态继续保留风险证据和必要背景。K 是试运行口径，接入成功不意味着所有判断均正确。

## 四、当前启动器怎么选择模式

当前 `scripts/collection_trial.py` 中的 configure_backend 按 role 强制设置上述数量。**不能只在 .env 改 M3_* 就认为已切换，它会被覆盖。** identity 返回的 baseline=cd7ce5e 也是历史标签，发布版需额外返回真实代码提交、配置哈希，不能将该标签当当前版本证明。

现有双环境拓扑：前台3148 → 网关8148 → demo API8147 / production API8149；两套后端运行同一份代码，各自连接数据和 worker。普通新建对话默认进入 demo。正式会话及派生任务、报告需要登记 production 路由；不是打开同一个网页就会自动开启批量。

发布前建议把 role 的参数抽成可校验配置：demo profile 固定1帖，production profile 提供批量上限；启动身份和最终冻结快照必须来自同一配置。新机只有单模式需求时可仅启动对应 API、前端和一个 worker；但这条可移植单模式启动路径目前仍需实现，不能套用旧 README 的默认 uvicorn 命令代替。

若要求同时保留空白词库/规则生成演示与预置 K 批量环境，可只向 production 初始化 K 及词库。要在 demo 也试 K，可以单独导入，不能把两个数据目录指到同一位置。

## 五、发布者应如何制作一个可下载的版本

1. 从 f6d19f8 创建发布整理分支，保留正在运行的工作目录；把上表待补内容完成，形成新的发布提交。不要在夜间任务正在运行时随意重启或替换服务。
2. 将私有 runtime 中的任务执行与守护代码抽取为仓库脚本；会话 ID、run ID、账户 ID、注册表路径均改为参数或新建回执，旧重试计数不混入新项目。
3. 补两份无密钥配置模板、初始化器、预检器、受控启动/停止器；初始化必须幂等、不清空旧库、不自动执行旧任务。账号密钥首次生成后持久保留。
4. 固定 MediaCrawler 版本。若允许分发，选择子模块固定提交或随源码交付；有本地修改则提供可重放补丁。若需独立授权，文档明确获取途径并让预检报出缺项，不能宣称仓库完全自包含。
5. 导出词库/规则等可公开资源为 JSON。K 模板直接用已跟踪 bundle，不改写旧版资源。演示数据与真实采集数据分开。
6. 在全新目录和虚拟环境执行下面的接收流程；不得引用原机器任何兄弟项目、现有 .venv 或历史 data。分别完成1帖和小规模批量的真实端到端验证。
7. 保存 Python/Node/pnpm、采集器及模型服务版本、锁文件、测试结果、实际提交和发布清单。通过后打发布标签，推送源码和标签；大体积合法资源放 Release 资产并附 SHA-256、版本和放置位置。
8. 如果需要提供 Download ZIP，额外提供包含子模块源码的 source bundle 或明确补下载步骤。GitHub 普通源码 ZIP 不应被当作自动包含外部采集器的整包。

本文未执行 GitHub 上传。仓库公开/私有范围、目标地址及外部资源许可需要发布时按实际选择落实。

## 六、接收者操作流程

以下命令用于最终发布包；`REPOSITORY_URL` 和 `RELEASE_TAG` 必须由发布者提供。当前 f6d19f8 仍缺初始化及采集器自动安装集成，不能仅执行这段就认定安装完成。

```bash
git clone --recurse-submodules REPOSITORY_URL audit-agent
cd audit-agent
git checkout RELEASE_TAG
git submodule update --init --recursive
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-hermes.txt
cd Audit_assistant
pnpm install --frozen-lockfile
pnpm run build
cd ..
cp .env.example .env
```

接着按发布清单安装采集器、它自己的依赖和 Playwright 浏览器，安装 FFmpeg/ffprobe。Node 也可能被采集器用于 JavaScript 执行，不能因前端已构建就删掉。版本使用发布清单中验证过的版本。

填写自己的 DASHSCOPE_API_KEY、模型服务地址、MEDIACRAWLER_DIR；若启用远端 ASR/OCR/翻译，填写相应服务及凭据；如果选择本地推理，还需自行安装对应依赖和权重。所有外部必需服务均通过预检后才能声称“完整可运行”，纯假数据 UI 不算真实采集验收。

然后运行发布包提供的初始化入口（**目前待实现，不能杜撰已存在的命令**），创建独立 demo/production 数据与输出目录，导入资源，生成账号加密密钥，使用自己的账号登录。按选择的 profile 启动，在前台确认实际数量后才执行任务。

macOS 已有完整数据的迁移用户，可以使用现有启动器，先根据下列字段创建 `.env.collection-trial.json`；路径必须替换为本机真实绝对路径：

```json
{
  "worktree": "/ABS/audit-agent",
  "python": "/ABS/audit-agent/.venv/bin/python",
  "node": "/ABS/node/bin/node",
  "secrets_env": "/ABS/audit-agent/.env",
  "demo_data": "/ABS/runtime/demo",
  "production_data": "/ABS/runtime/production",
  "demo_port": 8147,
  "production_port": 8149,
  "gateway_port": 8148,
  "frontend_port": 3148,
  "registry": "/ABS/runtime/registry.json",
  "logs": "/ABS/runtime/logs"
}
```

现有脚本的 `check`、`status` 是身份检查；`start` 启动两套 API、网关和前台；`workers` 会启动两套 worker，不是仅启动新选择的任务。**必须先处理待执行队列并核实范围再启动 worker。** `stop` 停止本启动器管理的服务。脚本还要求两个数据目录已有三个数据库、crawler_auth.key、outputs，以及可用 A/B 报告和 registry，因此不能用于空库首次部署。

完整夜间任务还需启动交付后的有界执行/守护组件，而非把通用 workers 的无限进程恢复等同于“任务只重试一次”。HTTP 超时补试与整轮失败修复重启分别计数；整轮最多修复重启一次，再失败停止；遇 verify/captcha 记录并停止，不绕过、不等待自动恢复。

## 七、源码安装与历史数据迁移是两件事

新用户跑自己的任务不需要原开发者的历史库。若明确需要迁移原会话和报告，应另作受控数据备份包：一致性备份整个 data 中相关 SQLite、outputs 实体目录、路由登记和必要的任务配置。`data/outputs` 若是符号链接，必须包含实际目标，并在新机重建正确路径。

历史账号加密数据需要对应密钥才能解密；不能把账号库与 crawler_auth.key 一起上传公开 GitHub。默认清除账号凭据、由接收者重新登录。模型密钥、cookies、浏览器 profile、真实个人数据和本机 .env 不属于公开源码包。

不要直接复制运行中的 SQLite 主文件而漏掉 WAL；使用 SQLite backup API 或一致停机备份。仅备份 audit_index.sqlite3 不足以保留对话，还依赖 investigation.sqlite3、investigation_creation.sqlite3、证据文件及 registry。

## 八、完成交付的验收标准

- 干净机器只凭仓库、明确列出的外部依赖和自己的凭据能够启动，无开发者绝对路径。
- 单条模式真实只采集/分析1帖；批量模式按每词上限推进，去重和评论上限正确。
- 新会话可见，任务路由不串库，评论依据可打开，报告及统计与全部保存结果一致。
- K 冻结模板和模型参数真实生效；超时及格式反馈补试有界；验证码/失败退出生效。
- 停止/重启不丢历史数据、不自动复活已停止任务；前台正常不代表 worker 正在采集。
- 安装和依赖、空库初始化、实际模型请求、任务执行、结果入库及报告展示均验收通过后，才能称为“别人下载后可完整复现”。

当前结论：f6d19f8 是本机已集成的功能基线；以上缺项补齐并做干净环境验收之后的新提交，才应成为 GitHub 的正式可安装发布基线。

## 九、Hermes 的交付方法

Hermes 是需要安装的外部 Agent 运行时；本项目的 `backend/hermes_runtime` 适配层及 `hermes_m0` 业务代码也必须一起提供，不能只安装 Hermes 就认为项目齐全。

已记录的运行时是 `hermes-agent==0.20.4`，`requirements-hermes.txt` 固定来源：

```text
hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@e624e9fde561e1add9388384012b295fde669ade
```

建议发布方式：保留固定源码依赖，另在许可允许范围提供从核验源码构建的非 editable wheel，附版本、源提交、构建方法及 SHA-256，方便安装失败时使用同一产物。不要用最新版替代固定版本，不要把开发者其他项目的 `.venv` 或绝对 PYTHONPATH 当成依赖交付。

历史运行时清单明确说明：曾因 GitHub 获取失败使用哈希核验的缓存 wheel；固定源码与新安装环境的一致性不能仅凭版本号推断。因此发布前应实际从上述提交构建/安装，检查关键文件哈希、`pip check`、Hermes 适配层测试，以及真实 M3 对话的资源生成和工具调用。若源码与缓存产物不一致，应登记差异并建立新的可复现产物，不能隐瞒差异。

本版优先使用已验证过的 Python 3.12 环境；Hermes 清单声明范围是 >=3.11,<3.14，范围内其他组合仍需验证。各环境使用自己的持久化 HERMES_HOME，demo 与 production 分开；不分发旧会话缓存、模型凭据或个人配置。Hermes 自身的模型配置也需由初始化和预检覆盖，不能假定应用的某个密钥自动满足所有运行时要求。

对应资料：`docs/product-baseline-r0/HERMES_RUNTIME_MANIFEST.md`、`dependency-manifest.md`、`hermes-clean-room-report.md`。

## 十、MediaCrawler 的交付方法与当前实际差异

MediaCrawler 是通过 CLI 调用的独立采集器，不是 `pip install MediaCrawler` 就能替代的组件。建议发布一个本项目适配过的、固定提交的源码依赖，放在 `external/MediaCrawler`（子模块或合法的随包源码），保留它自己的虚拟环境与锁文件。

**本次只读核对发现两套来源不同：**

| 来源 | 状态 |
| --- | --- |
| 当前主项目 .env 实际指定的 MediaCrawler | 原目录 `projects/MediaCrawler`；HEAD `ec56ebfe638dcfc8680f41711215ee9618a843e2`，有多项已修改及未跟踪源码 |
| 已整理的可复现源码快照 | `MediaCrawler-product-baseline-r0-1`；提交 `a77d8f4ad99b692641711c8e170c73dc7ebdf627`，本次检查工作区干净 |

干净快照已有账号状态注入、限速、CLI 参数及相关平台适配，具体见 `media-crawler-dependency.md` 与 `media-crawler-migration-matrix.md`。但不能据此宣称它等于本次真实运行的原目录。**发布前必须比较当前 CLI 实际依赖文件，迁移缺失的必要修改并做真实单帖验收；之后固定最终采集器提交。** 不应把所有原目录脏改动和凭据整包上传，也不能只交付 ec56ebfe 这个 HEAD 丢掉未提交修改。

发布包至少记录：可访问的采集器仓库 URL、固定提交、许可证和第三方声明、所需补丁、requirements/uv.lock、Node 依赖与浏览器版本、安装方法。若使用子模块，接收者必须有对应仓库访问权限，提交也必须已经推送到可访问远端。没有实际仓库地址前不要虚构 clone 命令。

接收者配置示意（路径在本机初始化时生成）：

```dotenv
MEDIACRAWLER_DIR=/ABS/audit-agent/external/MediaCrawler
CRAWLER_LOGIN_PYTHON=/ABS/audit-agent/external/MediaCrawler/.venv/bin/python
```

应用适配器调用其 main.py/CLI，通常不需要额外启动 MediaCrawler 自带 API 服务。按锁定采集器版本安装其 Python 依赖、需要的 Node 依赖和 Playwright Chromium；具体安装命令随最终选定版本交付并验证。不得复用原开发者的浏览器用户目录、cookies 或登录状态；接收者自行登录，由项目管理账号状态。

单条/批量共用同一个固定采集器源码，数量由应用任务配置控制，不需要为演示修改采集器源码。两个后台若共享采集器，要保留跨进程串行锁。当前适配器使用 fcntl，因此 Windows 原生部署也不能直接宣称支持；需要替换锁与进程管理并另行验收。

## 十一、旧版启动说明如何借鉴

用户提供的 dc53807 / m3-frozen-baseline 文档仅作启动器设计参考，不能替代 f6d19f8 或后续发布版本。建议保留：

- `check/start/status/stop` 统一入口，路径绑定、配置优先级明确，避免继承终端残留环境。
- 校验真实代码提交、数据目录、Python、代理目标、所选模式及模板身份，普通 HTTP 200 不算验收。
- 同环境重复启动幂等；未知进程占端口就失败，不抢占、不杀进程、不自动换端口。
- 同一数据目录限制一个 worker；前端配置只含代理等公开参数，不加载服务端密钥。
- 只读预检不发起模型调用；真实单帖验收独立执行。

发布版本需要补上旧启动器没有的空库初始化；A/B 检查应仅在导入演示包后适用。必须分别说明前台交互启动（关闭终端是否停止）与后台持续任务的进程生命周期。旧版 Ctrl+C 关闭全栈的语义不能写成当前 launchd 后台模式的行为。固定路径应在接收者本机生成，而不是继续绑定原开发者目录。


## 最新依赖与代码登记

2026-09-10 再次核对：主应用基线 `f6d19f8e82a9cf0944c7b11ffbcbfe8c15cc5bd8`，包含 e364a23 的评论融合边界修复，并新增视频审核短规则编号、严格还原、错误响应留存和一次反馈纠错。适用范围及验证边界见 `docs/video-rule-codes.md`；K 业务标准保持不变。

MediaCrawler 已上传 https://github.com/Vicenta-cc/media-crawler 的 main，固定提交 `5f428d1071522ce1e011ceedeceffaa858404949`。已包含原运行目录的源码修改、补充忽略规则及接入说明，23项测试通过，账号和采集数据未上传。前文“采集器尚未交付”的历史待办由此更新为：依赖源码已交付，主应用还需接入自动安装/固定引用并完成干净机器验收。本机实际采集目录未切换。

两者是不同仓库的提交，不能互相替代。单条默认交付范围不变；本地已集成主应用不等于主应用已上传或可空库一键安装。
