# 2026-10-19 部署前核对：版本、配置边界、依赖与参数

> 最新状态以[候选收尾记录](CLOUD-CANDIDATE-CURRENT-20261019.md)为准；下文保留此前计划和历史盘点，不能作为当前已验收状态。

本记录保存发布准备时的本地盘点和离线修复快照；2026-10-19 随后已开始部署执行，补丁提交与云端只读盘点见 [执行记录](DEPLOYMENT-EXECUTION-20261019.md)。尚未安装云端候选依赖、重启 3499、扫码或真实采集。部署主计划见 [云端候选部署计划](CLOUD-CANDIDATE-RELEASE-20261019.md)。

## 版本与验收边界

| 对象 | 已核实状态 |
| --- | --- |
| Audit 远端 `release/2026-10-16` | `709c3929c5c584d3b596b83b813f7555e14c913d`，包含 `9a0ded4` 及发布时间修复、回归与本地验收文档 |
| Media 远端 `release/2026-10-16` | `32729e8b06cbf11f6aa153085852e3a44c1d05d8` |
| Media 业务源码 | `7bd9d117cd1b0753990ba754c58b8c12de8f4ca5`；至 `32729e8` 仅 README、交接文档、冒烟 JSON 有变化 |
| 本机 API/worker manifest | 仍固定 Audit `16fca47453468fe87c4bee3226eb472d2ad839f6` / Media `7bd9d11`；本轮不加载修复 |
| 本轮发布入口修复及文档更新 | 基于上述已推送起点的本地变更；不包含在 `709c392` / `32729e8`，提交并审核后须重新冻结最终交付 SHA |
| 云端实际版本、参数、依赖及资产 | 本轮未连接核实；不能用本机值代替 |

历史 `1dadd47`、`db5d57f` 的测试结果保留为历史证据，不移动旧 tag，也不扩大为最终候选的 Linux 验收。最新本地真实调查记录见 [3499 实测](LOCAL-3499-ACCEPTANCE-20261017.md)。

## 云端独立前端补丁补核

后续按用户提醒核对本地历史发布收据，发现 2026-10-14 的独立前端补丁 **尚未合入当前 `709c392`**。该项是后续新增发现，不在前述发布入口修复的已完成范围内。

