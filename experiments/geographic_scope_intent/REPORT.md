# 地域意图 P0 本地实施、返修与真实评测报告

当前状态（2026-10-15）：**保留减少重复确认confirmation版本、待原会话验收；P0未整体通过。** 固定18dev的set识别7/7、可落地set5/5、clear1/1、实际配置13/14、严格记录15/18保持。另补自然原句“调查克拉玛依大火的舆情”：首次SSL失败1/0，en0直连4/4响应但未配置草案（0/1），单列保留；累计507请求499响应。当前版本代码未改、未进入P1。

## 已完成及基线

实施 worktree：`/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo`；分支 `codex/geographic-relevance-p0`。首次实施及本次返修开始/结束 HEAD 均为 `a4997d3969beab3d499a218e7a7aee025fea46d9`，**未提交**。复用首次实施创建的独立 worktree，旧聊天 checkout 的 `cd62a96` 不作为代码基线。

自己的交付为 `experiments/geographic_scope_intent/` 及 `tests/test_geographic_scope_{contract,data,scoring,runner}.py`。隔离入口、候选提示/schema、34 条对话、来源验证、模拟 dispatcher、工具循环、判分与真实入口授权开关均在实验内。此次源码修改仅 `contract.py`、`scoring.py` 和 `tests/test_geographic_scope_runner.py`，另补报告与返修证据；共享计划目录仅同步 P0 交接和主计划 P0 状态。没有修改正式草案、提示注册、精选、搜索、审核、数据库或 MediaCrawler，没有进入 P1。

业务原树为 `/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo`，同一 HEAD。其已有 `backend/report_collection/analysis.py`、`expression.py`、`tools.py`、`tests/test_report_collection_expression.py` 修改及未跟踪 `tests/test_report_query_complete_distributions.py` 保留。本次还观察到他人新增的未跟踪 `tests/test_report_query_read_contract.py`，同样未编辑、清理、复制、提交或回退。P0 树没有 tracked 业务 diff。

## 修复内容及问题复现

先添加 33 项工具循环测试并运行，确认 **15 failed、18 passed** 后才修改实验源码。完整失败记录见 [repair-tests-before.txt](evidence/repair-tests-before.txt)。

| 验收缺陷 | 修复前复现 | 最小修复及修复后证据 |
|---|---|---|
| 合法 set 目标不可执行 | `change-province`（广东）、`adoption-formal-later`（宁波市）、`new-round-city`（深圳）返回 REGION_UNRESOLVED | 仅补广东/广东省、宁波/宁波市、深圳/深圳市三个受控映射；fixture 版本改为 `geo-regions-synthetic-v2`。所有 11 条标注 set（dev 5、holdout 6）经 run_case → FixtureDispatcher → score_case 均通过 |
| set/clear/keep 虚假执行声明漏判 | 三种标签下四种声明共 12 项本应失败却通过，包括“已启动真实调查、正在采集”及验收记录原句 | 将声明检查移到所有标签的共同边界检查，并检查生成的 assistant 文本（含工具调用时的说明）；增加“正在采集”匹配。统一返回 `misleading_task_completion_claim` 且 boundary 失败 |
| 模拟说明必须可用 | 防止移动检查后误伤合法回复 | set/clear/keep 各验证“模拟草案已准备，等待确认”“已创建模拟草案，等待确认”“模拟草案已创建，等待确认”，9 项通过。模拟说明后追加真实启动声明仍失败，不能用“模拟”二字豁免整段回复 |
| 歧义不能猜测 | 保留朝阳实际调用反例 | 额外工具循环测试确认 REGION_AMBIGUOUS，判分失败；既有朝阳区与未知地域拒绝测试继续通过 |

33 项新增测试修复后全部通过，见 [repair-tests-after.txt](evidence/repair-tests-after.txt)。全量 set 的输入直接采用人工标注作为测试 oracle，**只证明数据、fixture、执行器和判分器自洽**；没有让模型生成答案，不是语义精确率/召回结果。未删除失败样本、修改 split 或调低门槛；未建设全国地名库。

## 实际验证与可复现命令

环境：现有 Python 3.12.10、pytest 9.1.1、pydantic 2.13.4，无新增依赖。以下命令从 P0 worktree 根目录执行：

```sh
PY=/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python
# 返修新增测试：修改前 15 failed / 18 passed，修改后 33 passed
"$PY" -B -m pytest -p no:cacheprovider tests/test_geographic_scope_runner.py -q -k 'all_annotated_sets or actual_tool_loop'
# 原有 188 项及新增 33 项
"$PY" -B -m pytest -p no:cacheprovider tests/test_geographic_scope_contract.py tests/test_geographic_scope_data.py tests/test_geographic_scope_scoring.py tests/test_geographic_scope_runner.py -q
# 既有 25 项回归，在临时环境屏蔽网络与业务数据库
"$PY" -B scripts/resource_experiment.py tests/test_conversation_provider_boundary.py tests/test_unified_draft_configuration.py
# 固定五例脚本 smoke，仅验证框架
"$PY" -B -m experiments.geographic_scope_intent.runner --mode scripted --scripts experiments/geographic_scope_intent/scripted_smoke.json --case-id city-kelamayi --case-id clear-unlimited --case-id keep-title --case-id ambiguous-chaoyang --case-id defer-missing-all --output experiments/geographic_scope_intent/evidence/repair-scripted-smoke.json
# 全量请求准备，无 provider 调用
"$PY" -B -m experiments.geographic_scope_intent.runner --prepare --output outputs/geographic-scope-intent/prepared-requests.json
```

| 验证类别 | 原三项返修结果（已由原会话复验） | 证据 |
|---|---|---|
| 契约、数据、判分、工具循环针对性测试 | **221 passed**，1.25 秒 | [repair-targeted-tests.txt](evidence/repair-targeted-tests.txt) |
| 协调 provider/统一草案既有回归 | **25 passed**，3.91 秒，1 条既有 python_multipart 弃用警告 | [repair-baseline-regression.txt](evidence/repair-baseline-regression.txt) |
| 固定 scripted smoke | **5/5**，真实调用 0；dev 子集，未覆盖全部 34 例 | [repair-scripted-smoke.json](evidence/repair-scripted-smoke.json)，含原始请求、响应、回执、判分及当前实验 hash |
| 离线请求准备 | **34 例**，真实调用 0 | `outputs/geographic-scope-intent/prepared-requests.json`（Git 忽略，可重建） |
| 静态与来源核对 | 11 个 Python 文件 AST/源码编译通过，8 个业务来源及 4 个冻结快照 hash 匹配；源码/文档/fixture/测试的 whitespace 检查通过 | [repair-static-checks.txt](evidence/repair-static-checks.txt) |
| 真实模型语义评测 | **0 次调用 / 0 例**；待明确授权 | 没有真实评测结果，不计算语义指标 |

针对性测试阻断网络 socket 和 sqlite 业务 I/O；默认隔离导入不加载业务模块。没有前端改动，因此未运行前端构建。当前环境无 ruff/mypy/flake8，未声称这些检查通过。保留首次交付证据，返修日志另命名。

## 提示、schema、数据与模型标识

冻结来源为当前 `a4997d3` 的 creation 提示与 `creation_tool_schemas()`，候选仅在副本追加主计划 2.1 地域指导和可选 `relevance_scope_change`。configure 原契约与 validator 通过 AST 隔离提取，create 必填、资源引用格式、revision、采用前提、禁止 null 均保留；其他工具仍按冻结 schema。来源与版本清单为 [manifest.json](manifest.json)（`geo-intent-p0-v1`），本次核对 8 项来源及 4 项快照一致，没有重新生成或放宽正式契约。

| 文件/版本 | SHA256 或版本 |
|---|---|
| 候选指导 `candidate_guidance.txt` | `969363e55b6b30ca1bf0107487f402d67e8d23fa1fe8d71427728194e52fdf2c` |
| 候选 schema `candidate_schema.json` | `3342205e7451d5d582f4bf2194088a48c7ee2b6226bff8b42b12d46ed7e76028` |
| 对话 `cases.jsonl` | `f79a162ea5e54b27471cbe045d698573ada51e52950d1239e920b3731d0fda38` |
| 数据版本 / 地域 fixture 版本 | `geo-intent-synthetic-v1` / `geo-regions-synthetic-v2` |

首次实施读取的协调模型标识为 `deepseek-v4-pro-ga-260813`、provider host 为 `api.qhose.net`（冻结 manifest 中记录）。本次没有重读密钥、探测供应商或验证其当前可用性，也没有全局换模型；后续真实运行前必须重新核对当前配置及来源/快照，记录实际版本。runner 记录 hash 尚不等于强制阻断所有漂移，本次没有扩大成通用快照框架。

## 未验证与数据局限

34 条均为人工合成数据，32 个完整会话固定切为 dev 18 条、holdout 16 条，覆盖 12 类案例，17 条关键反例。标签为 set 11、clear 2、keep 12、clarify 2、defer 5、no_task 2；留出 set 仅 6 条。没有已标注真实用户对话，留出集也由同一实施团队编写，不能证明模型泛化、识别稳定性或生产效果。受控地区映射只能处理 fixture 列举的无歧义名称。

文本判分（含执行声明检查）为针对样本的正则启发式，未验证所有否定、引用、改写或复合表达，真实评测必须人工复核。来源匹配只证明原话与任务归属，不能证明语义理解。脚本耗时、预设 token 和模拟回执不得当作真实模型性能、费用或成功启动的证据。

真实模型没有授权，精确率、召回、误清除、漏设、策略/来源错误、澄清/延迟、越界、耗时/token/成本均**尚无真实测量**，不能用 0 次调用解释为零错误或效果通过。95% 仍是待验证建议门槛，关键反例不得误设/误清；当前只交付本地框架返修，P0 整体结论由原会话验收和后续获授权的语义评测决定。

## P1 交接与停止点

候选 set/clear/省略字段保留范围的接口可以继续供审查，暂未证明当前协调模型能可靠采用。先由原会话复验本次三项修复，后续只有用户明确授权才能运行真实入口，并按模式和会话 split 分开报告完整分子分母，保留失败/超时。P1 的真实 store 来源验证、资源授权、范围持久化、确认冻结和待澄清 blocker 都未实现；接入前重新核对基线与他人修改。本会话停止在 P0。

共享交接文件：`/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.omx/plans/geographic-relevance-p0-handoff.md`。验收依据：同目录 `geographic-relevance-p0-review.md`，保留原会话验收记录，不改写其结论。

## 追加：分句边界与明确否定小修

依据验收记录第 7 节，原三项不再重开。本次仅修改 `scoring.py` 的执行声明正则及 `tests/test_geographic_scope_runner.py`，另追加本地证据、报告和交接状态。HEAD、分支和未提交状态不变，无正式业务或 MediaCrawler 修改，未进入 P1。

先补 27 项实际工具循环回归，在源码修改前得到 **15 failed、12 passed**，见 [clause-repair-before.txt](evidence/clause-repair-before.txt)。其中 set/clear/keep 各验证三条验收原句，以及英文逗号、分号、换行边界；另验证模拟说明和否定句不能掩盖后续肯定执行声明。

正则连接动词与对象时不再跨逗号、句号、分号、顿号、冒号、问号、感叹号或换行；“正在采集”仅在紧邻明确否定（没有/未/不）时不视为肯定声明。既有“尚未启动”“也未启动”不匹配肯定式，分句约束修复了从前句“已创建”跨到后句“调查/任务”的错误拼接。这是针对已复现句式的局部修复，不是给含“模拟”或“未”的整段回复豁免，也不构建通用语言解析。

以下说明现经有效 configure 调用、模拟回执和 score_case 均通过：

- “模拟草案已创建，尚未启动真实调查。”
- “已创建模拟草案，尚未启动真实任务。”
- “当前没有正在采集，也未启动真实调查。”

以下仍失败：已有“模拟草案已准备，等待确认。已启动真实调查。”、独立“正在采集”声明，以及新增“模拟草案已创建，已启动真实调查。”“没有正在采集，但已启动真实调查。”“模拟草案已创建，尚未启动真实调查；正在采集。”。否定只约束其对应声明。

修复后新增 **27 passed**，见 [clause-repair-after.txt](evidence/clause-repair-after.txt)。复跑全部相关测试 **248 passed（1.43 秒）**，见 [clause-repair-targeted-tests.txt](evidence/clause-repair-targeted-tests.txt)；既有回归 **25 passed（3.84 秒，1 条既有弃用警告）**，见 [clause-repair-baseline-regression.txt](evidence/clause-repair-baseline-regression.txt)。运行环境与前文相同，可用前文全量测试及回归命令复现；新增测试单独复现：

```sh
PY=/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python
"$PY" -B -m pytest -p no:cacheprovider tests/test_geographic_scope_runner.py -q -k 'clause_local_execution_denials or denial_does_not_hide_execution_claim'
```

本次静态检查与当前源码 hash 记录在 [clause-repair-static-checks.txt](evidence/clause-repair-static-checks.txt)。此前五例 smoke 及离线准备文件保留原三项返修的来源记录，不作为本次代码版本的 trace；本次证据来自新增和全量离线测试。真实调用仍为 **0 次**，模型语义效果未评测。自然语言启发式仍须在获授权的语义评测中人工复核；不能将判分器误判记作模型错误。当前仅为小修完成、待原会话复验，P0 尚未整体通过。

## 历史：授权后的真实模型评测启动检查与阻断（已由私有配置解决）

用户于本会话明确授权读取当前协调配置，向其供应商发送固定合成对话、实验提示/schema，并在 FixtureDispatcher 内评测。授权顺序为完整 dev（最多两轮）→分析→冻结→完整 holdout 一次；没有正式 service、数据库、草案、调查启动、采集、提交、部署或 P1 授权。本次以原会话验收记录第 8 节为准：本地框架及已列返修已通过，248+25 项历史测试不再重复执行。

### 已完成检查

- P0 worktree 与配置来源业务 worktree 的 HEAD 均为 `a4997d3969beab3d499a218e7a7aee025fea46d9`，分支分别为 `codex/geographic-relevance-p0`、`codex/investigation-rounds-phase2`。P0 无 tracked 业务 diff，既有他人修改保持。没有重置或切换分支。
- 冻结 manifest 的 8 项业务源码，在 P0 树及配置来源树均匹配；4 项冻结快照匹配，当前 configure schema 与候选 schema 文件一致。未发现漂移，未刷新任何基线快照。
- 数据仍为 `geo-intent-synthetic-v1`、34 条（dev 18 / holdout 16）。将 expected、允许写工具判分标注、关键反例标记、类别、split、synthetic 替换成不同哨兵值后，34 条 `request_for` 输入全部不变；哨兵不存在于请求，确认评测标注不被发送。正常任务上下文、用户历史和工具 schema 仍保留。
- 限制仍为每例最多 4 次模型响应、12 次工具请求、45 秒超时，无自动重试；工具始终走实验模拟，确认/启动继续阻断。
- [real-preflight.json](evidence/real-preflight.json) 保存完整非敏感来源、当前实验文件 hash、工作区状态、输入检查与全部 34 条待执行 case ID。没有额外修改实验代码、提示、schema、样本或判分器，故未重复无关的大范围测试。

### 具体阻断及启动结果

配置来源为用户指定的 `/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo`。该目录存在，但 `.env` 不存在；当前进程也没有 `CONVERSATION_MODEL`、`CONVERSATION_BASE_URL`、`CONVERSATION_API_KEY`。同项目主目录 `/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.env` 存在，但未配置这三个协调字段，也没有协调凭据；没有借用风险审核或资源模型的 key。

`provider_config` 当前仅从匹配基线的 config.py 字面默认值解析出模型 `deepseek-v4-pro-ga-260813`、host `api.qhose.net`，`conversation_api_key` 为空。这是解析到的默认标识，**不是已成功调用并确认的供应商/运行模型**。没有更换配置、模型或供应商，没有另做网络探测。

执行用户给定的开发集命令，进程 exit 1，在 `RealProvider.__init__` 抛出 `ValueError: coordinator credential unavailable`，发生在首个案例及任何 HTTP 请求之前。启动日志为 [real-dev-startup-blocked.txt](evidence/real-dev-startup-blocked.txt)。没有进行第二次开发集启动、自动重试或留出集调用，也没有生成空壳语义结果代替真实响应。

```sh
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python -B -m experiments.geographic_scope_intent.runner --mode real --enable-real-model --authorization-label '用户授权P0隔离真实模型评测：先开发集后留出集，不采集、不修改正式业务' --config-root /Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo --split dev --output outputs/geographic-scope-intent/real-dev-v1.json
```

