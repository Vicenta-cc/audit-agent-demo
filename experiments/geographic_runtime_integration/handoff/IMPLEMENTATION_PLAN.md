# 地域调查正式运行路径接入实施计划

日期：2026-10-15。状态：**只读调研及实施计划完成，待原会话审阅；尚未实施正式接线。**

本计划承接 P0–P5 主方案，细化尚未完成的 P2/P3/P4 正式接点与 P5 最小展示。下文 R0–R6 是本次接入工作包，不重定义原阶段编号。当前用户目标是先验证“前端发起 → 真实采集 → 地域精选 → 原审核 → 报告”可用，精选质量优化单独后置。规划不解除地域启动阻断，不启动采集、调用模型、迁移账号、改运行环境或提交业务代码。

配套 [源码摘要清单](./geographic-relevance-runtime-source-inventory-20261015.json) 固定调研所引用的当前文件；它是规划基线，不是未来实现的验收清单。

## 1. 基线、现状与完成含义

实施工作树固定为 `/Users/ext.wanghongtao6/.codex/worktrees/geographic-integration/xhs-audit-agent-demo`，以下简称 **W**。引用源码均以 W 为根，行号对应下面的确切提交。开始实施时先复核 HEAD/工作树差异和附带源码摘要清单，保留其他人的新增修改，不 reset。

| 版本 | 固定值与含义 |
|---|---|
| 应用 GitHub main | `a4997d3969beab3d499a218e7a7aee025fea46d9`；远端 `git@github.com:Vicenta-cc/audit-agent-demo.git`，是此前本次会话读取值，不是云服务器部署证明 |
| 最终 P1 | `031e0cd26379ee44c2372c9a28536c377ab3d4b9` |
| 离线检查点 | `9b20f342dddbf6172596b2b440d3e19559a766a3` |
| 本地整合分支 | `codex/geographic-integration`，HEAD `9b36fbc2b5b3341bf7eb88bc8495ba08e8b4c363`，两个父提交分别为上述 main 与离线检查点；tree 与离线检查点相同 |
| 固定 P3 判定基准 | tag `p3-post-only-names-v2-local-20261015` → `2120c53379d1b79c45e3cb8232b9c9b96657b183`；原导出及旧证据保持不变 |

本地配置的 `origin` 指向另一个 `saas-demo` 仓库，不能用 `git push origin` 猜测提交目的地。本轮及本计划不含推送、部署或云服务器改动。

现状：P2/P3/P4 模块和离线联调已存在，但正式 pipeline 未消费它们。P2/P4/P3 输入都限制为 fixture；P4 存储拒绝业务库。不是从零重写搜索、评分、审核或报告，也不是打开一个开关即可运行。[E1–E6]

三种结果分别验收：

1. **离线正式路径集成通过**：走真实 service/store/Run/Job/pipeline/报告代码，以隔离数据库和受控外部响应验证，不能只跑实验 JointHarness。
2. **隔离环境功能冒烟通过**：从前端输入“调查克拉玛依大火”，明确克拉玛依市范围，有限额地执行真实模型/采集/审核并得到可追溯报告。若无合法入选，只能判定正确结束/阻断，不能宣布正向报告链验证通过。
3. **正式启用**：另行原会话验收、质量及部署决策。本计划的本地功能冒烟通过不代表可上线，不用历史 11/12 或人工响应成功率声称质量达标。

## 2. 已确认缺口与源码证据