- 发布目录标识：`analysis-record-link-20261014-e991e84c`；基于 `a4997d3969beab3d499a218e7a7aee025fea46d9`，补丁摘要 `e991e84ce42fe6171fc981cc62639cb7e18ff30d2cf7504f2cbf1fd60b31ebb0`。当时后端 SHA 未变，因此仅比较后端 Git 祖先关系会漏掉该补丁。
- 行为：分析记录链接使用会话 ID；按指定 Run 查找对应历史轮次；兼容旧 Run 链接，保留 Run / ReportVersion 一致性与授权失败拦截，不能在失败时转到最新轮次。
- 涉及 `InvestigationCenterArea.tsx`、`AnalysisRecordsPage.tsx`、`m3AnalysisRecords.ts` 及新增 `tests/analysisRecordTarget.spec.ts`。本轮逐个核对保存源码与发布 manifest 的 SHA256，四文件均匹配；移植前的 `709c392` 未包含前三处补丁及新增测试。
- 本地证据目录：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/frontend-audit-provider-hotfix-20261014`。历史收据记录 30 项逻辑测试、类型检查、构建及线上旧链接浏览器验证通过；不能作为当前候选回归结果或当前线上状态。

本地已保留其他修改，单独移植三处源码并新增 18 项目标解析回归（含历史 7 项），纳入默认逻辑测试入口。完整逻辑回归 50 项、类型检查及生产构建通过，保留既有大包体积提示；独立代码 review **APPROVE**，架构 review **CLEAR**，两位审查者各自重跑目标解析 18 项通过。该补丁已单独提交为 `be5ca8ffab13975b41080dae5e33060d4690bd23`，父提交 `f918dd68b6072292ff74c8e2d1ec6215555d94b0` 为 dotenv 修复；`709c392` 保留为旧远端起点。详细收据见 [前端补丁移植记录](FRONTEND-ANALYSIS-LINK-HOTFIX-20261019.md)。未部署线上、重启 3499 或进行真实采集。

## 发布入口禁止隐式 `.env` 补值

问题：`deploy/camoufox/runtime-support/serve.py` 清空继承环境并安装 manifest / 私有白名单配置后，App Settings 仍会调用 `load_dotenv(ROOT / '.env')`。制作发布资产时遗留的 `.env` 可能被一并打包、哈希认可，并补入未经过逐键白名单审核的模型、ASR、数量或鉴权设置。资产树哈希仍然有效；这不是绕过哈希检查的结论。

修复：当 `CAMOUFOX_RELEASE_MANIFEST` 存在时，App Settings 不调用 checkout 的 dotenv 加载；不依赖 `python-dotenv` 是否支持 `PYTHON_DOTENV_DISABLED`。API 和 worker 共用真实 `configure()`，都会在导入 Settings 前设置该 manifest 标记。受限爬虫子进程原有禁止加载规则保留；普通未受管开发入口仍可按原方式加载 `.env`。

离线回归使用临时 checkout、真实 Settings、真实 API/worker 启动脚本和带干扰项的 `.env`；仅替换资产检查和服务主循环，不启动服务或浏览器。覆盖：

- API / worker 的缺失参数不能从 `.env` 补值，尤其不能由其关闭鉴权或把检索词改为 2。
- 审核过的私有文件可以为两入口提供显式业务配置。
- 普通开发入口的 `.env` 加载保留，受限子进程仍拒绝加载。

旧实现先复现 API、worker 两个入口失败，修复后通过。最终离线验证：

- 发布工具测试 **58 项通过，零跳过**，其中新增配置边界回归 5 项。
- 子进程与固定运行边界回归 **19 项通过**；出现一项既有 Starlette `python_multipart` 弃用警告。
- 独立代码 review：**APPROVE，未发现确认缺陷**；独立架构 review：**CLEAR，无架构阻断**。两位审查者各自重跑新增 5 项测试并通过。
- Python AST、两仓库 `git diff --check` 和四份 Markdown 本地链接检查通过。

复现发布测试时，使用固定 App Python，并将 `CAMOUFOX_CONTRACT_CRAWLER_REPOSITORY` 指向实际 Media checkout，避免相关测试因缺少路径跳过：

```sh
env CAMOUFOX_CONTRACT_CRAWLER_REPOSITORY=/Users/ext.wanghongtao6/Documents/Codex/projects/MediaCrawler-camoufox-runtime \
  PYTHONDONTWRITEBYTECODE=1 \
  /Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-local/venv-app/bin/python \
  -m unittest discover -s deploy/camoufox/tests -v

env PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:backend \
  /Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-local/venv-app/bin/python \
  -m pytest -q tests/test_crawler_child_runtime.py tests/test_fixed_runtime_boundaries.py