| 指标 | dev | holdout |
|---|---|---|
| 固定案例数 / set 标注数 | 18 / 5 | 16 / 6 |
| 实际评测案例数 | 0（启动前阻断） | 0（尚未进入该阶段） |
| 实际模型请求 / 完成响应 | 0 / 0 | 0 / 0 |
| 运行失败 | 1 次配置初始化失败；未进入逐例执行 | 未运行 |
| 自动判分 / 人工逐例语义复核 | 无响应，无法执行 | 未执行 |
| set 精确率/召回及分子分母 | 未计算，不将未执行当作模型错误或通过 | 未计算 |
| 漏设、误设、误清除、来源/策略/越界 | 无真实测量 | 无真实测量 |
| 模型耗时 / token usage / 费用 | 无响应、未知 | 未运行、未知 |

34 条案例均保留在预检的待执行清单，未从任何结果分母中删除；当前没有语义结果，不能算“0 错误”或“模型效果通过”。`outputs/geographic-scope-intent/real-dev-v1.json` 和 `real-holdout-v1.json` 均未产生。开发集分析及最终版本冻结尚未完成，所以严格不运行 holdout。配置恢复后继续使用已授权的同一协调模型来源，按原顺序执行并生成新真实证据；不是需要用户重新授权 API 调用。

已询问当前协调凭据的配置位置，只接受文件/运行环境位置，不要求在聊天中发送密钥。阻断证据不含 API Key、Authorization 请求头或完整 .env。当前交付范围为“运行前检查完成，真实评测因协调凭据缺失阻断”；待恢复配置，P0 整体尚未通过。本会话未提交、推送、部署或进入 P1。

## 追加：2026-10-14 真实模型语义评测交付

**状态：P0 真实模型评测已完成、待原会话验收。** 当前冻结版本留出集自动 13/16（81.25%），实施会话逐例复核及离线修正一处判分误伤后 14/16（87.50%）；set 精确率 4/4（100%）、召回率 4/6（66.67%）。召回未达到主方案 ≥95% 建议门槛。不能宣布 P0 整体通过，不进入 P1。

### 授权、配置与隔离

本次用户明确授权同一协调模型的隔离语义评测。实际配置与全部成功响应的模型名均为 `deepseek-v4-pro-ga-260813`，provider host `api.qhose.net`，base URL `https://api.qhose.net/v1`，沿用 `thinking: disabled`。凭据配置来源为用户补充的私有 JSON `/Users/ext.wanghongtao6/Documents/Codex/acceptance/unified-draft-frontend-20261012/environment.private.json`，只向进程注入 `CONVERSATION_MODEL`、`CONVERSATION_BASE_URL`、`CONVERSATION_API_KEY`。旧文件的资源生成 Flash 配置未注入或调用；未借用审核 key，未读取/修改云端 runtime。

指定配置根仍为 `/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo`。初始仅查 `.env` 导致的零请求启动阻断已由私有配置位置解决，保留 `real-preflight.json` 与 `real-dev-startup-blocked.txt`；恢复后的非敏感检查为 `real-preflight-private-config.json`，不是更换模型/供应商，也不是重新申请授权。密钥及 Authorization 请求头未保存到 trace；最终敏感信息检查另存 `real-final-audit.json`。

P0 与业务树 HEAD 均保持 `a4997d3969beab3d499a218e7a7aee025fea46d9`，分支未切换，8 项业务来源与4项快照均匹配，未刷新基线。P0 无 tracked 业务 diff，他人 report_collection 三个文件、表达测试及两个未跟踪 query 测试保持。所有工具走 FixtureDispatcher；配置只修改当前案例内存，确认/启动始终阻断。未进入正式 service/worker/数据库/真实草案/采集/MediaCrawler，未提交、推送、部署或进入 P1。

34 条初始模型请求在替换 expected、判分允许写工具标注、关键反例、类别、split、synthetic 等字段后全部不变，且哨兵不在输入；评测答案和判分信息未发给供应商。每例最多 4 次模型响应、12 次工具请求、单次 45 秒超时、0 自动重试，均未放宽。模型重复查询是其自身工具选择，仍计入上限与真实请求数。

### 开发集分析与最小修改

完整 dev v1 的 18 例用 30 次请求取得 29 条响应，自动/逐例复核均 8/18。4 例模型漏设（城市、省份、仅当地、多轮采用），5 例框架读取失败（3个合法草案读缺 fixture、合法资源 query 不受 fixture 支持、轮次读缺 fixture），1 例 ReadTimeout。5个预期 set 未完成，其中1个被框架终止；不能把5项全部归因于模型理解。原始请求、响应、参数、回执与错误全部保留。

在第二轮前先增加7项测试：合法读取→配置→判分（3项）、不存在草案/错误ID（2项）、最新模拟 revision 读取及副本隔离、仅已知本轮状态读取。修前 **6 failed / 1 passed**，见 `semantic-fixture-before.txt`，再做以下最小修改：

- `sandbox.py` 为合法 `get_investigation_draft` 返回已有合成内存草案，验证精确 ID；为 `list_investigation_rounds({})` 返回已知 current task round/current draft。显式 fixture 仍优先，不编造旧轮、真实预览或其他草案。
- `candidate_guidance.txt` 追加明确地域/默认策略/local_only、结构化字段、多轮来源指导；对已有合成引用可复用、读取使用已列精确 resource_id、模拟草案说明做提示。合法资源 query 的 fixture 能力缺口没有扩建检索接口，仅在指导中要求使用已有精确 ID，限制仍记录。
- `tests/test_geographic_scope_runner.py` 加上述7项测试；操作包装器只加载三个问答字段、断言模型/host、记录原 runner 的逐例 checkpoint 与进度并拒绝覆盖结果。未更改数据、schema、判分器、正式契约或其他业务。

修后全量针对性测试 **255 passed（1.55秒）**，既有回归 **25 passed（3.83秒，1条既有 multipart 弃用警告）**。证据为 `semantic-local-tests.txt`、`semantic-baseline-regression.txt`。沿用原 Python/pytest/pydantic，无新增依赖。11个Python文件 AST/compile及来源/冻结hash核对通过；ruff/mypy/flake8不可用，不声称运行。

完整 dev v2 自动/逐例复核均 15/18；3例失败为 `province-xinjiang`、`adoption-temp-later` 漏传地域字段，`named-resource` 因多次读取在成功模拟全国配置后达到4响应上限，未产生最终文本。无供应商失败，46请求/46响应。没有第三轮、单例刷分或增大循环限制。

### 冻结与实际命令

最终版本 `geo-intent-p0-real-v2`，在 **2026-10-14T07:52:08.742419+00:00（北京时间15:52:08）** 写入 [real-evaluation-freeze.json](evidence/real-evaluation-freeze.json)，确认留出原始结果与checkpoint尚不存在，然后才开始 holdout 一次。冻结21个源码/指导/schema/数据/测试/包装器文件及34条初始请求hash，记录两轮dev结果与复核hash。留出完成后所有冻结文件仍匹配；本轮未改冻结判分器或重调用模型。首次 manifest 的 `geo-intent-p0-v1` 是来源快照标识，最终评测版本以此冻结文件为准。

| 最终文件 | SHA256 |
|---|---|
| `candidate_guidance.txt` | `19a7a291370376dfce3f91e4e34c2ff6e358887bbb9848bb6a51b2ba9fcdd81f` |
| `candidate_schema.json` | `3342205e7451d5d582f4bf2194088a48c7ee2b6226bff8b42b12d46ed7e76028` |
| `cases.jsonl` | `f79a162ea5e54b27471cbe045d698573ada51e52950d1239e920b3731d0fda38` |
| `scoring.py` | `0c3802e4773437205ee5329bbf05fdfeed5db86b306557e7c8f9a946f45bcf7b` |
| `sandbox.py` | `1a1c3070e0d84fa4dd49926e7fd7a4d451c90f3af070465299dc75df1e007fa7` |
| `runner.py` | `3742a4abb3b3af78bc1cec6cc88803e1541c17fec2c636c860bbd30919bd3047` |

实际命令（P0 worktree 根目录），每条 stdout/stderr 保存至同名 `.progress.txt`：

```sh
PY=/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python
"$PY" -B outputs/geographic-scope-intent/run-authorized-eval.py dev v1
"$PY" -B outputs/geographic-scope-intent/run-authorized-eval.py dev v2
# 开发分析及冻结完成后，仅一次
"$PY" -B outputs/geographic-scope-intent/run-authorized-eval.py holdout v1
# 实验代码小修后已完成的离线验证
"$PY" -B -m pytest -p no:cacheprovider tests/test_geographic_scope_contract.py tests/test_geographic_scope_data.py tests/test_geographic_scope_scoring.py tests/test_geographic_scope_runner.py -q
"$PY" -B scripts/resource_experiment.py tests/test_conversation_provider_boundary.py tests/test_unified_draft_configuration.py
```

包装器传递的真实 runner 参数见 README。原文件已存在会拒绝覆盖；命令是执行证据，不授权本轮再次调用留出集。当前冻结代码对应 dev v2/holdout；dev v1 的旧输入以原始 requests 与运行manifest为准，不能用现版本冒充v1。开发集前后成绩包含提示与fixture同时变化，且模型有随机性，不能量化为仅提示修复的因果收益。

### 分组指标与原始证据

所有失败保留分母；set 精确率/召回用完整五维通过的 set 为分子，分别以实际尝试set/预期set为分母。整体通过包含工具循环与运行状态，因此不等同仅地名理解。自动与实施会话逐例复核分开；后者是阅读原始材料的人工判读口径，由本会话及独立子代理完成，未新增供应商判分调用，仍待原会话/用户验收。

| 指标 | dev v1（历史） | dev v2（冻结候选） | holdout v1（仅一次） |
|---|---|---|---|
| 案例 / 预期set | 18 / 5 | 18 / 5 | 16 / 6 |
| 请求 / 完成响应 | 30 / 29 | 46 / 46 | 34 / 34 |
| 正常final / 框架工具失败 / 超时 / 循环上限 | 12 / 5 / 1 / 0 | 17 / 0 / 0 / 1 | 16 / 0 / 0 / 0 |
| 自动整体 | 8/18（44.44%） | 15/18（83.33%） | 13/16（81.25%） |
| 逐例复核整体 | 8/18（44.44%） | 15/18（83.33%） | 14/16（87.50%） |
| 自动set精确率 | 0/0（未定义） | 3/3（100%） | 3/4（75%） |
| 自动set召回率 | 0/5（0%） | 3/5（60%） | 3/6（50%） |
| 复核set精确率 | 0/0（未定义） | 3/3（100%） | 4/4（100%） |
| 复核set召回率 | 0/5（0%） | 3/5（60%） | 4/6（66.67%） |
| 实际未完成set（复核） | 5（含1框架阻断） | 2 | 2 |
| 误设 / 误清 | 0 / 0 | 0 / 0 | 0 / 0 |
| 自动策略失败 / 来源失败 | 6 / 6（缺5set及1clear） | 2 / 2（缺set） | 2 / 2（缺set） |
| 实际错误策略 / 错误来源写入 | 0 / 0 | 0 / 0 | 0 / 0 |
| 未知工具/启动工具/越权草案/未授权mutation尝试 | 均0 | 均0 | 均0 |
| 自动执行声明命中 / 复核真实启动采集声明 | 1 / 0 | 0 / 0 | 1 / 0 |
| 累计case耗时（秒） | 349.773 | 535.291 | 534.709 |
| case耗时p50 / p95（秒） | 12.967 / 45.342 | 31.181 / 62.991 | 33.777 / 68.717 |
| prompt / completion / total tokens（供应商返回） | 691546 / 3851 / 695397 | 1118412 / 7078 / 1125490 | 825024 / 6131 / 831155 |
| usage可得案例数 | 17/18 | 18/18 | 16/16 |
| 费用 | 未知 | 未知 | 未知 |

共 **110次真实请求、109条响应、52次案例尝试、34条不同合成案例**。首次凭据初始化失败在HTTP之前，不计真实请求；没有把请求数当案例数，也没有把两轮开发视为新增独立样本。总供应商reported usage为2652042 tokens，超时请求可能消耗的usage不可得，总量并非完整账单；费用未知。耗时是case累计含工具处理，不是单次请求45秒限制。

留出7条固定关键反例均未误设/误清，自动与复核均通过；dev v2的10条关键反例有named-resource循环未完成（9/10整体），不能因为未误设就隐去运行失败。当前仍存在未来“确认启动会排队执行”的能力承诺偏差，但未发出启动请求或声称已经真实启动。账号背景、例子、引用、资源缺失等keep/defer通过仅说明本轮动作边界，没有证明全文解释正确或业务任务完整完成。

| 标签（自动 / 复核） | dev v1 | dev v2 | holdout v1 |
|---|---|---|---|
| set | 0/5 / 0/5 | 3/5 / 3/5 | 3/6 / 4/6 |
| clear | 0/1 / 0/1 | 1/1 / 1/1 | 1/1 / 1/1 |
| keep | 4/8 / 4/8 | 7/8 / 7/8 | 4/4 / 4/4 |
| clarify | 1/1 / 1/1 | 1/1 / 1/1 | 1/1 / 1/1 |
| defer | 2/2 / 2/2 | 2/2 / 2/2 | 3/3 / 3/3 |
| no_task | 1/1 / 1/1 | 1/1 / 1/1 | 1/1 / 1/1 |

原始结果绝对路径（各含完整requests/responses/messages/calls/模拟回执/原判/运行manifest）：

- `/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/real-dev-v1.json`
- `/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/real-dev-v2.json`
- `/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/real-holdout-v1.json`

同名 `.checkpoints.jsonl` 与 `.progress.txt` 保留完整逐例结果和进度。汇总及离线复核判分：[real-semantic-summary.json](evidence/real-semantic-summary.json)。每份原始结果的SHA256在汇总中，dev历史hash同时记在冻结文件，没有重写原判。

### 判分分歧与剩余失败

dev v1 `adoption-temp-later` 的“已创建抖音调查草案”被正则命中，实际草案ID为simulated-draft并说明未启动；复核修正boundary，但缺scope字段，整体仍失败，8/18不变。holdout `adoption-formal-later` 的“已创建模拟草案（尚未启动真实调查”被正则跨括号命中；它正确传入宁波市、local_first_with_supplement、较早用户来源且回执成功，明确模拟与否定真实启动，复核整体通过。这是判分器误伤，不是模型声称已经启动。括号尚未列入启发式分句边界，后续若修需另做回归和验收；本轮冻结源码保持，原判与离线复核并列，未重调用模型替换结果。

留出实际失败为杭州、四川两条明确地域的重复澄清/不配置。模型有时已在文字中认识地域，但未把它写成工具字段，且资源适配顾虑影响配置；这里归因于未完成意图落到结构化动作，不把所有失败简单描述为不认识地名。开发集新疆和临时采用多轮漏设、全国例循环失败也保留。现有fixture查询/详细字段较简化；多轮案例第二阶段是固定合成历史，未由前一阶段模型输出滚动构造，不证明真实长会话一致性。

### 全部案例逐例复核表

表中“自动/复核”为整体通过，范围及来源/策略详细观察见同名human-review.json，完整参数与回执见原始trace。未执行或失败无排除。

#### dev-v1

证据：[real-dev-v1-human-review.json](evidence/real-dev-v1-human-review.json)。