| 编号 | 当前事实与证据（W 内相对路径） | 应补的能力 |
|---|---|---|
| E1 | `backend/investigation_creation/scope_search_plan.py:217` 的计划、`:334` 的 profile 校验均 fixture-only；`:569` build、`:607` validate、`:619` parse 可复用算法 | 正式 authority 捕获、显式来源契约、确认事务冻结与独立预期摘要 |
| E2 | `backend/investigation_creation/contracts.py:763` 执行契约缺地域计划；`store.py:1277` 确认事务、`resources.py:156` confirmation_fence/`:749` resolver 已有；`adapters.py:501` ensure_job/`:637` 校验有字段白名单 | 地域计划穿透配置、Run、Job、worker 与恢复；旧 v2/v3/v4 哈希兼容 |
| E3 | `experiments/geographic_execution_plan/dependencies/p3/relevance_scope.py:51`、`:75`，`relevance.py:207` 锁定 fixture；`relevance_post_only_names_v2.py:62` 准备和 `:86` 重验都使用此链 | P3 正式来源契约扩展，不改原判定、提示和名录 |
| E4 | `backend/audit_agent/scoped_selection.py:41` fixture gate；`scoped_selection_store.py:18` 路径/表限制；`scoped_execution.py:18` 注入端口 | 正式计划入口、业务存储适配、真实 capture/detail/account/stop 端口 |
| E5 | `backend/audit_agent/pipeline.py:815` 与 `:2111` 仍逐词评分后立即精采；`:2225` 评分、`:2233` 排序、`:2279` detail | 地域专用全词候选屏障和既有 P4 最终选择编排；无地域继续旧路径 |
| E6 | `pipeline.py:457` callback 立即入审核链；`:2153` 恢复旧 detail_completed 直接再投 callback；`scoped_execution.py:131` 已有先 review 后 deliver | 同时隔离正常与恢复的交付入口，精采后的证据先复核再入库送审 |
| E7 | `backend/audit_agent/ingestion.py:954` ingest_batch 以 batch_path 去重；`:1317` 内容审核状态；`worker.py:583` 完成闸门 | 稳定交付键与持久化 membership 的绑定、跨库对账，不能把入队等同已落盘 |
| E8 | `backend/reporting/integration_source.py:221` canonical_rows 只消费已完成审核；`:240` coverage 的 candidate_posts 是已入库数量 | 独立地域覆盖/筛选统计，按 source_job_id/content_key 绑定报告依据，不改原风险分母 |
| E9 | `backend/investigation_creation/scope_intent.py:231` blocker，service `:571`、store `:1312` 都拦截；前端 `confirmationView.ts:68`、`TaskConfirmationCard.tsx:111` 展示并禁用 | 仅在隔离验收环境具备完整能力时允许测试任务；默认生产能力继续关闭 |
| E10 | `experiments/geographic_selection/REPORT.md:129` 起有风险绑定返修及反例，末尾仍待原会话复验；`experiments/geographic_joint_integration/REPORT.md:3` 同样声明未获原会话复验 | 独立复验并绑定当前源码，不能用作者自测替代 |
| E11 | `backend/audit_agent/config.py:147` 默认 triage=off，`:150` 筛选模型与对话模型独立；`crawler_account_store.py:386` 可用账号选择已有 | 显式确认测试任务为 select、使用实际冻结模型/账号，不能暗开精选或把 DeepSeek 对话配置当所有模型配置 |

当前 P1 环境不是完整采集环境：`/Users/ext.wanghongtao6/Documents/Codex/runtime/geographic-p1-event-place-20261015/launch_backend.py:22` 指向不存在的 disabled-crawler，测试库账号数为 0。该启动器 `:15` 使用代理 8996，不能按较旧说明盲目重启 8994。历史 `runtime/independent-retest-20261013/business/data/audit_index.sqlite3` 有一条 dy active 记录及相邻密钥文件，最近使用记录 10-13；仅是候选来源，不证明登录仍有效。账号材料未解密、复制或联网验证。此前只读结果见 [环境核验](</Users/ext.wanghongtao6/Documents/Codex/handoffs/geographic-p234-offline-20261015/FRONTEND_READINESS.md>)。

## 3. 先固定的设计与边界

### 3.1 正式来源契约不能假冒 fixture

**必须先处理的设计差异**：固定 P3 公共接口不仅检查 fixture，也把它纳入输出和摘要。[E3] 不能仅在外层检查 owner，再将真实任务改标 fixture；不能 monkeypatch 校验器或给正式任务使用实验 DB 路径。

建议保留固定 P3 tag 的原样导出作旧版回放基准，另外建立显式版本的运行时契约包及导入清单，只扩展来源校验、输出和摘要封装。原 names-v2 的模型提示、地点名称绑定、行政判断、IP 统计、档位判定保持；相关原语义函数逐字或 AST 差分与逐例回放验证。新增正式 context/profile 的版本与判定基准版本分别记录，不把衍生包谎报为原 tag 全字节相同。