```

以上命令从本次 Audit 工作区执行；这些 Mac 本地路径仅供复现，不是 Linux 部署参数。本记录与审查结论不能代替资产、Linux 沙箱或真实业务收据。

## 依赖核对

Media 两种安装入口尚不等价：

| 项目 | requirements.txt | pyproject.toml / uv.lock 关注点 |
| --- | --- | --- |
| Pillow | `12.1.0` | pyproject 和 uv.lock 为 `9.5.0`；本机实测 venv 使用 `12.1.0` |
| aiosqlite | `==0.21.0` | `>=0.21.0` |
| pyhumps | `==3.8.0` | `>=3.8.0` |
| opencv-python | 无版本约束 | `>=4.11.0.86` |
| 额外声明 | 未列出右栏项目 | pre-commit、websockets、asyncpg |
| Python | 源码声明 `>=3.11` | 不意味着每个更高版本都有匹配 wheel 或已验收 |

Audit `requirements.txt` 含范围依赖；`requirements-hermes.txt` 还含固定 SHA 的 Git 依赖。现有清单均不能直接作为已验证的完整离线哈希锁。App 与 Media 的 pydantic、python-dotenv 等版本不同，应保持独立 venv，不能合并安装以消除差异。

部署构建要求：

1. 选定并验证 Media 的唯一发布依赖来源，保留选择理由，不只改 Pillow。此次仅核对，未擅自升级、降级或删除依赖。
2. 在目标 Linux x86_64 / Python 3.11 构建环境生成两套传递依赖齐全的哈希锁和 wheelhouse；Git 依赖及只有源码发行包的包先构建 wheel，再记录最终 wheel 摘要。不得将 VCS URL 直接当作可离线安装的 wheel。
3. `deploy/camoufox/build.py` 用调用它的 `sys.executable` 创建两套 venv，故执行时明确目标解释器；venv 在最终绝对位置创建，不搬运 Mac venv。
4. 安装使用 `--no-index --require-hashes --only-binary=:all:`，两套 venv 分别 `pip check`；Media 核对 Camoufox `0.5.8` / Playwright `1.62.0`。浏览器、指纹模型及系统库另行匹配和校验。

本轮没有制作 Linux wheelhouse 或确认该依赖组合能够在云端成功安装，这些仍为候选构建门槛。

## 参数核对

以下本地值来自仓库外运行 manifest 的非敏感字段及只读数据库查询；不得整份复制本地配置。表中代码默认只供比较，**不是批准的生产值**。

| 参数 | 代码默认 / 规则 | 本地 manifest 覆盖 | 候选正式值 |
| --- | --- | --- | --- |
| `SEARCH_TERMS_MAX` | 10 | 2 | 待生产盘点后锁定 |
| `TRIAGE_CANDIDATES_PER_KEYWORD` | 10，范围 1–30 | 未覆盖，沿用 10 | 待锁定 |
| `TRIAGE_CANDIDATE_COMMENTS` | 60 | 10 | 待锁定，并核对统一评论设置 |
| `M3_COMMENTS_PER_POST` | 300 | 10 | 待锁定，并核对数据库 |
| `INVESTIGATION_MAX_POSTS` | 10 | 30 | 待锁定，并核对数据库总帖上限 |
| `TRIAGE_ACCOUNTS_PER_TASK` | 3 | 1 | 待账号/线路容量验收 |
| `CRAWLER_MAX_CONCURRENCY` | 3 | 1 | 待账号/线路容量验收 |
| `TASK_EXECUTION_CAPACITY` | 3 | 1 | 待资源测算及验收 |
| `TASK_ANALYSIS_CAPACITY` | 3 | 1 | 待资源测算及验收 |
| `TASK_WORKER_PROCESSES` | 3 | 1 | 待资源测算及验收 |
| 新任务每词精审 | 确认服务和存储层显式固定为 1 | 1 | 保留规则，不能作为测试开关调大 |

历史多轮分析记录另须核对 `INVESTIGATION_MULTI_ROUND_ENABLED` 及会话轮次成员数据：workspace 只有启用该能力才投影 `rounds`。该补丁对缺失轮次保持拒绝；候选测试需覆盖开关生效、旧轮次返回及跨用户拒绝，不以 HTTP 模拟测试代替真实权限验收。

本地 `audit_index.sqlite3` 的 `task_settings` revision 2：`max_notes=1`、`max_total_notes=10`、`max_comments=10`、`max_items_per_minute=5`、`max_concurrency=1`、`analyze_limit=1`、`analysis_batch_size=5`、采集评论/媒体开启、二级评论关闭。已确认任务还携带冻结快照；账号新登录保护可进一步降低速率。环境、数据库和任务快照应分别核对，不能仅更新一个环境变量便宣称生效。

`M3_POSTS_PER_KEYWORD` / `M3_ANALYZE_LIMIT` 是本地 manifest 的历史项目，当前 backend 未找到相应读取；不得用它们解释新任务每词数量或分析上限。排除候选中的无效项目，不通过修改数据库硬改旧冻结任务。

本地非敏感模型配置：

| 阶段 | 本地核对值 | 候选验证要求 |
| --- | --- | --- |
| 普通对话、资源生成 | `deepseek-v4-pro-ga-260813` | 两条真实业务路径均核对模型身份与成功响应 |
| 文本、视觉主模型 | `qwen3.7-plus` | 分阶段核对，不能由对话模型推断 |
| 图片、contact sheet、预筛 | `qwen3.6-flash` | 核对最终解析值与请求收据 |
| ASR | 启动器强制 Dolphin、远端模式、语言 auto | 从候选主机验证实际远端服务和超时 |
| ffmpeg | 本机固定绝对路径 | 换成 Linux 已核验路径，验证完整视频解码 |

本机配置还含旧 CORS 来源及放宽 Secure Cookie 的值，不能移植。生产应显式 `APP_AUTH_MODE=required`、HTTPS Secure Cookie、准确 HTTPS Origin、关闭直连降级并保留账号/线路/风险保护。

## 私有配置来源与维护顺序

所有业务参数需逐项映射到 manifest 的允许字段或 `parent_env_files.reviewed_allowlist`，由同一受限配置供 API/worker 使用。配置文件仅记录受审路径、权限、所有者、哈希和键名；不将密钥、Cookie、代理凭据或私密 URL 写入本文或公开收据。

必须核对的组：鉴权与加密 Key 的受限引用；对话/资源生成/审核/报告模型与凭据；ASR；`FFMPEG_PATH`；`HERMES_HOME`；outputs、资源锁及持久化路径；上述数量与容量；登录 Origin、账号代理/安全和超时。云端实际值未知，表中空缺是发布前待办，不通过 `.env` 静默补齐。

候选环境先与正式环境隔离。准备真实迁移与基础健康证据后，按 `maintenance.py` 的 release/migration/health/operator 收据契约，仅受控解除候选维护，重启候选 API/worker 并验证准入，再测试扫码和采集。不能一直保持候选维护为 true 却要求它完成登录，也不能为测试解除旧生产保护。

维护模式下，既有受控账号迁移流程仍可凭匹配账号、owner、migration 和 operation 的授权执行迁移登录；上述顺序针对普通候选登录及采集验收，不取消或绕过该迁移授权例外。恢复真实账号时使用迁移服务的正式入口，不伪造这些身份。

`maintenance.py` 和 `cutover` 都不会替操作员重启服务。正式切换前仍须列清实际 API/worker 单元、停写证明、备份/恢复演练、重启顺序和验收命令；示例单元名不能直接当作现场清单。原计划中的独立候选、多轮业务、跨报告、多用户权限和带新增数据的回退演练继续保留。

## 证据定位

- 发布边界：`deploy/camoufox/runtime-support/serve.py`、`worker.py`、`backend/audit_agent/config.py`、新增 `deploy/camoufox/tests/test_release_dotenv_boundary.py`。
- 构建：`deploy/camoufox/build.py`、`README.md`；App `requirements.txt`、`requirements-hermes.txt`；Media `requirements.txt`、`pyproject.toml`、`uv.lock`。
- 参数：`backend/audit_agent/config.py`、`task_settings.py`；`backend/investigation_creation/service.py` / `store.py` 的确认路径；`scripts/audit_runtime.py` 的受控环境。
- 本机只读来源：`~/.codex/runtime/media-merge-login-smoke/runtime.json` 与 `data/audit_index.sqlite3`，仅用于复核本地覆盖，不是生产配置交付物。