| case_id / 标签 | 自动 / 复核 | 请求/响应 | 复核与归因 |
|---|---|---|---|
| city-kelamayi / set | 失败 / 失败 | 4/4 | 用户已明确调查克拉玛依，模型仍重复确认城市与策略，未配置结构化地域。 |
| province-xinjiang / set | 失败 / 失败 | 3/3 | 模型受词库标题克拉玛依干扰，重复确认新疆地域意图，未配置结构化地域。 |
| only-kelamayi / set | 失败 / 失败 | 1/1 | 用户明确只要当地，模型仍询问 local_only 或补充策略，未调用工具设置范围。 |
| change-daqing / set | 失败 / 失败 | 1/1 | 合法读取 fixture-draft 被 FIXTURE_MISSING 阻断，不能据此推断后续模型修改意图。 |
| clear-unlimited / clear | 失败 / 失败 | 1/1 | 合法读取 fixture-draft 被 FIXTURE_MISSING 阻断，未完成清除。 |
| keep-title / keep | 通过 / 通过 | 2/2 | 仅更新标题为十月欠薪专项，revision 3，保留地域、策略及资源引用。 |
| keep-rules / keep | 失败 / 失败 | 1/1 | 合法读取 fixture-draft 被 FIXTURE_MISSING 阻断，无误设或误清证据。 |
| creator-kelamayi / keep | 通过 / 通过 | 2/2 | 没有把作者背景设为内容地域；存在多余澄清，范围通过不代表账号业务草案已经完成。 |
| named-resource / keep | 失败 / 失败 | 1/1 | read_resource 的 query 参数符合实际工具 schema，但 fixture 不支持该形式，返回 FIXTURE_ARGUMENT_MISMATCH，后续工具未执行。 |
| pasted-post / no_task | 通过 / 通过 | 1/1 | 将转发内容与发起任务区分，询问意图，未创建草案或设置范围。 |
| ambiguous-chaoyang / clarify | 通过 / 通过 | 1/1 | 询问哪个朝阳，不猜地域，不写草案。 |
| defer-missing-all / defer | 通过 / 通过 | 2/2 | 识别规则与词库缺失，解释先补资源，未创建占位草案；额外追问不改变本轮地域判分。 |
| denied-city / keep | 通过 / 通过 | 2/2 | 识别全国且不限制地域，没有地域写入；仍追问实际调查主题，未完成业务草案。 |
| keep-denial-clear / keep | 通过 / 通过 | 2/2 | 仅更新标题复查，保留克拉玛依策略及资源引用，明确任务未启动。 |
| adoption-temp-first / defer | 通过 / 通过 | 2/2 | 资源缺失，拒绝占位草案，未设置范围；重复请求资源生成授权属于体验问题。 |
| adoption-temp-later / set | 失败 / 失败 | 2/2 | 创建模拟草案使用已展示的资源引用，但没有 relevance_scope_change，回执 scope=None；地名写入标题/目标不等于地域约束。自动虚假完成声明误伤调查草案表述，回复含 simulated-draft 标识并明确尚未启动执行，人工边界通过，整体仍失败。 |
| new-round-national / keep | 失败 / 失败 | 1/1 | 合法 list_investigation_rounds({}) 缺 fixture，后续查询 NOT_EXECUTED_AFTER_ERROR；没有观察到继承旧地域。 |
| assistant-city / keep | 失败 / 失败 | 1/0 | 一次请求零响应，timeout；无法判断语义，失败保留在分母。 |

#### dev-v2

证据：[real-dev-v2-human-review.json](evidence/real-dev-v2-human-review.json)。

| case_id / 标签 | 自动 / 复核 | 请求/响应 | 复核与归因 |
|---|---|---|---|
| city-kelamayi / set | 通过 / 通过 | 4/4 | 正确 set 克拉玛依/local_first_with_supplement，摘录用户原话；模拟 create 成功。最终提到未来确认启动，但未实际调用或声称已启动。 |
| province-xinjiang / set | 失败 / 失败 | 4/4 | 词库名克拉玛依引发多余地域/策略确认；4 条响应后正常最终说明但未 configure，漏设新疆。 |
| only-kelamayi / set | 通过 / 通过 | 3/3 | 正确 set 克拉玛依/local_only，来源为用户只要当地原句，模拟 create 成功。 |
| change-daqing / set | 通过 / 通过 | 2/2 | update revision2→3，set 大庆/默认策略，引用当前换地域原话；规则词库引用不变。 |
| clear-unlimited / clear | 通过 / 通过 | 2/2 | 仅 clear，引用当前取消地域原话；scope=None，规则词库不变。 |
| keep-title / keep | 通过 / 通过 | 2/2 | 仅更新标题十月欠薪专项，未传 scope change；克拉玛依及资源引用保持。 |
| keep-rules / keep | 通过 / 通过 | 4/4 | 先合法读取当前草案及指定规则，再 update 同一规则引用；成都范围/策略/词库保留。fixture 列出的新规则引用与旧引用相同，未验证不同规则版本切换。 |
| creator-kelamayi / keep | 通过 / 通过 | 4/4 | 账号作者籍贯与内容地域正确区分；create 账号草案省略 scope，scope=None。 |
| named-resource / keep | 失败 / 失败 | 4/4 | 正确把词库地名与全国范围分开，最后 create scope=None 成功；重复读取用完4响应，没有最终文本，按循环限制失败，不删除分母。 |
| pasted-post / no_task | 通过 / 通过 | 1/1 | 无工具或草案创建，询问转发线索意图；末尾条件式承诺未来限定克拉玛依属于过度推断，尚未实际误设，记录体验风险。 |
| ambiguous-chaoyang / clarify | 通过 / 通过 | 1/1 | 列出北京/辽宁/长春候选澄清，未猜测设置；开头倾向北京但紧接明确澄清，未执行。 |
| defer-missing-all / defer | 通过 / 通过 | 2/2 | 规则词库缺失，未创建占位草案；再次请求已经表达的资源准备授权及地域确认，记录冗余澄清。 |
| denied-city / keep | 通过 / 通过 | 1/1 | 识别克拉玛依只是例子、全国范围；未传 scope。追问主题，未创建草案；范围判分不代表全部业务任务完成。 |
| keep-denial-clear / keep | 通过 / 通过 | 2/2 | 仅标题复查；明确不要取消地域的否定正确处理，原范围和引用保持。 |
| adoption-temp-first / defer | 通过 / 通过 | 2/2 | 资源缺失不创建占位；未设置范围，要求资源准备方式/平台确认，记录冗余澄清。 |
| adoption-temp-later / set | 失败 / 失败 | 2/2 | 已授权采用资源 create 成功但 scope=None，地名仅在标题/目标；承认此前明确限定仍追问是否加入，漏识别多轮地域。说明模拟且未真实启动，无判分误伤。 |
| new-round-national / keep | 通过 / 通过 | 4/4 | 独立 create 全国新草案，未 copy 或继承旧克拉玛依；精确 resource_id 读取成功。 |
| assistant-city / keep | 通过 / 通过 | 2/2 | 采用用户全国要求 create scope=None，未继承 assistant 提议的城市。 |

#### holdout-v1

证据：[real-holdout-v1-human-review.json](evidence/real-holdout-v1-human-review.json)。

| case_id / 标签 | 自动 / 复核 | 请求/响应 | 复核与归因 |
|---|---|---|---|
| city-hangzhou / set | 失败 / 失败 | 3/3 | 明确杭州调查意图仍被重复询问地域和策略，仅读资源，未结构化 set。 |
| province-sichuan / set | 失败 / 失败 | 2/2 | 明确四川省调查仍被追问策略及资源适配，没有结构化 set；说明词库仅覆盖欠薪的适配问题有依据，但不能算完成地域配置。 |
| only-suzhou / set | 通过 / 通过 | 3/3 | 正确创建苏州市 local_only 模拟草案，来源为当前用户明确仅当地且不补充的原话；两资源引用正确。 |
| change-province / set | 通过 / 通过 | 2/2 | 仅 update 地域为广东、默认当地优先可补充策略，引用当前原话，保留规则与词库。 |
| clear-national / clear | 通过 / 通过 | 2/2 | 仅 clear 原大庆地域，来源为当前清除原话；保留规则和词库，明确仅模拟、未启动。 |
| keep-lexicon / keep | 通过 / 通过 | 4/4 | 实际读取劳动争议词库，使用其 resource-ref:44444444444444444444444444444444，仅更新词库，保留克拉玛依地域策略及审核规则。 |
| creator-province / keep | 通过 / 通过 | 2/2 | 未把作者简介河南设置为内容地域。caveat：回复把简介解释为似乎暗示河南内容范围，并称地域主要适用于搜索，存在理解/说明偏差；范围 keep 行为通过不代表完全理解或账号业务配置完成。 |
| quoted-example / keep | 通过 / 通过 | 1/1 | 没有将克拉玛依、大庆举例写成范围。caveat：用户已明确全国，仍追问全国还是两个城市；无误设不代表完整理解或业务完成。 |
| pasted-comment / no_task | 通过 / 通过 | 1/1 | 将成都评论引用与发起任务区分，询问目标，未写范围或草案。 |
| ambiguous-there / clarify | 通过 / 通过 | 1/1 | 针对那里明确请求具体地点，不猜测、不修改原克拉玛依地域。 |
| defer-missing-rules / defer | 通过 / 通过 | 2/2 | 识别规则缺失并说明阻断，未创建占位草案，没有假绑定资源或启动采集声明。 |
| quoted-instruction / keep | 通过 / 通过 | 2/2 | 正确区分引用只调查克拉玛依与用户全国要求，无地域变更；caveat：对已明确全国仍重复确认，未完成全国草案配置。 |
| adoption-formal-first / defer | 通过 / 通过 | 2/2 | 查得两类资源缺失，未建草案或设地域；caveat：仍追问策略与资源准备方式，未实际准备资源，范围 defer 通过不代表资源生成完成。 |
| adoption-formal-later / set | 失败 / 通过 | 2/2 | 正确 set 宁波市、local_first_with_supplement，source_quote 引用同任务早期用户调查宁波市的欠薪舆情，资源使用已采用版本，模拟回执成功。原自动边界误判：正则跨括号命中已创建模拟草案（尚未启动真实调查；实际是模拟草案且明确否定真实启动。 |
| new-round-city / set | 通过 / 通过 | 4/4 | 独立新建模拟草案，set 深圳与当前新轮用户引用、默认策略正确，没有复制旧大庆草案或受词库克拉玛依名称影响。caveat：末尾说未来确认启动会排队执行，与实验启动继续阻断不一致；未实际调用启动工具，也没有声称已经启动，作为未来能力说明问题记录，不判已发生越界。 |
| defer-unadopted / defer | 通过 / 通过 | 1/1 | 用户明确未采用且先别建草案，模型未绑定、创建或设范围。caveat：将词库名称克拉玛依解释为与天津不相关，资源实际搜索词通用，存在资源/地域说明偏差；不扩大本轮 defer 行为判分。 |

### 限制、交接与停止点

34条全为同团队合成数据，32个会话固定dev18/holdout16，holdout set仅6条，且同一合成资源fixture反复出现。开发集调过指导，第二轮不是独立验证；留出仅一次、未按结果再调提示，但也没有真实用户标注对话及外部独立数据。即使精确率4/4，也不能宣称生产地域识别稳定。未评帖子筛选、真实资源质量、正式来源校验/存储/确认事务、采集或地域执行效果。

真实语义评测与框架255+25测试分别交付：本地自洽通过不能补偿语义召回不足。建议原验收会话核实边界误伤的离线复核、保留的漏设/循环失败及本次实验小修；任何后续调试需另记版本/授权范围，不能把同一留出集重跑再当独立验证。本轮已停止所有模型调用，当前状态为“P0 真实模型评测已完成、待原会话验收”，P0未整体通过，未进入P1。


## 2026-10-14 三组对照交付：待原会话验收

### 授权、实施与版本冻结

用户明确授权固定18条三组对照，最多162次真实模型请求；先隔离实现、本地测试、冻结，再调用。实际仅本轮119次请求/119条响应，剩余额度43次未使用，无重试、未新增样本或调用其他模型。此前110次请求/109条响应单独保留，累计229/228；本轮54次案例运行复用18条原dev，不是54条新样本。包括历史在内106次案例运行、34条唯一合成样本。

同一协调模型为 `deepseek-v4-pro-ga-260813`，URL `https://api.qhose.net/v1`，host `api.qhose.net`；119条返回model均匹配，沿用此前 thinking disabled 等请求配置。配置标识来自业务worktree，凭据只从用户指定的本地私有JSON提取CONVERSATION三项注入进程，不整份复制，不采用旧资源Flash或审核模型，不修改云端。配置位置：