这属于**拟议的非语义契约扩展**，不是本轮已经实施、已经验收或旧固定 P3 约束自动允许的变更。下一轮实施启动文本需要明确纳入此范围；若仍要求所有 P3 衍生代码字节绝对不变，此接点保持阻塞，不能靠改来源标签继续。原 tag、原导出和历史报告无须改动。即使新来源合法，也必须由正式服务端做权限/确认校验，摘要本身不是授权。

P2/P4 同样增加显式正式契约版本，保留旧 fixture 校验和实验 runner。把可复用纯派生/校验从实验适配中提取到 backend，再由实验入口包装回用；产品不得 import 实验 harness、guard、默认测试配置或人工 client。复用一个 P4 选择器/状态转换核心，不复制第四套算法。[E1、E4]

### 3.2 冻结字段与身份

建议正式 envelope（具体字段名在 R1 契约测试中定版）：owner/session/draft/revision、计划 schema/version/digest、scope/policy、原样有序 queries 及来源、有效采集参数、完整风险输入、P3 判定与运行时契约版本、名录/prompt/model/profile 摘要。模型密钥、cookie 不进入快照。

- 计划主体与 `run_id/job_id/execution_id` 运行绑定分层：确认事务内校验权威草案/资源后保存计划和 Run 绑定；Job 按已有 `job_id_for_run` 派生。不要为预知 job_id 在确认事务外提前建 Job，不构造包含自身 config_hash 的循环摘要。[E2]
- 原查询词/顺序/来源不变，评论 0/false 不当缺省，search_terms_max 保持 1–200；每词名额和总额来自 resolver，不因要测多条而修改 P2 默认。
- 风险规则取 `prompt_profile_snapshot`，冻结 terms/search_groups、认证过滤、模型及评分参数；新地域恢复不读实时词库/规则补缺。使用原 TriageEngine/terms_for/score/rank_candidates，不改引擎私有缓存。[E1；`experiments/geographic_execution_plan/frozen_risk.py:109–154`]
- 正式确认/Job 状态是外部可信绑定来源；恢复不能从待验 payload 自取 digest 再验证自己。

### 3.3 原始风险、地域判断与精采证据

首次 capture 保存完整原始 post/comments、rank/source_keyword、账号标识、缺失/截断信息及不可变摘要；RiskRecord 精确对应 raw_source，P3 使用规范化投影。首次评分落盘、query 完成屏障和缓存加载均校验原文、评论、身份，不只校验自摘要。[E10；`scoped_selection_store.py:94、:105、:213`]

精采后是另一份证据：保留首次 RiskRecord 和原排序，只做必要的地域复核。同档正常变化可交付，不因新增认证字段重新评分；变档/不足/失败不交付，成功采集预算仍结算。原正式内容审核继续基于允许交付的精采证据执行，不能把初筛 weak/strong 当正式审核 high/medium。[E6–E8]

P3 只看原帖定义的文字证据；评论文字仍供原风险逻辑使用，地域侧仅做原有用户/IP统计。local → province_unknown → ip_only；同档原词序和原 rank_candidates 顺序；IP 仅有效 strong。local_only/普通省级目标不扩补。保留固定 P3 的已知语义限制，不增加关键词地理判断、重写查询词或调整阈值。

### 3.4 持久化、交付与终态

- 建议新增 `backend/audit_agent/geographic_execution_store.py`，在 JobStore 所用库新增有命名空间的执行/事件/尝试/预算表；共享现有 P4 状态转换，不放宽实验 Store 的限制。唯一约束区分 occurrence（同帖跨词独立风险）与 execution/content 的精采成功归属，选中才预留、成功才结算。[E4]
- 正式交付账本放在 ingestion/membership 所在库，交付幂等键、run/job/execution/attempt、计划和初始/精采证据摘要与 membership 在同一事务写入。P4 状态库与 ingestion 库之间不假装有单一事务：采用稳定键写入、查证确认、幂等对账，允许内部重试，不承诺外部网络严格一次。[E7]
- `deliver` 只有验证持久化接收记录后才能确认；入队成功不是交付落盘。旧 batch_path 去重仍复用，但不能单独证明地域绑定正确。
- 现有 `ingestion.py:1064` 的 shared_complete 分支可能把当前 membership 指向旧 shared_raw_path。地域交付须保存不可变的 delivery/evidence 专属原始文件，membership、正式审核输入和重启恢复都核对该文件摘要；不能账本记新精采摘要、审核实际读旧共享内容。无地域原共享行为保留。
- 未知 detail 结果保留预留并待可信 receipt 对账；不能超时释放后重复采集。已入库但未记 delivery receipt 则查账本补认，不重新精采。[E6、E7]
- 建议首版存在未知 receipt、未完成 query 或未交付待恢复项时不发布“成功完成”的最终报告。合法终止但零交付时显示原因卡与计数，沿旧机制不伪造报告帖子；部分失败沿原失败/终止规则处理，不新增自动发布部分成功策略。[`worker.py:583`]
- 原验收 WATCH 仍非本轮返修阻塞。新增正式账本契约是本次接线需要，不回头改旧可选 status 协议来重写原验收结论。