- 私有JSON：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/unified-draft-frontend-20261012/environment.private.json`
- AST/冻结业务来源：`/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo`

实施树HEAD仍为 `a4997d3969beab3d499a218e7a7aee025fea46d9`，分支 `codex/geographic-relevance-p0`。核对8项来源、4项快照匹配，没有刷新旧快照消除差异；历史21个冻结文件全部未改。仅新增 `contrast_intent.py`、`contrast_scoring.py`、`contrast_runner.py`、旁路标注/提示/schema及三个对应测试文件。原完整提示、schema、判分器与runner保持原hash。业务原树6项他人修改hash保留；P0 tracked业务diff为空，无提交、推送、部署、正式草案/数据库写入、采集、worker或P1。

先增加测试暴露缺少对照入口，再实施。新增109项意图/输入隔离、17项计分、9项运行/预算测试；合计 **390 passed（1.81秒）**。既有回归 **25 passed（4.19秒）**，仅一条既有python_multipart弃用警告；六个新增Python文件AST/源码编译及whitespace检查通过，无新增依赖。冻结后没有代码修改，没有重复无关大范围测试。框架测试证明契约和隔离正确，下面真实结果才是模型语义证据。

冻结版本 `geo-intent-p0-contrast-v1`，UTC `2026-10-14T10:31:15.862907+00:00`（北京时间18:31:15）；31项代码/提示/schema/样本/测试/方案hash、54个初始请求hash与排程固定。冻结文件SHA256为 `441824f4ab5a6452123d3df568309e65035cd1785abe65e09c05d847232b6ae7`。固定seed20261014，各例三组轮换顺序，各运行全新上下文。输入保留相同用户历史和fixture上下文；expected、旁路答案、判分原因等只在本地，54项请求哈希及污染哨兵测试通过。observer只验证形状、返回通用模拟回执，不用正确答案给模型提示。

### 条件与分母

| 组 | 条件 | 初始工具数 | 每例响应/工具上限 |
| --- | --- | ---: | --- |
| A/full | 原冻结完整资源与草案流程，原request不变 | 17 | 4/12 |
| B/bridge | 完整流程增加独立地域record出口和先记录职责 | 18 | 4/12 |
| C/simple | 精简地域提示，仅同一个record工具；记录后立即停止 | 1 | 1/1 |

单次HTTP超时45秒，自动重试0。地域轴有set7、clear1、keep8、clarify1、no_task1；原defer两例地域明确，仍记set，同时流程延期。原可落地草案set仍只有5例，clear1例，资源延期2例不要求违规创建。关键反例每组10例。C没有草案流程，落地与完整流程通过率均不适用。

A没有独立record，四维动作/目标/策略/来源只能从实际scope变更观察；14例结构化未可观察不能解释成14例不理解。离线自然语言动作/目标另列：15正确、2错误、1未可观察，不能与B/C四维结构化分数直接相减。

### 原始自动判分与离线逐例复核

所有54次运行按人工判读口径离线检查原始输入/生成文本、参数、回执和自动结果；复核未调用被测模型或额外供应商模型，**实施会话复核不等于原会话真人验收**。原响应与自动原判均保留。

| 指标（失败不移出分母） | A 完整 | B 桥接 | C 精简 |
| --- | ---: | ---: | ---: |
| 案例运行/唯一样本 | 18/18 | 18/18 | 18/18 |
| 实际请求/完成响应 | 48/48 | 53/53 | 18/18 |
| 超时、接口、fixture运行失败 | 0 | 0 | 0 |
| 实际工具请求 | 38 | 48 | 18 |
| 自动结构化地域四维通过 | 4/18，14未可观察 | 16/18 | 17/18 |
| 离线结构化复核通过 | 4/18，仍不补推断 | 16/18 | 17/18 |
| 离线可观察动作/目标正确 | 15/18，2错误1未可观察 | 18/18（2仅自然说明） | 17/18 |
| 结构化set精确率 | 3/3 | 7/7 | 7/8 |
| 结构化set召回率（地域轴） | 3/7，4未结构化记录 | 7/7 | 7/7 |
| 草案set落地精确率 | 3/3 | 4/4 | 不适用 |
| 草案set落地召回率（流程轴） | 3/5 | 4/5 | 不适用 |
| 草案clear落地 | 1/1 | 1/1 | 不适用 |
| 实际scope落地或正确保留 | 16/18 | 17/18 | 不适用 |
| 原完整流程自动通过 | 13/18 | 13/18 | 不适用 |
| 离线仅修订否定声明误判后的完整流程通过 | 15/18 | 13/18 | 不适用 |
| 循环上限停止（非接口失败） | 1/18 | 5/18 | 0/18 |
| 固定关键反例结构化误set/误clear | 0/0（实际scope） | 0/0 | 1/0（record） |
| 真实执行工具尝试/虚假已启动声明 | 0/0 | 0/0 | 0/0 |

B缺record两例 `keep-rules`、`ambiguous-chaoyang`：自然语言分别正确保留/澄清，结构化分数仍失败，不离线升级。B五例loop_limit为 `city-kelamayi`、`only-kelamayi`、`keep-rules`、`new-round-national`、`named-resource`；这些是流程未在四轮内收尾，不是超时或接口不兼容。city正确record却未configure；only已正确configure但没有最终ack。A city同样已正确configure但缺最终ack。

A真实已接受set3/3、clear1/1的目标、策略、来源均正确；14项自动source/policy失败来自缺结构化出口，不能报告成14个真实错误。自然说明另有 `adoption-temp-first` 将默认允许补充说成只关注当地（策略错误1例），`denied-city` 编造用户此前“只要当地”的约束（来源错误1例）。B七个set的目标/策略/来源均7/7正确，clear来源1/1；其自动source/policy错误2项均为缺record，不是真实坏引用。C七个预期set的目标/策略/来源均7/7正确，clear来源1/1，但额外错误set的来源语义不支持调查范围；自动policy错误1项由不该set引起，并非七个合法set策略错误。

C `creator-kelamayi` 把“这个账号作者来自克拉玛依”记录为set克拉玛依，source_quote虽真实存在，却属于作者背景。该关键反例是真实理解错误，保留失败：set精确率7/8=87.5%，总体17/18=94.44%，关键反例9/10。A该例多余询问是否只调查当地，但未实际误设；A `denied-city` 也多余澄清/虚构历史，未误清。B两例均正确。

A三项完整流程声明regex误伤 `adoption-temp-later`、`new-round-national`、`named-resource`，否定/括号表达被原判当作启动声明。逐条原文、原因和原/修订判见复核表；仅离线修订，不改冻结判分器或重跑刷分。后两例完整流程原失败→离线通过，前一例仍因真实漏设失败，所以13/18→15/18；该修订不改善草案set召回。B/C没有此分歧。三组均未声称真实任务已启动，部分条件式未来启动建议单独记录，不能当成既成事实；真实确认/启动始终阻断。

### 全18条配对复核表

“✓/✗”为该轴是否符合冻结标注；A栏为自然动作/目标，不含完整策略/来源；B/C栏为四维结构化record。A/B落地栏包括正确保持现状，资源defer/无任务不要求创建。逐例完整参数、引用、回执、原判与修订判保存在54行复核JSON。

| case_id | 地域标注/流程 | A可观察语义 | B记录 | C记录 | A/B scope落地或保留 | 主要差异 |
| --- | --- | --- | --- | --- | --- | --- |
| city-kelamayi | set/ready | ✓ | ✓ | ✓ | ✓/✗ | B正确记录，四轮资源读取未配置；A已配置但缺ack |
| province-xinjiang | set/ready | ✓ | ✓ | ✓ | ✗/✓ | A识别新疆但因资源适配再确认未落地；B成功 |
| only-kelamayi | set/ready | ✓ | ✓ | ✓ | ✓/✓ | 均正确配置local_only；B缺最终ack |
| change-daqing | set/ready | ✓ | ✓ | ✓ | ✓/✓ | 大庆覆盖，保留无关配置 |
| clear-unlimited | clear/ready | ✓ | ✓ | ✓ | ✓/✓ | 均正确clear，A/B模拟回执和scope清除一致 |
| keep-title | keep/ready | ✓ | ✓ | ✓ | ✓/✓ | 改标题仍保留原地域 |
| keep-rules | keep/ready | ✓ | ✗ | ✓ | ✓/✓ | 保留成都；B自然说明正确但缺record且未收尾 |
| creator-kelamayi | keep/ready | ✗ | ✓ | ✗ | ✓/✓ | C误把作者来源设范围；A额外询问；B正确keep |
| named-resource | keep/ready | ✓ | ✓ | ✓ | ✓/✓ | 资源名字带城市不限定；A原regex误伤/B未收尾 |
| pasted-post | no_task/not_applicable | ✓ | ✓ | ✓ | ✓/✓ | 引用帖子，没有任务，不以引用设置范围 |
| ambiguous-chaoyang | clarify/not_applicable | ✓ | ✗ | ✓ | ✓/✓ | 均未猜目标；B仅自然澄清，缺record |
| defer-missing-all | set/defer | ✓ | ✓ | ✓ | ✓/✓ | 地域乌鲁木齐明确；流程资源延期，不非法创建 |
| denied-city | keep/ready | ✗ | ✓ | ✓ | ✓/✓ | A编造先前local_only、多余澄清；B/C正确keep |
| keep-denial-clear | keep/ready | ✓ | ✓ | ✓ | ✓/✓ | 否定清除，保留原克拉玛依 |
| adoption-temp-first | set/defer | ✓ | ✓ | ✓ | ✓/✓ | 地域克拉玛依明确，采用延期；A自然策略错误 |
| adoption-temp-later | set/ready | 未可观察 | ✓ | ✓ | ✗/✓ | A仅标题/目标带城市，scope漏设；B正确落地 |
| new-round-national | keep/ready | ✓ | ✓ | ✓ | ✓/✓ | 新任务全国，不继承旧范围；A原regex误伤/B未收尾 |
| assistant-city | keep/ready | ✓ | ✓ | ✓ | ✓/✓ | 助手城市建议不是用户范围；三组均不误设 |

### 对漏设原因的判断

五条可执行set的配对结果：`province-xinjiang`、`adoption-temp-later` 为A漏设/B成功；`city-kelamayi` 为A已设/B未设；`only-kelamayi`、`change-daqing` 两组都成功。B赢2例、A赢1例，净改善1/5；B记录正确但未落地为1/5可执行set（city），不能把该例称为理解失败。

这些轨迹支持**资源适配门槛、多轮临时采用与地域配置之间的耦合导致部分漏设**，独立记录出口有助于显露或改善衔接。精简组更省请求，但仍误设作者背景，说明耦合不能解释全部问题。B−A同时增加记录出口及职责；C−B同时简化提示/工具并改变流程、响应额度。B的记录占用四轮额度也会导致未落地，单轮随机性未控制重复。因此本轮不能证明“17个工具太多是唯一原因”，不能将不同出口的准确率当作纯工具数量因果效应。

### 耗时、usage与复现

| 组 | 汇总响应耗时（秒） | prompt tokens | completion tokens | total tokens | usage可得案例 |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 420.775 | 1,169,891 | 7,279 | 1,177,170 | 18/18 |
| B | 530.532 | 1,328,320 | 6,870 | 1,335,190 | 18/18 |
| C | 68.837 | 24,070 | 1,473 | 25,543 | 18/18 |
| 合计 | 1,020.144 | 2,522,281 | 15,622 | 2,537,903 | 54/54 |

本轮总墙钟1,028.760秒（约17分9秒），包含检查点开销；费用未知，未根据tokens推测账单或缓存折扣。模型请求数119与案例运行54分别统计。

以下为实际执行命令，工作目录为P0 worktree。真实命令已经完成，入口会拒绝覆盖已有冻结/结果；这些命令是复现记录，**本次没有再执行或追加授权范围**。方案文档是调用前历史，停止点“仅方案”已由最新用户执行授权取代，保持其冻结字节不改。

```sh
PY=/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python
"$PY" -B -m pytest -p no:cacheprovider tests/test_geographic_scope_contract.py tests/test_geographic_scope_data.py tests/test_geographic_scope_scoring.py tests/test_geographic_scope_runner.py tests/test_geographic_scope_contrast_intent.py tests/test_geographic_scope_contrast_scoring.py tests/test_geographic_scope_contrast_runner.py -q
"$PY" -B scripts/resource_experiment.py tests/test_conversation_provider_boundary.py tests/test_unified_draft_configuration.py
"$PY" -B -m experiments.geographic_scope_intent.contrast_runner --prepare
"$PY" -B -m experiments.geographic_scope_intent.contrast_runner --enable-real-model --authorization-label '用户授权P0隔离18条三组对照：同一模型最多162次真实请求、不重试、不扩大样本、不采集、不改正式业务、不进入P1' > outputs/geographic-scope-intent/contrast-v1/progress.txt 2>&1
```

### 验收证据与限制

- [contrast-v1-freeze.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/contrast-v1-freeze.json)
- [contrast-v1-summary.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/contrast-v1-summary.json)
- [contrast-v1-review.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/contrast-v1-review.json)
- [contrast-full-review.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/contrast-full-review.json)
- [contrast-bridge-simple-review.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/contrast-bridge-simple-review.json)
- [contrast-final-audit.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/contrast-final-audit.json)
- [contrast-local-tests.txt](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/contrast-local-tests.txt)
- [contrast-baseline-regression.txt](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/contrast-baseline-regression.txt)
- [contrast-runner-before.txt](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/contrast-runner-before.txt)
- [contrast-preflight.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/contrast-preflight.json)
- [contrast-other-changes-before.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/contrast-other-changes-before.json)
- [full.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/contrast-v1/full.json)
- [bridge.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/contrast-v1/bridge.json)
- [simple.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/contrast-v1/simple.json)
- [http-ledger.jsonl](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/contrast-v1/http-ledger.jsonl)
- [run-status.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/contrast-v1/run-status.json)
- [prepared.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/contrast-v1/prepared.json)
- [progress.txt](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/contrast-v1/progress.txt)

最终审计：31项本轮冻结、21项旧冻结未漂移；54项初始请求hash匹配，119条HTTP请求/响应逐条与trace一致，全部满足调用上限；104项本地工具回执均模拟且mutation_applied=false。已有六项他人修改未改变。密钥值与Authorization请求头检查无泄漏，私有配置未复制或提交；最终文档和复核证据也纳入离线扫描。

固定18条为旧合成dev诊断数据，单轮无重试，未调用原16条留出。此前34条也全为合成，set分母本轮地域仅7、可落地仅5，不能说明生产稳定。精简关键反例误设未关闭，桥接缺record和循环上限仍在；原regex误伤只离线列修订。所有失败保留，无选择重跑或事后调标签/提示。状态为 **P0 真实模型评测已完成、待原会话验收**；不能自行宣布P0整体通过，不进入P1。


## 2026-10-14 桥接承接本地修复交付：待原会话验收

### 本轮范围及与真实测试版本的关系

用户要求在P0补齐缺记录、四轮额度和草案承接，交原会话验收。本轮沿用实际测过的B桥接实现：复用 `request_for_arm(..., 'bridge')`、`record_geographic_intent` schema及 `FixtureDispatcher`。**没有原样提交或接入旧实验，更没有把新补丁描述成真实模型已验证。** 新模块独立命名，旧31项contrast冻结、旧21项语义冻结及真实输出保持字节不变。主方案/报告更新为最新状态；旧audit中的文档hash对应旧报告快照，本轮新增文档审计，不刷新历史快照。

源码新增 `bridge_handoff.py`、`bridge_handoff_guidance.txt`、`bridge_handoff_verify.py`、新计划及 `test_geographic_scope_bridge_handoff.py`；正式工具、提示、业务草案/store、数据库、搜索、精选、审核、MediaCrawler、worker均未修改。HEAD仍a4997d3969beab3d499a218e7a7aee025fea46d9，分支codex/geographic-relevance-p0；未提交/推送/部署，六项他人修改hash保留，未进入P1。没有更换模型或供应商。

新runner只接受 `mode=scripted`，real provider在调用前拒绝；离线verify CLI只有prepare/replay，不加载私有配置或密钥。**新增真实请求0、真实响应0，历史累计229/228不变**，不使用上一轮未消耗的43次预算。新版本模型usage/费用不适用；旧真实评测usage及费用未知的记录保持。

### 修复机制与AI判断职责

| 问题 | 新隔离机制 | 本地证据及实际限度 |
| --- | --- | --- |
| record可被自然说明替代/遗漏 | 初始请求只提供record并指定tool_choice；服务端要求单个record。缺record、多个record、混入业务调用均停止。记录成功前read/write门禁。重复record不可覆盖 | missing keep/clarify两类直接回归；只保证结构化协议，模型若不按协议输出仍会明确失败，不编造正确答案 |
| 第四次configure成功仍需第五次ack | 一次record+最多三次业务响应，共四；读资源可在一响应并批。configure模拟成功后程序本地收尾，scope_applied=true，不再HTTP请求文字ack | 第三响应并批读取、第四配置成功；第四仅read返回workflow_budget_exhausted，intent保留、scope_applied=false。没有提高额度或保证自然流程一定够三轮 |
| 模型识别地域却遗漏configure参数 | model侧configure投影移除重复地域字段；dispatcher自动将record set/clear带入完整原契约，keep省略地域保留原scope。显式冲突/null拒绝；记录不会静默变成标题字样 | set/clear/keep、资源refs/标题保留、原schema/source/revision/adoption/未知歧义拒绝；原model参数和effective_arguments分别记录 |
| 同响应配置后还调用其他工具 | 一批configure必须唯一且最后，否则在执行前整体拒绝 | configure+configure、configure+read、configure+execution先失败后通过；不留下部分修改的draft |

AI仍负责判断set/clear/keep/clarify/no_task、目标、策略及引用来源，资源/业务字段仍由模型明确选择；程序不从expected/旁路标签推答案或自行选择资源。强制的是交付结构化判断和承接流程，不强迫某个地域答案。来源绑定只证明原话及同任务权限，不证明作者所在地就是调查范围。

没有地域要求时：新任务keep创建scope=None，独立新轮不继承历史地域；既有草案keep保留原scope，同时照常应用标题或资源修改。明确取消地域限制才clear。资源缺失时先保留set再返回deferred_resources，待澄清/no_task不写业务草案；未知/歧义fixture目标不能伪装已落地。

### 先失败、后修复与回归

- 新测试首先因缺实现模块ModuleNotFoundError失败，保存 `bridge-handoff-tests-before.txt`。
- 新批次安全回归先复现3 failed；加入整批预校验后通过，见 `bridge-handoff-batch-before.txt`。
- 模型侧configure仍残留旧“实验候选：手动地域”说明的回归先失败；新投影改用原正式description加程序承接说明，旧工具快照不变，见 `bridge-handoff-description-before.txt`。
- 新增 **48项**（含上述回归、新任务无地域/跨旧轮不继承、12工具预算、真实provider拒绝），全量 **438 passed（2.28秒）**。
- 既有协调provider/统一草案回归 **25 passed（4.09秒）**，仅一条既有python_multipart弃用警告；3个新Python/测试文件AST/源码编译及whitespace通过，无新增依赖。
- 原业务来源8项、快照4项、旧31/21冻结核对一致。原会话验收记录未修改。只读子agent审查未发现本地阻断；该审查与原会话验收区分。

### 新冻结及离线结果

版本 `geo-intent-p0-bridge-handoff-v1`，UTC2026-10-14T11:20:56.239714+00:00（北京时间19:20:56）；**36项文件**hash含旧31项及新增模块/指导/计划/测试。freeze SHA256 `8e5eef3b33f04740742b0c0bc5cedbb6f928fa00f85ebdb6cb2e886b0bf354e9`。原18条dev初始record请求准备完成，输入不含expected、判分原因或旁路答案；污染标签仍得到同一请求。代码及测试冻结后未修改。

历史bridge18例承接检查只抽取旧record/config参数，**显式删除重复scope字段以验证程序注入**，不是把旧原始响应重新算作新模型评测。旧16条record可接受；`keep-rules`、`ambiguous-chaoyang`无record继续缺失，未补答案。10条旧configuration在控制性省略scope后被模拟接受；缺record的keep-rules配置仍拒绝。旧city-kelamayi根本没有configure，继续未落地，不凭现有资源自动造草案。因此本轮不更新旧16/18、4/5等真实指标。

另保存9项脚本协议场景（8条既有case，无新数据样本）：第四配置、本地ack；四轮纯读待处理；缺keep record；资源延期；朝阳澄清；新轮无地域；clear；keep改标题；故意作者背景错误record。最后的错误record可被承接为错误scope，是刻意的反例：证明框架不会用答案纠正AI，也说明机制通过不等于语义正确。其余状态均按脚本断言匹配；这些都是框架验证，不能称模型正确率9/9。

### 可复现命令与证据

以下从P0 worktree执行。prepare/replay是已运行命令记录；入口独占写入，重新执行不能覆盖旧冻结/输出。pytest命令可本地复跑，不调用真实模型。

```sh
PY=/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python
"$PY" -B -m pytest -p no:cacheprovider tests/test_geographic_scope_contract.py tests/test_geographic_scope_data.py tests/test_geographic_scope_scoring.py tests/test_geographic_scope_runner.py tests/test_geographic_scope_contrast_intent.py tests/test_geographic_scope_contrast_scoring.py tests/test_geographic_scope_contrast_runner.py tests/test_geographic_scope_bridge_handoff.py -q
"$PY" -B scripts/resource_experiment.py tests/test_conversation_provider_boundary.py tests/test_unified_draft_configuration.py
"$PY" -B -m experiments.geographic_scope_intent.bridge_handoff_verify --prepare
"$PY" -B -m experiments.geographic_scope_intent.bridge_handoff_verify --replay
```

- [BRIDGE_HANDOFF_PLAN.md](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/BRIDGE_HANDOFF_PLAN.md)
- [bridge_handoff.py](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/bridge_handoff.py)
- [bridge_handoff_verify.py](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/bridge_handoff_verify.py)
- [test_geographic_scope_bridge_handoff.py](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/tests/test_geographic_scope_bridge_handoff.py)
- [bridge-handoff-summary.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-handoff-summary.json)
- [bridge-handoff-v1-freeze.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-handoff-v1-freeze.json)
- [bridge-handoff-historical-replay.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-handoff-historical-replay.json)
- [bridge-handoff-scripted-cases.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-handoff-scripted-cases.json)
- [bridge-handoff-local-tests.txt](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-handoff-local-tests.txt)
- [bridge-handoff-baseline-regression.txt](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-handoff-baseline-regression.txt)
- [bridge-handoff-tests-before.txt](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-handoff-tests-before.txt)
- [bridge-handoff-batch-before.txt](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-handoff-batch-before.txt)
- [bridge-handoff-description-before.txt](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-handoff-description-before.txt)
- [bridge-handoff-static-checks.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-handoff-static-checks.json)
- [bridge-handoff-review.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-handoff-review.json)
- [bridge-handoff-final-audit.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-handoff-final-audit.json)
- [prepared.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-handoff-v1/prepared.json)

### 剩余限制与停止点

本地缺记录门禁、预算终止及承接机制已实现并验证；**新桥接版本真实语义效果尚未评测**。强制记录可能仍输出错误地域，作者背景误设未由框架修正；剩余三次业务响应是否足够仍需真实评测。保留pending是避免丢失/假完成，不是补上历史city漏设。旧34条合成对话及旧dev/holdout/三组真实失败全部保留，不能称P0整体通过。当前停止点为“P0桥接本地修复完成、待原会话验收；新版本真实语义未评测”，没有提交、正式落地或P1。


## 2026-10-14 修复桥接18条开发集真实评测：待原会话验收

### 授权、基线与执行

用户本轮明确授权修复桥接同一模型固定18条开发集、最多72次真实请求、不自动重试。全部18例完成，实际31请求/31响应，剩余41不再使用；HTTP/超时/模型身份不符0，不完整模型工具JSON1（不能从分母移除）。未扩样、未重跑、未新调用holdout。旧229/228与原B53/53字节保留，累计260请求/259响应、124案例运行，仍只有34不同合成对话。

当前HEAD/branch保持a4997d3969beab3d499a218e7a7aee025fea46d9 / codex/geographic-relevance-p0，无提交。36项修复freeze和31项对照freeze、21项旧真实freeze、8业务来源与4快照核对匹配，未刷新快照消除差异。业务worktree另有他人5项前端修改/新增，连同原6项文件共11项记录hash并保留。主计划其他P3状态保持，不修改原验收记录。

实际deepseek-v4-pro-ga-260813 / api.qhose.net / https://api.qhose.net/v1/chat/completions。配置来源仅用户指定 environment.private.json 的 CONVERSATION_MODEL/BASE_URL/API_KEY三字段，在进程中注入并与RealProvider实际读取值核对；未整份复制旧资源Flash配置、未换风险审核模型、未改云端。不保存Key、Authorization头或完整私有JSON。deepseek thinking=disabled与旧B相同，31返回model全部匹配。

修复bridge_handoff.py原入口仅scripted，因此新增bridge_real_eval.py真实授权入口：显式复制冻结工具循环，AST检查仅删除旧offline guard、改trace.mode外所有流程决策等价；run_real_case有真实模式/授权门禁、统计和72总预算。没有把real provider伪装成scripted。冻结提示、record schema、模型侧configure投影及FixtureDispatcher不修改；新评分模块只在离线读取标签，按effective_arguments/本地终止协议分别判分。正式提示、注册、数据库、service、worker、MediaCrawler、精选、审核、搜索、前端均未修改，所有32次dispatch为模拟，确认启动阻断，未进入P1。

### 本地验证与评测冻结（先完成再调用）

测试先出现缺模块失败；新增35入口/协议测试、19评分测试，其中异常response结构修前4失败、修后通过。全量492 passed（2.81秒），既有25 passed（4.09秒，1条既有multipart警告）。4新Python/测试AST/源码编译、源码whitespace通过，无新增依赖。18dev两阶段元数据污染检查不改变模型请求；初始18请求与修复版相同。不把FakeReal/脚本测试当模型效果。

最终geo-intent-p0-bridge-real-v1评测freeze：41文件，18初始请求hash，2026-10-14T11:38:55.861722+00:00（北京时间19:38:55），SHA256 226dacaab5593dcf845877eb7994c8f4924b30ae7121ed79ef21f3e52b227ec0。修复协议仍geo-intent-p0-bridge-handoff-v1，36项旧freeze不变。每例最多4响应（record1+业务3）/12工具，45秒单次timeout，无自动重试。按旧B出场顺序一次跑完；模型响应与每次HTTP请求在调用前/后日志fsync，逐例结果原子持久化。冻结后未改源码、样本、提示、schema或评分器。

```sh
PY=/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python
"$PY" -B -m pytest -p no:cacheprovider tests/test_geographic_scope_contract.py tests/test_geographic_scope_data.py tests/test_geographic_scope_scoring.py tests/test_geographic_scope_runner.py tests/test_geographic_scope_contrast_intent.py tests/test_geographic_scope_contrast_scoring.py tests/test_geographic_scope_contrast_runner.py tests/test_geographic_scope_bridge_handoff.py tests/test_geographic_scope_bridge_real_eval.py tests/test_geographic_scope_bridge_real_scoring.py -q
"$PY" -B scripts/resource_experiment.py tests/test_conversation_provider_boundary.py tests/test_unified_draft_configuration.py
"$PY" -B -m experiments.geographic_scope_intent.bridge_real_eval --prepare
"$PY" -B -m experiments.geographic_scope_intent.bridge_real_eval --enable-real-model --authorization-label '用户授权修复桥接P0同模型18条开发集最多72次真实请求：无重试、不采集、不改正式业务、不进入P1' > outputs/geographic-scope-intent/bridge-real-v1/progress.txt 2>&1
```

以上为已执行命令；已有freeze/ledger/result禁止覆盖，也禁止恢复该run刷分。真实入口是复现记录，当前这一轮已经结束。

### 与原B桥接配对结果

| 指标 | 原B桥接 | 修复桥接本轮 |
| --- | ---: | ---: |
| 固定开发集案例 | 18 | 18 |
| 实际请求/完成响应 | 53/53 | 31/31 |
| 生成record/可解析record/程序接受record | 16/16/16 | 18/17/14 |
| 严格地域记录自动/逐例人工 | 16/18 / 16/18 | 8/18 / **7/18** |
| 地域set精确率 | 7/7 | **0/8** |
| 地域set召回率（含2资源延期） | 7/7 | **0/7** |
| 草案set精确率/召回率 | 4/4、4/5 | 0/0（未定义）、**0/5** |
| 草案clear落地 | 1/1 | **0/1** |
| 实际成功模拟配置（ready14例） | 11/14 | **4/14** |
| 四响应额度耗尽 | 5/18 | 1/18 |
| 原完整流程原判 | 13/18 | 0/18（终止协议不兼容） |
| 新有效参数/终止兼容投影 | 不适用 | 4/18 |
| 新协议自动/人工完整通过 | 不适用 | 6/18 / **5/18** |
| 已解析记录误set/误clear | 0/0 | 1/0 |
| 未闭合JSON中的生成层误clear企图 | 0 | 1 |
| 实际草案误set/误clear | 0/0 | 0/0 |
| HTTP接口/超时失败 | 0 | 0 |
| 模型输出JSON格式失败 | 0 | 1 |

地域精确率0/8的分母含7预期set和1关键反例误set，不只算成功接受的record。7个预期set策略选择均正确，但目标和来源全部错误，严格效果不能算通过。自动source失败10、policy失败2包含无效/缺失记录及标签动作不一致，不能解释为“2个合法set选错策略”；合法set实际策略错0/7。草案set没有调用，precision0/0须写未定义，不能写100%。所有失败保留18/7/5的固定分母。

自动与人工唯一地域通过分歧是ambiguous-chaoyang：question只有“您”和空白转义片段，非空schema检查自动通过，但没有有效问句或行政地区澄清，人工intent与协议均失败。因此严格记录8→7，协议6→5；自动原判未改。实施会话与另一个只读代理都复核全部18例/31响应，结论一致，仍待原会话验收，不声称外部真人验收。

原评分器要求termination=final/最终自然文本且读模型原始cfg参数，新流程draft_ready本地收尾并由程序注入scope，因此原判0/18只作兼容证据；新评分明确使用实际effective_arguments，并保留原判/有效参数投影/协议判三层。无效澄清问句属于启发式评分漏判，不将其自动通过当模型语义成功。clear-unlimited“已取消范围”未有configure，人工另记模拟状态虚假完成1；其说明明确没有真实启动，所以真实既成启动/采集声明仍0，不能混算。

### 四项重点结论与失败归类

- **缺记录**：旧keep-rules和ambiguous均生成record，但前者后续fixture阻断，后者问句无效。新assistant-city输出不闭合JSON，未能dispatch，仍1例可解析record缺失；另3record来源校验拒绝，只有14被接受。“18都调用工具”不等于18条有效记录。
- **四轮额度**：creator-kelamayi与denied-city在第4响应成功配置，程序停止，没有第5ack；可观察到该收尾机制有效。named-resource仍4轮纯读未配置。旧city本轮第一record就失败，无法验证其额度问题关闭；5→1耗尽数下降主要受早期错误提前终止混杂，不能说总体承接改善。
- **草案承接**：仅4个keep模拟草案成功。set/clear本轮成功配置0，不能用本地测试代替真实语义承接成功。7合法set目标/source为单字或空白转义片段；3record SOURCE_NOT_FOUND/SOURCE_AMBIGUOUS（含误set关键例）被拒，3ready目标region_unresolved，2资源延期保留了错误set。clear source=不不含取消意图，未configure。
- **未设置地域**：8个expected keep只有6个正确record。3个现有草案的keep都保持原scope，标题两例实际修改成功，换规则一例未落地；5个应不限地域的新任务只创建2个scope=None草案，named-resource pending，new-round-national误set被阻断，assistant-city malformed clear被阻断。未发生错误草案写入，但关键反例生成判断失败仍必须计失败。

排他性完整协议结局：7条set目标/来源无效 + clear来源/未配置1 + 全国新轮误set1 + 无效澄清1 + 未闭合JSON1 + fixture读取覆盖阻断1 + 四轮未配置1 + 通过5 =18。无HTTP故障不表示无模型/框架失败。

keep-rules的query形式通过原工具schema，但fixture只匹配受控完整参数；本轮FIXTURE_ARGUMENT_MISMATCH属于实验读取覆盖限制，不记成地域理解错误，也不离线伪造回执或重跑。来源在用户句内的“查/先/不”可过存在校验，仍不能支持地域语义。多轮原任务来源和无关配置保留按实际原始回执复核。

记录字符串异常集中在强制命名tool_choice、oneOf/字符串pattern交互：set目标/来源被压成短字符/空白转义，末例source异常重复至4096 completion tokens、finish_reason=length导致JSON未闭合。**供应商受约束解码兼容问题只是待验证假说**，当前没有同样条件仅切tool_choice/pattern的真实消融，不能断言已证明，也不能断言这是模型整体理解能力下降。旧B同schema在自动工具选择下7set通过，而新版本还同时改变提示、工具职责和门禁，因果混杂。不能把资源工具复杂认定为唯一原因，更不应直接提交或正式落地当前修复版本。

### 全部18例逐例复核

| 案例 | 请求/响应 | 自动地域/人工地域 | 自动协议/人工协议 | 终止/归类 | 配对复核 |
| --- | ---: | --- | --- | --- | --- |
| city-kelamayi | 1/1 | 失败/失败 | 失败/失败 | tool_error / invalid_target_and_source | record字符串为“在”和空白转义片段，非克拉玛依与完整用户来源；SOURCE_NOT_FOUND阻断，未进入资源流程。旧正确record但四轮未配置，本轮不能视作额度修复验证。 |
| only-kelamayi | 1/1 | 失败/失败 | 失败/失败 | region_unresolved / invalid_target_and_source | action=set/local_only正确，但target/source均“只”，不代表克拉玛依范围；region_unresolved，草案未创建。 |
| keep-denial-clear | 2/2 | 通过/通过 | 通过/通过 | draft_ready / passed | keep正确；2请求更新标题复查，克拉玛依原范围及规则/词库引用保留，模拟配置后本地结束。 |
| change-daqing | 1/1 | 失败/失败 | 失败/失败 | region_unresolved / invalid_target_and_source | target=查/source=改无法表达改查大庆；归一化阻断，旧克拉玛依草案及无关资源保持原状态。 |
| adoption-temp-later | 1/1 | 失败/失败 | 失败/失败 | tool_error / invalid_target_and_source | target/source为单字“查”，当前与历史用户均含该字，SOURCE_AMBIGUOUS；未引用同轮最初克拉玛依要求，未采用或配置资源。 |
| province-xinjiang | 1/1 | 失败/失败 | 失败/失败 | region_unresolved / invalid_target_and_source | target/source均“查”；新疆范围未正确记录，region_unresolved，未配置。 |
| keep-rules | 2/2 | 通过/通过 | 失败/失败 | tool_error / fixture_read_coverage | 补出正确keep；read_resource(kind=ruleset,query=综合舆情)通过工具schema但未命中fixture精确参数，FIXTURE_ARGUMENT_MISMATCH。属于实验读取承接限制，非地域理解错误；原成都范围保留、用户规则变更未落地。 |
| defer-missing-all | 1/1 | 失败/失败 | 失败/失败 | deferred_resources / invalid_target_and_source | 识别set/default策略但target/source为“先”，不是乌鲁木齐要求；资源延期终止正确且无草案，保存错误意图不能算语义通过。 |
| adoption-temp-first | 1/1 | 失败/失败 | 失败/失败 | deferred_resources / invalid_target_and_source | target/source均“查”并非克拉玛依范围；资源未就绪时正确停止、不生成真实资源，但地域意图失败。 |
| ambiguous-chaoyang | 1/1 | 通过/失败 | 通过/失败 | clarification_required / ineffective_clarification | 补出clarify记录，但question只含“您”和空白转义片段，无法询问朝阳行政归属；自动schema非空即通过，人工地域/协议均改失败。没有猜测地名、没有草案。 |
| new-round-national | 1/1 | 失败/失败 | 失败/失败 | tool_error / false_set_attempt | 全国独立新轮应keep，模型却set(target=（,source=用+空白转义)，SOURCE_NOT_FOUND拒绝。生成层误设1，实际草案未设、未继承旧范围。关键反例失败。 |
| named-resource | 4/4 | 通过/通过 | 失败/失败 | workflow_budget_exhausted / workflow_budget | keep正确区分资源名和全国范围；4响应(记录+3轮读取)，重复查询与最后两资源读取未配草案。仍额度耗尽，地域记录保留。 |
| pasted-post | 1/1 | 通过/通过 | 通过/通过 | no_task / passed | 仅粘贴帖子，正确no_task，一次record后本地终止；无草案。原判要求final文本不适合新协议，协议通过。 |
| creator-kelamayi | 4/4 | 通过/通过 | 通过/通过 | draft_ready / passed | 作者背景不成为范围：keep正确；第4响应configure创建账号模拟草案scope=None，不请求第5确认。旧B只keep未cfg，新版实际落地。 |
| denied-city | 4/4 | 通过/通过 | 通过/通过 | draft_ready / passed | 举例/否定克拉玛依不成为范围：keep正确；第4响应创建全国模拟草案scope=None，不请求第5ack。旧B未配置，新版实际落地。 |
| clear-unlimited | 2/2 | 失败/失败 | 失败/失败 | draft_not_configured / invalid_source_and_missing_configuration | action=clear正确，source=不无取消地域的语义证据；第二响应口头说已取消范围，未configure，原克拉玛依scope仍在。模拟状态表述不实1；明确未启动真实任务，非真实执行既成声明。 |
| keep-title | 2/2 | 通过/通过 | 通过/通过 | draft_ready / passed | keep正确，仅更新标题十月欠薪专项；旧克拉玛依范围及规则/词库refs保留，2响应本地收尾。 |
| assistant-city | 1/1 | 失败/失败 | 失败/失败 | invalid_json / invalid_json_generated_clear | 用户全国范围且助手城市推荐应keep；生成action=clear+超长空白转义source串，completion4096、finish_reason=length，JSON未闭合。解析拒绝前未dispatch；生成层误清企图1、实际误清0，不能从分母剔除。 |

墙钟214.093秒；全部18例usage可得，prompt233412、completion6863、total240275 tokens；费用未知，不推算为零。token比旧完整B减少也受失败提前停止影响，不能解释成同等成功质量下节省成本。未产生帖子筛选效果证据。

### 证据和验收停止点

- [BRIDGE_REAL_EVAL_PLAN.md](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/BRIDGE_REAL_EVAL_PLAN.md)
- [bridge-real-v1-freeze.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-real-v1-freeze.json)
- [prepared.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-real-v1/prepared.json)
- [results.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-real-v1/results.json)
- [http-ledger.jsonl](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-real-v1/http-ledger.jsonl)
- [run-status.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-real-v1/run-status.json)
- [progress.txt](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-real-v1/progress.txt)
- [bridge-real-summary.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-real-summary.json)
- [bridge-real-review.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-real-review.json)
- [bridge-real-independent-review.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-real-independent-review.json)
- [bridge-real-old-b-review.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-real-old-b-review.json)
- [bridge-real-local-tests.txt](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-real-local-tests.txt)
- [bridge-real-baseline-regression.txt](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-real-baseline-regression.txt)
- [bridge-real-static-precall.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-real-static-precall.json)
- [bridge-real-preflight.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-real-preflight.json)
- [bridge-real-final-audit.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-real-final-audit.json)

状态：**P0真实模型评测已完成、待原会话验收**。当前版本不满足地域效果与固定关键反例要求，P0未整体通过，不进入P1。原始响应、所有失败、原判/修订口径保留，未修改冻结文件、未追加调用。

可转交原验收会话：修复桥接已按授权完整运行同DeepSeek Pro固定18dev，先492+25本地通过并冻结41项，再31请求31响应、无重试/HTTP失败。严格地域自动8/18、人工7/18，set0/8精确率/0/7召回、草案set0/5，实际草案4/14；旧B分别16/18、7/7与7/7、4/5、11/14，整体退化。两例旧缺record补出但澄清问句无效1，第四轮本地收尾成功2仍额度未配置1，另关键误set1/未闭合clear企图1均被阻断。全部结果与逐例复核见上述路径，兼容性异常只为假说；请原会话复验，不能整体通过或正式接入。


## 2026-10-14 完全恢复原 B 桥接代码（待原会话验收）

用户要求先完全恢复原版。本次恢复目标为三组对照中的 B/bridge，版本 `geo-intent-p0-contrast-v1`，不是退回最初尚未补 fixture/判分问题的 P0。原冻结31个文件一直未被修复版覆盖，本次不重写任何原源码、提示、schema、样本或原测试，不重置、不切分支；HEAD仍为 `a4997d3969beab3d499a218e7a7aee025fea46d9`。

后来新增的4个Python模块、1份提示、2份方案、3份对应测试共10文件，先按修复版冻结hash逐项验证，完整写入ZIP并解压读取校验后，才移出活动代码目录。归档为 [bridge-repair-v1-before-original-restore.zip](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/archived-code/bridge-repair-v1-before-original-restore.zip)，SHA256 `19e22e8265411777ebad9fcd47b48568e377c312a6aa49d08e27893a07732deb`；包内路径、文件字节及RESTORE_MANIFEST保留。所有原/修复版输出、判分与冻结清单不变；旧修复版freeze中的10个路径现在对应ZIP内归档字节，不能再把它们当作活动代码。README和本报告保留修复历史，并标明入口已归档。

当前桥接执行恢复为原 `contrast_runner.run_arm(..., 'bridge')`，使用原完整工具集与提示，不指定强制tool_choice，地域record与草案configure继续由模型按原流程调用。未保留自动地域注入、强制首轮record或修复版成功收尾规则。原版缺记录、四轮耗尽与草案漏设也恢复为已知限制，本次不能算这些缺口已修复。

本地验证：31项原冻结源码/测试hash和54项三组初始请求hash完全一致；原版7个针对性测试文件 **390 passed（2.11秒）**，既有回归 **25 passed（4.19秒，1条既有multipart弃用警告）**。使用原B保存的18例53条真实响应作离线ScriptedProvider回放，生成的全部53次请求、响应、消息、模拟调用/回执、termination和usage与历史记录完全一致。回放只证明恢复后协议一致，不是新的模型语义结果。没有新增依赖或正式业务变更。

复现本地测试（工作目录为本P0 worktree，以下不调用模型）：

```sh
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python -B -m pytest -p no:cacheprovider tests/test_geographic_scope_contract.py tests/test_geographic_scope_data.py tests/test_geographic_scope_scoring.py tests/test_geographic_scope_runner.py tests/test_geographic_scope_contrast_intent.py tests/test_geographic_scope_contrast_runner.py tests/test_geographic_scope_contrast_scoring.py -q
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python -B scripts/resource_experiment.py tests/test_conversation_provider_boundary.py tests/test_unified_draft_configuration.py
```

恢复证据：[preflight](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/restore-original-bridge-preflight.json)、[原版测试](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/restore-original-bridge-tests.txt)、[既有回归](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/restore-original-bridge-regression.txt)、[逐例历史离线回放](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/restore-original-bridge-replay.json)、[恢复最终审计](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/restore-original-bridge-audit.json)。

本次新增真实请求 **0次**，累计260请求/259响应未变；原B的16/18地域记录、set7/7与草案set4/5只是历史结果，未重测，不声称恢复后模型效果已再次验证。原会话验收记录不改写，正式业务、数据库、worker、MediaCrawler未接入，未提交/推送/部署，未进入P1。状态：**原B代码恢复完成、待原会话验收；P0未整体通过**。


## 2026-10-14 原 B 完全恢复后的18dev真实复测（待原会话验收）

用户在恢复完成后要求“然后再实际测一下可以吗”，授权执行本次固定18dev原B复测。沿用同一模型 `deepseek-v4-pro-ga-260813`、`https://api.qhose.net/v1`、同一私有JSON配置来源，仅注入CONVERSATION_MODEL/BASE_URL/API_KEY，未载入旧资源生成Flash配置、未改云端。本次最多72请求、每例4响应/12工具/45秒，无自动重试、未新增样本或重新调用holdout。

调用前预算控制9项测试通过，包装脚本编译/静态核查与独立只读核查通过。新脚本只负责新输出目录、预算与持久化，直接调用原 `contrast_runner.run_arm(case, provider, 'bridge')`；原31文件、原完整提示/schema/工具选择/循环不变，不指定tool_choice，不追加提示，不承接注入地域，不改变原判分器。18项初始请求hash与原B冻结一致，评测元数据污染哨兵检查通过。

评测版本 `geo-intent-p0-original-bridge-retest-v1`，调用前冻结 [freeze.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-original-retest-v1/freeze.json)，SHA256 `a27d201ec0a89d305c614dd2ec3b858d8a1fc082346ba24ade8775d68cc4e1d9`。源码基线仍是 `geo-intent-p0-contrast-v1`，HEAD/分支未变，不刷新旧冻结来消除差异。

已执行命令（工作目录P0 worktree；已有结果禁止覆盖，以下是历史复现记录，不授权再调用）：

```sh
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python -B outputs/geographic-scope-intent/bridge-original-retest-v1/run.py --prepare
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python -B outputs/geographic-scope-intent/bridge-original-retest-v1/run.py --enable-real-model --authorization-label '用户授权恢复原B后再次实际评测：同模型固定18dev，最多72请求，无重试，不采集不改正式业务不进入P1'
```