## 4. 实施顺序与文件责任

### R0：准入证据与可复跑基线

1. 核对 W HEAD/差异、固定 P3 文件、209 个上游导入及返修清单；原会话对 P4 HIGH 的复验若仍缺，记录为待验收，不能由本计划或后续作者自测抹去。可先写契约及离线测试；正式测试启动前关闭该项。[E10]
2. 明确 R1 的 P3 来源契约扩展范围；保留旧原样依赖，不跟踪移动分支。
3. 先锁回归：同 ID/query/rank 的原文、评论、认证变化拒旧风险；正常恢复；精采同档变化；原排序/双预算。复跑历史联合 87 项与 P4 原 284 项覆盖范围，数量仅为历史参考，按实际收集报告。P2 四项已知失败保留 node/message 和非零退出，不能 skip/改断言冒充全绿。
4. 产物：INPUT_MANIFEST、原会话复验引用、BEFORE_TESTS；源码与证据摘要分开。若历史 audit 硬编码 HEAD=031e0cd，不修改其旧证据；新增适用于整合提交的边界核验。

### R1：正式计划、来源和冻结贯穿（P2/P3 契约接入）

**修改现有**：`scope_search_plan.py`、`contracts.py`、`resources.py`、`service.py`、`store.py`、`frozen.py`、`adapters.py`、`audit_agent/job_store.py`。**建议新增**：`investigation_creation/geographic_runtime.py`（服务端 authority/版本绑定）、`audit_agent/geographic_p3_runtime/`（带清单的显式契约版本）、`audit_agent/geographic_bridge.py`（生产端口），必要时 `audit_agent/frozen_risk.py`（从实验提取纯冻结适配）。这些新增路径是规划名称，不是已存在文件。

复用 `resolve_authoritative_draft`/`confirmation_fence`/`resolve_confirmation`，在 store 确认事务内重验来源与 revision，拒绝前端手填的计划/摘要。建议新增 `investigation-run-config-v5` 专用于地域任务，旧无地域仍按原 v2/v3/v4 读写，保持 JSON 和 hash 不变；同步 parse、ensure_job 白名单、Job schema/序列化、worker 校验。该新版本名是设计建议，不是当前已有合同。Run 创建后 Job 创建失败可重试同一个确定性 Job 身份，不重建计划。[E1、E2]

P3 迁移逐项清点所有 fixture 来源校验、返回值和参与摘要位置；正式版本不得遗留 fixture 冒名。固定人工响应下对照旧版语义结果、请求模型文本及名录，不要求来源摘要和新封装版本相同。产品路径不得引入实验默认 profile。完成 R1 仍不开放生产启动。

**验收**：四类正式/临时资源组合；跨用户/会话/旧 revision、资源内容/查询词/身份自重算 hash 都拒绝；确认失败无半成品；旧缺省序列化不变；Run/Job/重启摘要一致；fixture 不能启动正式任务；变更实时规则/词库/设置后冻结请求不变。

### R2：正式状态、捕获与原风险/P3 端口（P4 接入上半段）

依赖 R1。修改 `scoped_selection.py`/`scoped_execution.py` 的正式入口及共用核心，增加 `geographic_execution_store.py`、`geographic_bridge.py`；保留原实验入口和 Store 防护。[E4]

在 `pipeline.py` 的分派处增加有合法地域计划才进入的新执行分支，使用现有 MediaCrawlerAdapter、AccountRotationManager、stop/crawl epoch；不改 MediaCrawler 源码。候选 capture 关闭媒体且不传正式审核 callback；把真实采集参数执行回执与原始 item/comments 绑定，先落 capture 再评估。[E5]