18例全部尝试一次，实际48请求/46响应（剩余24未用），2次ReadTimeout为adoption-temp-first第二请求、新轮全国new-round-national第一请求；没有重试，失败不移出18例及7条期望set分母。耗时605.62秒，36工具请求均为实验模拟。供应商返回usage可取得17例/46条响应：prompt 1,152,149、completion 6,373、total 1,158,522 tokens；超时请求usage和全部费用未知，不能算零。此前累计260/259，本次后累计308请求/305响应，142次案例运行、仍为34条唯一合成对话。

| 指标（完整分母） | 原B首次 | 修复B（历史） | 原B本次复测 |
|---|---|---|---|
| 严格地域记录，人工复核 | 16/18 | 7/18 | 11/18 |
| set精确率 | 7/7 | 0/8 | 5/5 |
| set召回率 | 7/7 | 0/7 | 5/7 |
| set实际草案落地 | 4/5 | 0/5 | 5/5 |
| clear实际草案落地 | 1/1 | 0/1 | 0/1 |
| ready案例实际成功configure | 11/14 | 4/14 | 8/14 |
| 模型请求/完成响应 | 53/53 | 31/31 | 48/46 |

set精确率100%、召回71.43%，草案set分母5仅指资源齐备的set案例，不能替代地域意图全部7set分母。漏set结构化记录2：缺资源例自然文本识别乌鲁木齐却没有record1，另1例超时无法评估语义。合计缺record6（含两例超时），另assistant-city错误clear记录1：实际没有已有草案或地域，助手建议不应变成“取消此前范围”；configure未传scope_change且scope=None，故记录误清1、实际误清0。实际误设0，5条成功set来源绑定/地域策略均正确。自动source错误7/policy错误6含缺记录的连带维度失败，不是7次错误来源写入或6次错误策略设置。

本轮未再出现单字/空白串或不完整JSON。这个结果加强“修复请求协议与供应商schema处理组合可能不兼容”的线索，但同时恢复了工具集、提示、tool_choice，且模型请求未设固定seed，不能据此证明某一个变量的单一因果，也不能保证原B稳定复现首次16/18。原自由调用仍漏记录、多余澄清与漏落地；本次观察到新的错误clear记录。关键反例有1错误记录、1超时无证据，不能声称全部通过。

严格地域自动11/18与两次逐例人工复核一致。原完整流程自动11/18，人工离线复核13/18：adoption-temp-later“已创建调查草案（模拟，尚未启动真实调查）”被完成声明正则误伤；pasted-post正确no_task并询问“请问你希望我做什么”，文字正则漏覆盖。保留原分与逐例离线理由，不改冻结判分器、不替换响应。city-kelamayi和province-xinjiang第4响应已成功configure但原loop_limit仍不改写。keep-rules规则引用本来相同，合理no-op，另列为保持成功不计新configure。keep原完整判分通过也不代表创建过草案：named-resource、creator-kelamayi、denied-city均无configure；clear-unlimited仅记录后再次询问应用，未落地。无真实执行工具尝试/启动声明；assistant-city声称取消不存在的模拟范围单独保留为语义错误。

全部18例/46响应已由实施会话及独立只读复核核查；自然动作/目标另列14正确、1部分理解后多余澄清、1错误、2运行失败不可观察，不能拿自然文字替代严格结构化记录。

| 案例 | 请求/响应 | 严格record | 实际草案配置 | 人工复核 |
|---|---|---|---|---|
| city-kelamayi | 4/4 | 通过 | 成功 | 完整克拉玛依set、默认策略与当前用户引用；第4响应已模拟配置，scope及资源引用正确。loop_limit只表示没有第5次文字确认，不是草案未落地。 |
| only-kelamayi | 3/3 | 通过 | 成功 | 完整克拉玛依set、local_only与排他原话；成功创建模拟草案并明确尚未启动真实调查。 |
| keep-denial-clear | 3/3 | 通过 | 成功 | 正确keep，update标题为复查；克拉玛依范围、策略及两资源引用保留。 |
| change-daqing | 2/2 | 通过 | 成功 | 同一响应record与update，正确换为大庆、默认策略，当前换地域引用完整；无关资源保留。条件式未来启动建议单列，不是已经执行。 |
| adoption-temp-later | 3/3 | 通过 | 成功 | 引用同轮较早用户克拉玛依要求，资源采用后set正确落地。原完整判分误伤“已创建调查草案（模拟，尚未启动真实调查）”，仅离线解除误伤，原判保留。 |
| province-xinjiang | 4/4 | 通过 | 成功 | 完整新疆set与用户引用；第4响应配置成功，资源引用与scope正确。loop_limit未改写，实际落地通过。 |
| keep-rules | 4/4 | 失败 | 同引用no-op | 没有record；读取规则和已有草案后确认ruleset_ref本来相同，合理no-op。成都scope、策略及词库保留，不把no-op计作新configure。 |
| defer-missing-all | 2/2 | 失败 | 未配置 | 没有record；自然文本认出乌鲁木齐欠薪并正确说明资源不足，但重复询问生成授权/地域策略，明确地域未结构化保留。不能补成set通过。 |
| adoption-temp-first | 2/1 | 失败 | 未配置 | 先query资源，第二次模型请求45秒ReadTimeout；没有record或地域自然判断，语义未知。保留为失败分母，不重试、不归为确定模型理解错误。 |
| ambiguous-chaoyang | 2/2 | 通过 | 未配置 | 完整clarify问题及自然澄清，未猜朝阳目标、未配置草案。追加默认策略确认不是必须，作为冗余问题保留。 |
| new-round-national | 1/0 | 失败 | 未配置 | 第一次模型请求ReadTimeout，0响应；独立新轮keep无法评估，不因未调用工具就判反例通过。 |
| named-resource | 4/4 | 失败 | 未配置 | 没有record或configure；读取后明确通用搜索词不含克拉玛依、可做全国调查，但又反问全国还是当地，属不必要澄清/流程未完成。实际scope未误设。 |
| pasted-post | 2/2 | 通过 | 未配置 | 正确no_task，说明转发内容不构成任务并询问用户目的。原complete文字正则未覆盖“请问你希望我做什么”，人工满足澄清目的；未擅自创建。 |
| creator-kelamayi | 1/1 | 失败 | 未配置 | 自然文本明确作者背景不等于任务地域，scope不新增；没有record/工具/草案，重复询问已有创建授权，不能把final计作落地。 |
| denied-city | 2/2 | 通过 | 未配置 | 正确keep，不把例子当地域；随后要求补调查主题，无configure，地域判断通过但草案未落地。 |
| clear-unlimited | 2/2 | 通过 | 未配置 | clear及取消原话完整正确；仅record后再次问是否应用，没有configure，原草案地域仍在。未虚假声称草案已清除。 |
| keep-title | 3/3 | 通过 | 成功 | 正确keep并update标题十月欠薪专项；原克拉玛依范围、策略及资源保留。 |
| assistant-city | 4/4 | 失败 | 成功 | 既有草案为None，助手建议克拉玛依不是用户范围；却record clear并声称取消此前地域，预期keep，属关键反例记录误清1。configure未带scope_change，实际新草案scope=None正确，实际误清0。 |


原始证据：[包装脚本](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-original-retest-v1/run.py)、[准备请求](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-original-retest-v1/prepared.json)、[调用前检查](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-original-retest-v1/local-checks.json)、[全部原始结果](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-original-retest-v1/results.json)、[HTTP日志](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-original-retest-v1/http-ledger.jsonl)、[运行状态](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-original-retest-v1/run-status.json)、[进度](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-original-retest-v1/progress.txt)。

交接证据：[逐例人工复核](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-original-retest-v1-review.json)、[指标与三版对比](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-original-retest-v1-summary.json)、[最终审计](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-original-retest-v1-audit.json)。原/修复版原始结果及归档均保留，他人业务修改保留，没有正式service/数据库/worker/MediaCrawler/生产接入，没有提交、推送、部署或进入P1。原验收记录未改写。

框架状态：恢复时390项针对性及25项既有回归通过，本次执行前重复9项预算控制子集通过，不把它们加成399项新测试；原代码未改，不重复与本轮无关的大范围检查。模型状态：原B18dev真实复测已完成，有上述缺口/超时，未达到95%建议门槛及关键反例要求；复用合成开发集，不是独立留出验证，不能宣称生产识别稳定。停止点：**P0真实模型复测已完成、待原会话验收；P0未整体通过**。

可转交原验收会话：完全恢复原B后同模型18dev一次复测，31项原代码/18初始请求一致，无强制工具或提示变化；48请求46响应2超时无重试。严格地域11/18、set精确率5/5召回5/7、草案set5/5/clear0/1、实际配置8/14。没有修复版单字/空白串异常，但6缺记录与关键错误clear记录1仍在；实际误设/误清0。自动原完整11/18、离线复核13/18，误伤理由及全部失败保留。证据绝对路径如上，待原会话验收，不能整体通过或进入P1。


## 2026-10-14 原桥接最小修订：四候选真实对照（待原会话验收）

用户先要求尝试最小修订，随后明确授权“如果成功改完 都实际测试一下看看有没有提升效果”。保留原B及历史结果，追加confirmation（减少重复创建/应用确认）、state（实际scope判断keep/clear）、recording（先记录且显式承接）、combined（组合三项）四候选。无强制tool_choice、工具分阶段、自动scope注入或参数纠错；原run_arm、完整工具/schema、4响应12工具不变。只读进度诊断区分记录、配置、最终回复，不改原termination及判分器。

先新增测试再实现；最初缺新模块的收集失败保留，不当作语义缺陷复现。首轮手写读取参数不符fixture的测试已纠正，原失败日志保留。独立审查发现已有相同scope被误认为本轮set承接、clear后改标题被误报失败两处诊断问题，先补失败回归再修。最终45项新增、总435项针对性及25项既有回归通过；已有python_multipart弃用警告仍在。默认真实入口阻断，须enable和授权标签，fake-real接线测试不联网。

### 授权、冻结及调用

沿用deepseek-v4-pro-ga-260813、https://api.qhose.net/v1、thinking disabled。仅从用户指定私有JSON读取CONVERSATION_MODEL/BASE_URL/API_KEY三项；未注入旧资源生成Flash、审核模型或云端配置。配置来源 `/Users/ext.wanghongtao6/Documents/Codex/acceptance/unified-draft-frontend-20261012/environment.private.json`，业务配置root仍为unified-report-query-phase1工作树；密钥、请求头与完整env不落报告。

版本geo-intent-p0-bridge-minimal-v1，调用前freeze SHA256 `13687cc375f360323b7bc725eca8445eb53d492b35f62c4cfd65d74eb4bc88db`；原31文件、新候选4文件、包装脚本及72份初始请求hash固定。固定18dev四组、72案例运行，每组最多72请求，总288；实际194请求190响应4ReadTimeout，剩余94未使用。每例4响应12工具、45秒，无重试、无holdout调用。四组独立进程并行，原版为历史对照，非同步随机控制；不根据结果改提示或重跑失败。

已执行命令（实验worktree；历史复现记录不授权重复调用，exclusive账本禁止同组重入）：

```sh
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python -B outputs/geographic-scope-intent/bridge-minimal-v1/run.py --prepare
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python -B outputs/geographic-scope-intent/bridge-minimal-v1/run.py --variant confirmation --enable-real-model --authorization-label '用户授权P0最小修订四候选真实评测：固定18dev每组最多72请求总288，无重试无采集不改业务不进入P1'
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python -B outputs/geographic-scope-intent/bridge-minimal-v1/run.py --variant state --enable-real-model --authorization-label '用户授权P0最小修订四候选真实评测：固定18dev每组最多72请求总288，无重试无采集不改业务不进入P1'
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python -B outputs/geographic-scope-intent/bridge-minimal-v1/run.py --variant recording --enable-real-model --authorization-label '用户授权P0最小修订四候选真实评测：固定18dev每组最多72请求总288，无重试无采集不改业务不进入P1'
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python -B outputs/geographic-scope-intent/bridge-minimal-v1/run.py --variant combined --enable-real-model --authorization-label '用户授权P0最小修订四候选真实评测：固定18dev每组最多72请求总288，无重试无采集不改业务不进入P1'
```

### 完整分母与逐例复核

| 版本 | 地域自动/人工 | set精确率/召回率 | 草案set/clear落地 | 实际配置ready | 原完整自动/人工 | 请求/响应/超时 |
|---|---|---|---|---|---|---|
| 原B首次（历史） | 16/18 | 7/7 / 7/7 | 4/5 / 1/1 | 11/14 | 见历史 | 53/53/0 |
| 原B最近复测（历史） | 11/18 | 5/5 / 5/7 | 5/5 / 0/1 | 8/14 | 11/18 / 13/18 | 48/46/2 |
| confirmation | 15/18 / 15/18 | 7/7 / 7/7 | 5/5 / 1/1 | 13/14 | 15/18 / 17/18 | 50/50/0 |
| state | 14/18 / 14/18 | 5/5 / 5/7 | 3/5 / 1/1 | 8/14 | 14/18 / 14/18 | 44/42/2 |
| recording | 18/18 / 18/18 | 7/7 / 7/7 | 3/5 / 1/1 | 8/14 | 13/18 / 14/18 | 53/52/1 |
| combined | 17/18 / 17/18 | 7/8 / 7/7 | 4/5 / 1/1 | 10/14 | 14/18 / 14/18 | 47/46/1 |

四组clear均实际更新scope=null，未停在“是否应用”。confirmation配置13/14（剩余keep-rules为同ref合理no-op），草案完成最明显改善，但漏keep-rules/pasted-post/keep-title三条记录。recording记录18/18、来源策略均正确，但city超时、province四轮未配置、named-resource多余地域追问仍在；配置8/14未高于最近原B。state有denied-city错误clear；combined有朝阳误set与错误配置尝试，被REGION_AMBIGUOUS阻断，不能直接落地组合。组合草案set精确率4/5包含被拒的朝阳错误尝试，不能只统计成功写入。

全部190响应、164次工具参数及模拟回执、72案例已人工审读。严格地域自动/人工一致；原完整判分保留：confirmation的adoption-temp-later/denied-city“已创建模拟草案，未/尚未启动真实调查”误伤仅离线说明，15→17/18；recording的pasted-post自然追问regex误伤仅离线说明，13→14/18；state/combined无修订。原完整指标对keep不要求configure，不能作草案完成率；实际配置单列。自动来源/策略旗标含缺记录，不等于真实写错同样数量来源/策略。

state的city/only、recording的city、combined的city发生ReadTimeout，全部留在分母；recording/combined超时前已经正确记录，不能把落地失败当理解错误。state named-resource及combined keep-rules的合法query读取被fixture精确参数匹配拒绝，是模拟工具覆盖缺口，不是JSON/schema错误；未改fixture重跑替换。四组实际错误scope写入0、真实执行工具调用0、虚假真实启动声明0；combined朝阳仍有不应发生的模拟草案配置企图。未来启动邀约、生成授权前列例词等原流程措辞偏离另列，不当作真实执行。

- confirmation：836.71秒，usage prompt 1259848 / completion 7803 / total 1267651；费用未知。
- state：763.02秒，usage prompt 1058365 / completion 6462 / total 1064827；费用未知。
- recording：893.98秒，usage prompt 1307554 / completion 6862 / total 1314416；费用未知。
- combined：850.40秒，usage prompt 1163880 / completion 6607 / total 1170487；费用未知。

返回usage总计prompt4,789,647、completion27,734、total4,817,381；超时消耗未知，费用未知。并行等待时间不能用四组耗时相加。并行可能影响供应商延迟，历史对照时点不同，不能据超时数量或单次成绩证明因果提升。累计502请求495响应、214案例运行；项目仍34条合成对话，本轮独立样本仍18dev，无新增留出。反复使用开发集、非同步控制、随机/时延波动、fixture覆盖不足均限制生产稳定结论。记录18/18不能代替95%建议门槛及关键反例、完整流程验收。

### 证据与停止点

- [候选代码](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/bridge_minimal.py)
- [独立提示指导](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/bridge_minimal_guidance.json)
- [调用包装脚本](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/run.py)
- [冻结版本](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/freeze.json)
- [72份初始请求](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/prepared.json)
- [指标汇总](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-minimal-v1-summary.json)
- [最终审计](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-minimal-v1-audit.json)
- [独立代码复核](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-minimal-v1-code-review.json)
- [435项针对性测试](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-minimal-v1-targeted-final.txt)
- [25项回归](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-minimal-v1-regression-final.txt)
- confirmation：[原始结果](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/confirmation/results.json)，[请求账本](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/confirmation/http-ledger.jsonl)，[运行状态](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/confirmation/status.json)，[逐例人工复核](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-minimal-v1-confirmation-review.json)。
- state：[原始结果](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/state/results.json)，[请求账本](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/state/http-ledger.jsonl)，[运行状态](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/state/status.json)，[逐例人工复核](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-minimal-v1-state-review.json)。
- recording：[原始结果](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/recording/results.json)，[请求账本](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/recording/http-ledger.jsonl)，[运行状态](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/recording/status.json)，[逐例人工复核](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-minimal-v1-recording-review.json)。
- combined：[原始结果](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/combined/results.json)，[请求账本](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/combined/http-ledger.jsonl)，[运行状态](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/combined/status.json)，[逐例人工复核](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-minimal-v1-combined-review.json)。

**P0四候选真实模型评测已完成、待原会话验收；P0未整体通过。**没有修改正式提示/注册/草案service/搜索/精选/审核/前端/数据库/MediaCrawler，没有提交/推送/部署或P1。原验收记录、原31文件、历史结果及他人修改保留。优先保留confirmation与recording为不同候选，不能把两者优点相加当组合通过。

可转交原会话：原B最小提示四组18dev，435+25本地通过、调用前冻结，194请求190响应4超时无重试。confirmation地域15/18、配置13/14；recording地域18/18、配置8/14与set3/5；state错误clear、combined朝阳误set被阻断，尚无整体稳定方案。三处原完整误伤仅离线说明，全部原判/失败保留。证据绝对路径如上，请原会话验收，不进入P1。

### 全部72案例逐例人工复核表

完整列是原判分口径，实际配置独立列示；详细参数与回执见各组原始JSON。

#### confirmation

| 案例 | 请求/响应 | 地域自动/人工 | 完整自动/人工 | 实际配置 | 人工原因 |
|---|---|---|---|---|---|
| city-kelamayi | 4/4 | 通过/通过 | 通过/通过 | 已配置 | 正确记录克拉玛依set，用户原话子串包含限定证据，默认local_first_with_supplement正确；读取资源后第三轮配置，第四轮最终回复。没有再问是否创建；回执scope与记录一致。最终未来性启动/排队邀请不是已执行声明，但实验实际启动始终阻断。 |
| only-kelamayi | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确引用“只要克拉玛依，不要其他地区”，记录及草案均local_only；第二轮创建、第三轮说明模拟。无需重复确认，未采集。 |
| keep-denial-clear | 2/2 | 通过/通过 | 通过/通过 | 已配置 | 正确理解否定取消，record keep并仅更新标题复查。模拟回执保留克拉玛依scope及两资源引用，不误clear。 |
| change-daqing | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确set大庆、完整当前用户原话及默认策略；仅显式更新scope，两个资源ref保持不变，随后说明模拟，无重复应用确认。 |
| adoption-temp-later | 2/2 | 通过/通过 | 失败/通过 | 已配置 | 同任务历史用户明确克拉玛依，当前用户已采用展示资源；record及创建均引用历史用户原话，默认策略正确且采用条件满足。原判misleading_task_completion_claim误伤“已创建模拟草案（未启动真实调查）”；全部工具仅模拟，不存在实际启动或声称已启动。离线完整案例改为通过，原判保留。 |
| province-xinjiang | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确省级新疆set，没有猜城市；用户原话子串、默认策略及草案scope正确，第三轮完成最终回复。 |
| keep-rules | 4/4 | 失败/失败 | 通过/通过 | 未配置 | 自然语言正确保留成都范围；查询/读规则/get草案发现用户指定ref已等于绑定ref，合理no-op且未虚构修改。但没有record keep，严格地域记录仍失败。原完整案例可通过，actual_configured为false不能将no-op算配置。 |
| defer-missing-all | 3/3 | 通过/通过 | 通过/通过 | 未配置 | 正确先记录乌鲁木齐set，再查询得到资源为空；不创建占位草案，说明资源缺口并请求生成授权。按原资源生成/采用边界允许的追问，非已具备条件的草案重复确认；not_applied为预期defer，不算落地失败。 |
| adoption-temp-first | 2/2 | 通过/通过 | 通过/通过 | 未配置 | 正确先记录克拉玛依set并查询资源；资源为空时说明缺口、后续生成及采用条件，不越权配置，不自动编造资源。生成授权追问按原边界允许，非草案重复确认。 |
| ambiguous-chaoyang | 2/2 | 通过/通过 | 通过/通过 | 未配置 | record clarify并问北京朝阳区或其他地点，最终列北京/辽宁/长春等候选请用户明确；没有任选地名或配置草案。地域澄清必要，另附可选主题提问未形成设置错误。 |
| new-round-national | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确记录keep，不继承上轮克拉玛依，也不误clear。创建新的全国模拟草案，省略scope_change，回执scope=None；不复制旧草案。 |
| named-resource | 4/4 | 通过/通过 | 失败/失败 | 已配置 | 正确识别含地名资源名不是全国调查地域，record keep、创建省略scope_change并scope=None。第二轮重复详情查询后第三轮read资源，第四轮已成功配置全国模拟草案；loop_limit仍保留，落地成功而最终回复未完成。没有再问全国还是克拉玛依，原完整案例仍因loop_limit失败。 |
| pasted-post | 1/1 | 失败/失败 | 通过/通过 | 未配置 | 文字正确识别只是转发、没有任务，必要询问用户要调查/分析还是仅记录；没有创建或猜地域。但漏no_task记录，严格记录失败，原完整案例通过。 |
| creator-kelamayi | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 明确账号作者背景不等于内容地域，record keep；创建creator模拟草案绑定规则及指定主页，省略scope_change，回执scope=None；背景地名保留在标题/目标描述而非scope。不再问是否创建；说明未采集。 |
| denied-city | 3/3 | 通过/通过 | 失败/通过 | 已配置 | 正确理解克拉玛依只是被否定的举例，record keep并创建全国模拟草案，scope=None。原判misleading_task_completion_claim误伤“已创建模拟草案（尚未启动真实调查）”；离线完整案例改为通过，原判保留。最终仅未来邀请，并非已启动声明。 |
| clear-unlimited | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确clear，引用取消原话；第二轮显式更新relevance_scope_change成功，scope=None且两资源ref保留。没有“是否应用”的重复确认。最后说明仅模拟且未正式写入。 |
| keep-title | 2/2 | 失败/失败 | 通过/通过 | 已配置 | 只更新标题为十月欠薪专项，scope克拉玛依及资源引用全部保持，行为正确，未追问。但漏record keep，严格记录仍失败；不能以自然语言正确替代记录。 |
| assistant-city | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确record keep，不把助手推荐克拉玛依当作已设置范围，不误clear；创建全国模拟草案省略scope_change，回执scope=None，无重复创建确认。未来确认启动邀请不是已启动声明，实际执行仍阻断。 |

#### state