全词完成屏障之前不得 detail/补充；query 成功空结果与登录失败/限流/中断分开。账号使用现有权限/租约和轮换，不靠复用同一文件目录冒充账号授权。正式状态转换、外部动作认领及交付写入校验当前 worker lease/crawl epoch，过期 worker 不得继续预留或提交结果。只有恢复到未执行的步骤才调用外部服务，已落盘风险与地域结果绑定正确时复用。[E4、E5]

**验收**：晚词 local weak 优先早词 province strong；同档不跨词比总分；跨词同帖不同风险上下文、后词可用名额、并发不双占；原风险 prompt/score/排除/排序与基线直接相等；登录/限流/stop 不算“本地不足”；无地域 off/select/compare 路径与旧恢复不变。

### R3：精采、复核、持久交付与恢复（P4 接入下半段）

依赖 R2。修改 `pipeline.py`、`ingestion.py` 和新 store/bridge；复用 `scoped_execution._finish_collection` 的先复核后交付顺序。给地域任务隔离 `run_detail` 的提前 callback，以及旧 detail_completed 扫描恢复入口；无地域保持原行为。[E6]

实现 3.4 的账本和对账。后台审核仍从原 ingestion/ref 链进入 `_persist_audit_result`，不换风险引擎、风险公式或正式审核器。将 collected、handed_off、审核 completed 区分，扩展 worker 完成条件，未知状态不得冒充完成。[E7、E8]

**验收**：after_reserve/begin_attempt/detail 返回未记 receipt/settle/review/入库未回执/审核完成标记前各断点注入并独立进程恢复；不得重采、双计或重交付。精采同档变化保留首次风险且正常交付；变档或 P3 失败无 membership/正式审核/报告帖子；旧 detail 文件或错误 run/attempt 回执不能绕过。同帖不同 Run 的精采内容不同且已有 shared_complete 时，正常执行及重启审核均读取本次获准证据，不能只证明账本摘要相同。

### R4：最小状态、依据接口与报告展示（P5 功能范围）

依赖 R3。修改 `investigation_creation/adapters.py`、`worker.py`、`api/investigation_creation.py` 的只读状态投影；新增按 Run 授权、分页/固定枚举过滤的筛选记录接口，复用 principal→run→job 校验。不得暴露任意路径、账号凭据或跨用户原始证据。

报告增补：`reporting/integration_source.py` 的 source snapshot 增加独立 geographic_coverage/selection_basis，经 `integration_graph.py`、`r31_graph.py`、`pass_graph.py`、`presentation_projection.py` 传播；聚合源按 source_job_id/content_key 匹配，发布版本冻结，不能读取今天的可变筛选状态重写旧报告。[E8]

前端增补：`Audit_assistant/src/types/investigationCreation.ts`、`types/investigation.ts`、`types/reports.ts`、对应 services、`TaskConfirmationCard.tsx`、`InvestigationContextDrawer.tsx`、`InvestigationReportPage.tsx`、`InvestigationReportCard.tsx`。已有地域编辑/草案展示复用。显示候选阶段进度、目标/策略、本地/省级未明/IP待核实标签、零入选/失败/待对账状态；不以原 candidate_posts 冒充预筛候选总数，不把 IP 补充写成克拉玛依本地事件。

**验收**：跨用户访问拒绝、发布报告多轮 scope 不串、分母和计数与账本对齐、旧无地域报告渲染不变；原正式风险指标保持；刷新/恢复仍展示冻结地域与实际查询词。

### R5：隔离正式路径离线端到端与本地能力入口

依赖 R1–R4。新增临时 DB/输出和业务导入前外部副作用 guard 的集成 runner；真实 service→确认→Run→Job→pipeline→ingestion→worker→报告存储都运行，只有网络模型/crawler 为受控端口。不能只拿旧实验 Harness 通过来代替。