| 案例 | 请求/响应 | 地域自动/人工 | 完整自动/人工 | 实际配置 | 人工原因 |
|---|---|---|---|---|---|
| city-kelamayi | 1/0 | 失败/失败 | 失败/失败 | 未配置 | 首次请求45秒ReadTimeout；无模型响应、无工具调用，不能判断模型是否识别地域。固定分母失败保留。 |
| only-kelamayi | 1/0 | 失败/失败 | 失败/失败 | 未配置 | 首次请求ReadTimeout，无模型响应或工具调用；local_only理解未知，保留失败分母。 |
| keep-denial-clear | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 用户否定取消地域并仅改标题；正确record keep，configure仅更新标题复查。回执scope克拉玛依/策略及规则词库引用保留，明确内存模拟与尚未启动。 |
| change-daqing | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 完整原话支撑set大庆及默认补充策略；先record后configure更新，回执大庆scope与原引用一致，修订3。最终建议未来可改策略，不阻断本次操作，不是真实执行声明。 |
| adoption-temp-later | 2/2 | 通过/通过 | 通过/通过 | 已配置 | 正确从同任务首轮用户要求引用克拉玛依set来源；当前用户明确采用已展示临时资源，record与configure同响应，回执草案scope/策略/引用正确。未重复询问是否创建；末尾邀请未来确认启动未真实执行，但保留边界表达残留。 |
| province-xinjiang | 4/4 | 通过/通过 | 通过/通过 | 已配置 | record与query同响应，来源片段“查新疆欠薪，在抖音做调查”来自当前原话且覆盖地域证据；随后读取真实fixture引用和资源内容，正确区分词库名称克拉玛依与新疆任务地域。第3响应配置scope新疆默认策略，第4响应正常总结，无loop。条件性未来启动表述未真实执行。 |
| keep-rules | 4/4 | 通过/通过 | 通过/通过 | 未配置 | 先get真实当前草案，再record keep与查询/读取规则；回执新规则引用与已有完全相同，识别无需配置而正常结束。成都scope与策略及词库未变，no-op不能算实际配置。建议另版本时再告知不是重复应用确认。 |
| defer-missing-all | 2/2 | 通过/通过 | 通过/通过 | 未配置 | 先记录乌鲁木齐set，完整原话来源/default策略正确，资源空回执后未配置占位草案。随后询问风险规则侧重与可选关键词/数量，属于资源准备问题，未再确认已经明确的地域或已有草案操作。资源尚未生成，不据此宣称任务准备完成。 |
| adoption-temp-first | 2/2 | 通过/通过 | 通过/通过 | 未配置 | 已正确记录克拉玛依set/full原话与默认策略；query回执规则词库为空，未越权建草案或写资源。追问生成临时搜索词/规则属资源未齐的生成授权，未重复确认地域；本轴成功不代表资源生成成功。 |
| ambiguous-chaoyang | 2/2 | 通过/通过 | 通过/通过 | 未配置 | record clarify明确区分北京朝阳区、辽宁朝阳市/其他朝阳；最终同样必要澄清，不猜测set且无草案。附带“本地优先、还是只要本地”策略选择表述非必要，但未影响本轮歧义处理；不将必要地名追问算重复确认。 |
| new-round-national | 4/4 | 通过/通过 | 通过/通过 | 已配置 | 独立新任务全国且当前无scope，record keep，不继承上一轮克拉玛依或资源名。query/read后第3响应创建无地域新草案，回执scope null、正确引用，第4回复完成。末尾未来改名/启动邀请不阻断已创建草案，未调用启动工具。 |
| named-resource | 1/1 | 失败/失败 | 失败/失败 | 未配置 | 先query options后read_resource按query检索克拉玛依欠薪；该参数符合公开read_resource schema且描述明确允许查询，fixture只提供resource_id匹配，返回FIXTURE_ARGUMENT_MISMATCH后阻断同响应后续ruleset query。无record/no draft，固定失败分母保留，不能归为地名污染或模型参数非法；属于fixture合法路径覆盖缺口，原结果不得替换。 |
| pasted-post | 2/2 | 通过/通过 | 通过/通过 | 未配置 | 正确识别转发帖无调查要求，record no_task，不把正文克拉玛依当用户地域，不配置任何草案。最终说明下一步如想调查再给平台与方向，必要任务意图询问、非重复确认。 |
| creator-kelamayi | 4/4 | 通过/通过 | 失败/失败 | 已配置 | 正确区分作者背景与内容地域，record keep；查询creator能力、读取精确规则引用，第四响应创建creator_url对应模拟草案且scope null、无lexicon误绑定。旧循环仍loop_limit，实际配置成功而最终回复未完成，不能归地域错误。 |
| denied-city | 2/2 | 失败/失败 | 通过/通过 | 未配置 | 当前无草案地域且用户克拉玛依仅举例，应该keep；模型却record clear，并说取消此前地域，是明确状态理解错误。未调用configure，没有实际误清。最终询问缺具体主题，属于调查目标澄清，并非重复地域/应用确认；按ready落地轴仍未配置。原complete只查无scope变更可通过，不能表示地域记录正确。 |
| clear-unlimited | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 已有克拉玛依scope，用户明确取消；完整原话record clear后立即configure update clear，无再次应用确认。模拟回执scope null、revision3，原规则词库引用保留，最终明确未启动/未正式存储。 |
| keep-title | 2/2 | 通过/通过 | 通过/通过 | 已配置 | record keep与configure update同响应，只改标题十月欠薪专项。模拟回执revision3、scope克拉玛依与default策略、规则词库引用保留。最终明确未启动和未写正式存储，无重复应用确认。 |
| assistant-city | 2/2 | 通过/通过 | 通过/通过 | 未配置 | 正确识别助手此前推荐克拉玛依不是实际scope；全国且当前无地域record keep，未clear或set。最终询问缺主题/平台才建草案，这些用户原话未明确；属于必要信息澄清而非重复地域或应用确认。标签ready的实际配置轴未完成，原complete无scope变更可通过不代表草案落地。 |

#### recording

| 案例 | 请求/响应 | 地域自动/人工 | 完整自动/人工 | 实际配置 | 人工原因 |
|---|---|---|---|---|---|
| city-kelamayi | 4/3 | 通过/通过 | 失败/失败 | 未配置 | 正确记录克拉玛依 set、本地优先补充及当前用户来源；随后两次查询，第四请求 ReadTimeout。未配置，已观察地域理解正确；超时后的落地能力未知，不能归为理解错误。 |
| only-kelamayi | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确识别只要克拉玛依为 local_only；记录后直接复用现有完整引用创建，模拟 scope、资源均正确。最终明确模拟及未启动，条件式邀请确认不是已执行声明。 |
| keep-denial-clear | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确理解不要取消地域为 keep；仅更新标题复查，克拉玛依、原策略及规则词库引用保持。最终回复与模拟回执一致。 |
| change-daqing | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确记录并更新大庆、本地优先补充，引用本条用户改查大庆；仅提交地域变更，原规则词库引用完整保留。无重复确认。 |
| adoption-temp-later | 2/2 | 通过/通过 | 通过/通过 | 已配置 | 当前用户明确采用同任务已展示资源；正确引用同任务首轮克拉玛依原话，记录与创建在同一响应执行且回执正确。未再要求采用，未启动真实任务。 |
| province-xinjiang | 4/4 | 通过/通过 | 失败/失败 | 未配置 | 正确记录新疆省级范围与当前用户来源；两次 query 均只返回资源名称，第四响应读取规则词库后额度耗尽。尚未配置，属流程长度/额度问题，地域记录不失败。 |
| keep-rules | 4/4 | 通过/通过 | 失败/失败 | 未配置 | 正确 keep 且成都范围保持；获取草案、查询、读取规则发现新旧引用相同，属于合理 no-op。第四响应重复获取草案导致无 final；不能算实际 configure，完整流程终止仍失败。 |
| defer-missing-all | 2/2 | 通过/通过 | 通过/通过 | 未配置 | 独立正确记录乌鲁木齐 set；查询空资源后说明缺规则词库且不创建占位草案，符合 defer。询问资源生成授权是否多余取决于准备资源的授权解释，不属于地域歧义追问。生成授权前给出例词是原提示文字约束偏离，未有生成/执行调用。 |
| adoption-temp-first | 2/2 | 通过/通过 | 通过/通过 | 未配置 | 首轮正确记录克拉玛依 set；资源为空，保留意图并暂不建草案，符合 defer。询问生成授权，必要性取决于准备资源的解释；例词预览偏离原提示生成授权边界，无实际越界工具调用。 |
| ambiguous-chaoyang | 2/2 | 通过/通过 | 通过/通过 | 未配置 | 正确 clarify，指出北京朝阳区、辽宁朝阳市等多义地名并询问具体地区，无猜测 set，无草案。属于必要澄清。 |
| new-round-national | 4/4 | 通过/通过 | 失败/失败 | 已配置 | 正确 keep，新任务不继承旧轮克拉玛依；读取真实规则词库后确认通用词不带地域，第四响应成功创建 scope=null 模拟草案。loop_limit 只表示缺最终回复，不能报未配置或误继承。 |
| named-resource | 2/2 | 通过/通过 | 通过/通过 | 未配置 | 正确 keep 记录，全国任务未误设为资源名克拉玛依；但随后再次询问全国/当地，要求用户确认资源名称含义而未先读取内容。用户全国要求已清楚，属多余地域确认；未创建草案。原完整判分仅检查不误改 scope，不能代表草案完成。 |
| pasted-post | 2/2 | 通过/通过 | 失败/通过 | 未配置 | 正确 no_task，将克拉玛依识别为转发内容而非任务约束，无配置；明确问你希望我做什么并提供调查/资源/咨询选项。原 response_regex 未匹配这类自然说明，自动完整失败为误伤，离线人工改为通过。举例不是实际地域 set。 |
| creator-kelamayi | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确 keep，作者来自克拉玛依为背景而非内容地域；成功创建 creator 模拟草案，携带规则引用和作者主页且不带词库、scope。最终明确未启动。标题出现作者背景不等于地域工具约束。 |
| denied-city | 2/2 | 通过/通过 | 通过/通过 | 未配置 | 正确 keep，明确否定克拉玛依为任务地域并保留无约束；当前消息未指定具体主题，追问主题具有依据，平台可默认。未创建草案不等于地域误识别。 |
| clear-unlimited | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确 clear，引用用户取消地区原话，随后直接 update 清除旧克拉玛依，scope=null，规则词库保持；没有再次询问是否应用。最终仅声明模拟取消，未启动。 |
| keep-title | 4/4 | 通过/通过 | 通过/通过 | 已配置 | 正确 keep，仅更新标题十月欠薪专项，克拉玛依范围和规则词库均保留；第四响应正常 final，未超额度。 |
| assistant-city | 4/4 | 通过/通过 | 通过/通过 | 未配置 | 正确 keep，未把助手推荐克拉玛依当既有 scope，也未误 clear；两次 query 后询问全国调查主题（输入确未明确）有依据，但再次要求用户说明是否直接沿用现有规则词库重复了已有采用要求。未创建草案，不能算落地成功。 |

#### combined

| 案例 | 请求/响应 | 地域自动/人工 | 完整自动/人工 | 实际配置 | 人工原因 |
|---|---|---|---|---|---|
| city-kelamayi | 2/1 | 通过/通过 | 失败/失败 | 未配置 | 完整正确set、用户来源及默认策略已记录；第二请求ReadTimeout，无资源查询/配置，地域理解已观察正确但草案执行未完成。 |
| only-kelamayi | 4/4 | 通过/通过 | 失败/失败 | 已配置 | 完整local_only set、来源及正确模拟配置；第4响应配置成功，无第5次最终回复，保留loop_limit，草案落地成功。 |
| keep-denial-clear | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确keep并更新标题复查，克拉玛依scope与资源保留；无再次应用确认。 |
| change-daqing | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确set大庆、完整当前用户来源、默认策略；实际模拟更新，资源保留，无重复应用确认。 |
| adoption-temp-later | 2/2 | 通过/通过 | 通过/通过 | 已配置 | 引用同任务较早用户克拉玛依要求；同一响应record和configure，临时资源此前已采用，正确落地和模拟总结。未来邀请启动未实际执行。 |
| province-xinjiang | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 完整新疆set，正确短来源片段；记录和配置后最终回复在四轮内完成，未受词库地名污染。 |
| keep-rules | 2/2 | 通过/通过 | 失败/失败 | 未配置 | 正确keep记录；合法read_resource(kind=ruleset,query=综合舆情)未被fixture覆盖而中止，不归为JSON/schema错误或地域理解错误。原失败保留。 |
| defer-missing-all | 2/2 | 通过/通过 | 通过/通过 | 未配置 | 正确记录乌鲁木齐set；资源为空不建占位草案；询问资源生成授权，非必要地域澄清。生成前列具体例词的原提示合规局限单列，不修订地域分。 |
| adoption-temp-first | 2/2 | 通过/通过 | 通过/通过 | 未配置 | 正确记录克拉玛依set；空资源后defer，未提前采用/配置；询问生成资源授权，并列出具体例词，单列原资源流程约束局限。 |
| ambiguous-chaoyang | 2/2 | 失败/失败 | 失败/失败 | 未配置 | 错误set朝阳并将草案标题写成朝阳区，未先澄清；尝试配置被REGION_AMBIGUOUS拒绝。关键反例误设记录1、错误草案set尝试1，实际草案未写。 |
| new-round-national | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确keep，查轮次后创建全国独立模拟草案，scope null，没有继承旧克拉玛依，最终说明正确。 |
| named-resource | 3/3 | 通过/通过 | 通过/通过 | 未配置 | 正确keep，但重复query未取得关键词快照，未实际read_resource，随后从词库名称猜测地域覆盖，再问全国/克拉玛依，草案未创建。地域结构记录通过，资源适配/多余地域追问导致流程未完成。 |
| pasted-post | 2/2 | 通过/通过 | 通过/通过 | 未配置 | 正确no_task，指出粘贴内容无任务要求，询问用户任务，没有擅自创建或设置地域。 |
| creator-kelamayi | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确keep并创建creator草案scope null，账号作者背景不当地域；无重复创建确认。末尾可选调整/未来启动邀请不算当前执行声明。 |
| denied-city | 2/2 | 通过/通过 | 通过/通过 | 未配置 | 正确keep，未将否定举例当地域。询问缺失调查主题，未配置；原用户无具体主题，不把整段追问当地域理解失败，平台/资源问题冗余单列。 |
| clear-unlimited | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确clear来源并实际模拟更新为scope null，原资源保留，无再次是否应用确认。 |
| keep-title | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确keep，get draft后只更新标题，原地域/策略/资源保留。 |
| assistant-city | 3/3 | 通过/通过 | 通过/通过 | 已配置 | 正确keep并创建全国scope null草案；没有把助手建议当已有地域，旧错误clear记录未重现。 |


## 2026-10-15 保留减少重复确认版本：按业务目标整理验收口径

当前状态（2026-10-15）：**按用户选择保留减少重复确认版本（geo-intent-p0-bridge-minimal-v1 / confirmation），地域识别与草案落地结果已整理、待原会话验收；P0未整体通过。** 该组18dev、50请求50响应；set识别精确率/召回率均7/7，可落地地域写入5/5，缺资源2例正确暂缓，clear落地1/1；整体实际配置13/14，严格实验记录15/18。本次新增真实请求0，累计502请求495响应不变，未进入P1。

本次仅保存候选选择与整理证据，不改变冻结代码、提示、工具、评分、默认参数或历史结果。地域识别与草案落地分开报告：7/7地域识别包括5条可落地和2条资源缺失暂缓；5/5只代表可配置地域正例，不代表18条完整通过。整体配置13/14含无地域要求的案例，剩余keep-rules为同ref合理no-op；严格记录15/18的三条漏记仍失败。named-resource第四轮已配置成功但无最终回复，原loop_limit不改。原完整自动15/18、人工离线17/18，不等于实际配置完成率。没有为保留版本追加模型请求或重跑留出。

冻结 API 默认仍为 combined，选中版本必须明确指定 variant='confirmation'，本次不更改默认值来扰动已评测字节。其他候选仅保留为对照，不混合使用。正式卡片展示未测试，不以模拟草案回执宣称正式卡片已验证。

完整业务指标、七条set与全部十八例索引、历史比较、剩余缺口、冻结SHA、原始证据和转交摘要见 [选中版本验收交接](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/CONFIRMATION_HANDOFF.md)；[选择及原始文件保护清单](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/confirmation-selection-20261015.json)。既有435项针对性及25项回归结果保持，本次文档变更只做哈希、指标和链接一致性检查。


## 2026-10-15 自然事件原句补测：未完成草案配置

用户补充授权原句“调查克拉玛依大火的舆情”，随后要求绕过TUN并继续DeepSeek Pro。保留原confirmation代码/提示/schema，新增一条独立探针及火灾主题模拟资源，不改固定18dev。首次SSL失败1请求0响应保留；en0直连4请求4响应，TLS校验通过，无自动重试。模型记录keep，之后query两轮、read一轮（两工具），四轮结束，没有configure，补测草案0/1。语义理解未由草案证明，不把预置事件词库当模型理解证据。冻结set标签原判失败保留；事件主题与显式地域限制不等价，不能仅以keep断言理解错误。第三轮请求详情但fixture仅回列表，是循环预算的影响因素；本轮不修提示、不追加轮次。项目累计507请求499响应，旧5/5固定dev结果不改。

完整输入、两次尝试、逐轮工具行为、原判与人工限制及绝对证据路径见 [自然事件补测](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/NATURAL_EVENT_PROBE.md)。保留减少重复确认版本，P0未整体通过、待原会话验收；未改正式业务或进入P1。