`scope_intent.py`、预览/service/store 三层统一接收服务端能力判断，默认仍返回 EXECUTION_UNAVAILABLE。**拟议的本地测试能力入口**仅由明确隔离启动器启用，校验环境、独立 DB/输出根和已实现依赖；不是前端参数、全局用户设置或请求绕过开关，owner/确认/来源校验仍必需。dy+search+select 是首版唯一支持组合，off/compare 明确阻断，不静默转 select。生产和当前 P1 服务继续关闭。测试该能力在默认启动、直接 store 调用和重启后无法被伪造开启。[E9、E11]

**验收**：真实应用接口和 UI 的正向/负向回放；按冻结方案交付报告；所有 fault cases；guard 意外联网/真实调度/正式库写入均为 0；新增测试全通过，历史失败逐例对照保留。原会话 P4 返修复验及新契约审查证据在实际本地启动前齐备。

### R6：隔离真实环境与“克拉玛依大火”功能冒烟

依赖 R5，不以大型语义质量评测作为这一次有限功能冒烟的前置。沿用用户已提出的本地功能测试范围；本计划本身不执行外部调用。

1. 新建单独 runtime/data/outputs/profile，用 W 的源码和现有 Python/前端依赖，不修改当前 P1 服务或库。前后端新端口先查占用并 strictPort，监听 127.0.0.1。后端会按轮创建 Python 进程，故仅另起前端不足以隔离源码。
2. 核对模型各角色与实际 endpoint/proxy：对话和资源模型不能代替风险、地域、媒体及报告配置。只在测试启动器用已验证的本地专用代理；TLS 验证保留、全局 TUN 不改。8996 与香港节点的当前可达性重新只做最小健康检查，不用陈旧的 8994 假设。
3. 固定一个已有 MediaCrawler 的确切版本/目录，先验证 adapter 所需字段、错误类型和 receipt 形状。不为地域功能改爬虫；若现版本不兼容，明确记录环境阻塞，不临时造兼容成功。
4. 从已授权查找的账号来源核对归属/权限与可用性；若需复制到隔离库，使用既有加解密/账号接口为新库正确保存，不直接混拷密文与不匹配的 key，不覆盖源库。不得把私人账号转成 public；登录过期时沿既有登录流程处理。密钥/cookie 不写 REPORT、Git 或模型提示。
5. 先在界面检查测试账号、明确 select、模型和有效限额。建议首轮最多 2 个原样查询词、每词 3 候选、总精采上限 2（仍以 resolver 实际较小限额为准）、候选评论不超过 10、禁止自动追加关键词/额外补采。模型真实请求总上限建议 30 次（含对话、资源、风险、地域、审核、报告和重试），达到即停止并记预算未完成；这是待写入运行清单的执行上限，不强改评分参数。新环境冻结配置后再运行。
6. 浏览器新建“调查克拉玛依大火”，证明地域解析为克拉玛依市，检查并确认策略/搜索词。使用语义适合的已有规则/词库；缺少资源时走已有资源流程，计入上述总调用限额，不拿测试通用词库的零召回证明流程正确。
7. 观察实际 query 请求、原始 capture、风险/P3输出、预算/精采/复核、正式入库审核、报告链接与冻结来源。重启隔离服务后验证记录/报告可恢复；保留旧服务与历史数据。真实平台无合法命中时如实停止，不降门槛、不塞 fixture 帖子、不得反复搜索直到成功。

预算必须可执行：现有 `audit_agent/qwen_client.py:292` 与 `investigation/qwen_chat_tool_client.py:70` 的内部重试也发真实请求。隔离 runtime 的所有模型客户端/子进程共用持久化、原子传输前计数门；每次实际发送和重试先占额度，超时不退、重启不清零，耗尽即停止，不只是日志统计。通过注入传输适配实现测试范围预算，不改模型提示或评分语义；先离线证明并发最后额度、内部重试和重启均不会突破，再开放 R6 网络。某角色的传输路径无法纳入预算时，R6 尚不就绪。

**功能正向通过条件**：至少 1 条真实合法入选内容走到正式审核和可打开的报告，且全部 ID/摘要/预算/计数能对账；范围或补充标签正确；无重复交付。若只有省级/IP补充，只证明该补充分支，不能写“本地精选已实跑”。另保留 offline 的 local 正向对照；真实 local 路径列为未验证。

**零入选分支通过条件**：页面说明实际原因且无虚构报告，可证明终态正确，不能代替上述正向通过。不研究精选效果提升，不因本轮结果不好修改固定 P3 或原风险规则。

## 5. 逐例验收矩阵

| ID | 必测输入/故障 | 可判定结果 |
|---|---|---|
| T01 | fixture/自填 owner/hash 提交正式确认 | 拒绝；无地域 Run/Job/外部请求 |
| T02 | 四资源组合，确认期间变更 revision/来源 | 合法冻结；竞态整事务拒绝，无混合快照 |
| T03 | 原查询词含空格/地名，参数 0/false、search_terms_max 边界 | 原值顺序往返，合法边界不收窄 |
| T04 | Run→Job→请求→独立重启；随后改 live 资源/设置 | 已冻风险/P3输入不变；错绑定拒绝 |
| T05 | 旧无地域 v2/v3/v4 快照与报告 | JSON/hash、旧 off/select/compare、报告兼容 |
| T06 | P3 旧版与新来源契约，同原文/响应/profile | 模型语义输入、地点/档位/状态相同；来源和新摘要诚实区分 |
| T07 | 同 execution/query/content/rank 换原文/评论/认证套旧 RiskRecord | 首次落盘/完成屏障/恢复均拒绝；detail/deliver=0 |
| T08 | raw/normalized 字段不同但映射正确 | 通过；认证/评论别名完整覆盖，不盲比投影 |
| T09 | 精采同档原文/评论/身份变化 | 初筛风险仅一次，保留原结果，地域复核后交付 |
| T10 | 精采变档/不足/P3失败 | collected 成功计额，交付/审核为0，原因可见 |
| T11 | 早词省级 strong、晚词本地 weak；同档原序 | 全词屏障后本地先，同档保持原词序/原风险排序 |
| T12 | local_only/省级目标/IP weak/风险失败回退/其他地区 | 各按原契约拒绝不合法补充，不添加新阈值 |
| T13 | 同帖跨词、并发预留、账号轮换/暂停 | 各 occurrence 独立风险，实际选中词结算，无双占/重复评分 |
| T14 | query 未完成、登录失败、限流、成功空 | 不把失败当本地不足，成功空可完成屏障 |
| T15 | detail 前后/receipt/settle 断点、陈旧 detail_completed | 无回执待对账；陈旧文件不能直接交付 |
| T16 | 入库成功但 P4 回执未写、审核完成标记前崩溃 | 独立进程查证恢复，不重采、不重复 membership/审核 |
| T17 | 同输入旧风险调用与新地域分支 | prompt/规则/词组/身份排除/完整分数/rank一致 |
| T18 | 篡改 run/owner/job/attempt，筛选接口分页过滤 | 跨用户拒绝、不能读任意文件或其他任务证据 |
| T19 | 发布报告后更改 scope、合并多个 source_job | 旧版本不变，新旧范围不串，本地/省级/IP分母独立 |
| T20 | 全风险 none、全地域不符、全部待核实、待对账、执行失败 | 分别呈现原因，不都写“无风险”或成功完成 |
| T21 | 默认启动器/当前P1/无能力直接确认/非dy-search-select | 门禁保留，无法通过API参数开启 |
| T22 | 隔离前端新建克拉玛依大火并确认、小额真实执行 | 逐段绑定一致；有合法交付则报告可打开，否则如实列未完成 |
| T23 | worker lease/crawl epoch 过期后仍尝试提交 | 状态和交付写入拒绝，不能双预留或认可过期结果 |
| T24 | 同帖跨Run新精采证据、旧 shared_complete 命中 | membership/审核/重启实际读本次不可变文件，摘要与交付账本一致 |
| T25 | 多进程抢最后模型额度、客户端内部重试、超时重启 | 每个真实发送预先计数，总量不超限，耗尽后无新请求 |

所有测试结果必须给实际命令、退出码、收集/通过/失败/跳过数与源码摘要。T01–T21、T23–T25 确定性测试新增失败为 0；T22 真实结果不保证召回，失败不能用模拟替换后改记成功。

## 6. 验证范围与执行注意

现有可复用套件：`tests/test_geographic_execution_plan*.py`、`test_geographic_joint_*.py`、`test_scoped_*.py`、`test_geographic_draft_configuration.py`、`test_frozen_workspace_configuration.py`、`test_temporary_ruleset_execution.py`、`test_search_terms_cap.py`、`test_unified_draft_configuration.py`、`test_triage_engine.py`、`test_triage_matcher.py`、`test_triaged_search.py`、`test_triage_account_rotation.py`、`test_analyze_limit_ingestion.py`、`test_report_task_configuration.py`、`test_unified_report.py`、`test_published_report_runtime_snapshot.py`、`test_r31_presentation_projection.py`。

建议新增分组：正式冻结/版本迁移、P3新旧差分、正式流水线、交付恢复/授权API、报告地域来源；名称以实际实现为准，不把规划文件名当已存在测试。先负例再实现，按每个工作包跑目标测试，最后统一跑全部接入回归，不把几轮分次通过相加当最终结果。

历史 joint runner 可用既有解释器执行：

```sh
PYTHONPATH=backend:. /Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_joint_integration.run_offline --output-dir experiments/geographic_joint_integration/artifacts/runtime-preflight-<fresh-run-id>
```

输出必须是新的目录。P4 原 runner 默认覆盖旧 artifacts/pytest.log/checks.json，复跑时采用已有“输出重定向但保留原 guard”harness 写新目录，不能直接覆盖历史。P2 原回归四项失败按精确基线节点和错误逐例比较；新增失败仍判失败，进程非零退出保留。

正式路径新增 runner 先安装保护、配置临时数据根，再导入业务代码；原 OfflineGuard 可能拒绝新的合法临时表/子进程，扩展精确白名单并补“越界写入仍拒绝”测试，不能整体关掉保护。离线 runner 与真实 R6 启动器分开。

前端在 `Audit_assistant` 运行现有 `npm run typecheck`、目标 Playwright（`geographicScopeDisplay.spec.ts`、`investigationCreation.spec.tsx`、`reportCardMetrics.spec.ts` 及新增正式地域场景）、`npm run build`。需要依赖时复用验证过的本地依赖，不临时安装；package.json 无独立 lint，不虚报 lint。浏览器人工路径证据与 mock UI 测试分开。

## 7. 风险、回退与交付

| 风险 | 控制/回退 |
|---|---|
| 把测试来源假充正式 | R1显式新契约；原依赖保持；来源与授权分别验证 |
| 旧恢复 callback 绕过复核 | 地域恢复只认新账本/receipt；旧无地域恢复保留 |
| 多库部分成功/未知网络结果 | 稳定键+同库接收事务+对账；未知不释放、不重复采集 |
| 快照大、每词候选扫描增加等待 | 原始证据不可变引用和摘要；候选阶段进度；执行预算，不新增搜索词 |
| 正式路径与实验算法漂移 | 共用纯核心，新旧逐例差分，不另造selector |
| 账号过期、代理或crawler不兼容 | R6预检、隔离环境、真实错误证据；不通过TLS关闭或库active自证成功 |
| 首轮真实零召回/预算耗尽 | 正确结束单独记账，正向报告路径仍未验证，不扩大采集或降门槛 |
| 原会话验收仍缺 | 明确保留待验收，不把作者自测或此计划当批准 |

回退默认只停止新地域启动；已创建地域 Run 保存冻结范围和依据，不能恢复为普通无地域任务。停用隔离 runtime 不影响当前 P1 服务；数据库采用增量迁移，旧字段/报告不破坏，不自动删除历史记录或重置账户。

每个工作包独立可审查提交（由实施任务范围决定，本文不自动提交），最终交付 `REPORT.md`、SOURCE_MANIFEST、INTERFACE_MAP、CASE_EVIDENCE、RUN_HISTORY、未验证项/环境清单（无秘密）、回退说明。REPORT分别写离线正式路径、本地真实功能冒烟、质量/上线状态。最终只交原会话验收，不自行宣布 P4 整阶段或正式链路可上线。

本次规划验证仅包括源码定位、契约/调用关系核对、Git树与文档引用/摘要检查；**未运行新的业务测试或真实全流程**。历史联合87通过、P4 284通过、P2 682通过/4基线失败只作已有证据引用，不称本轮新鲜结果。

独立只读架构审查提出的两项补充已纳入：shared raw 复用必须绑定本次实际审核证据，真实模型总上限必须覆盖客户端内部重试和子进程。审查只针对计划可执行性，不是 P4 返修复验或实现通过结论。
