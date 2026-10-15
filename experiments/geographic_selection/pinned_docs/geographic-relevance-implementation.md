# 指定地域舆情调查：第一版实施方案

2026-10-15 P4 并行独立准入：按用户本次要求整理 [P4-offline-v1](./geographic-relevance-p4-parallel-start.md)，固定最终 P1 `031e0cd` 与指定 P3 `2120c53`。允许独立选择模块、实验执行状态和受控离线验证，与 P2 分树并行；不改原评分引擎、P2/P3代码或正式流水线，地域启动阻断保留。同地域档内沿用原词序及各词原风险排序，不新增跨词分数榜。正式 P4 前置和整阶段验收不变；下文旧“不进入P4”指当时状态，不阻止这个新授权的独立子范围。固定P3混合省市依据的聚合行为按用户最新确认原样消费，不作为P4独立验收阻塞，不在P4纠正语义；详见启动文件第3节。

2026-10-15 用户最新搜索约束：词库/临时词生成及草案选择已经确定搜索词，确认后原样执行。例如 `克拉玛依市包过` 仍搜这个完整词；P2 不追加地域前缀、不插入空格、不剥地名再拼，也不自动重写资源。地域 scope 独立用于 P3 逐帖判断。本条覆盖旧版“补城市限定”“派生地域 query”设计，相关主定义和 P2 v2 已同步修正；历史归档不再作为实施指令。

2026-10-15 用户最新衔接约束：原预筛选能力和评分引擎代码保持；P3 独立输出地域结果，P4 消费原风险结果与地域结果，执行既定跨词本地优先及分档补充。P4 允许必要的候选汇总、最终选择和精采时序接入，不重写原搜索、词库、规则、自命中排除、身份过滤及评分公式。同帖不预归最早搜索词，精采成功才按实际选中词结算名额；失败释放临时预留。本条覆盖旧“最早 query 固定预算归属”的要求，不取消 P4 地域优先。

2026-10-15 最新 P2 入口：按 [P2-offline-v2](./geographic-relevance-p2-start-v2.md) 实施，固定最终 P1 `031e0cd26379ee44c2372c9a28536c377ab3d4b9` 和指定 names-v2 P3 `2120c53379d1b79c45e3cb8232b9c9b96657b183`。本轮交付纯计划、双来源校验、冻结恢复、风险输入不变性及 P3 人工回放，不接正式确认/Run/Job/采集，地域启动阻断保留。固定输入见 [摘要清单](./geographic-relevance-p2-pinned-inputs-v2.json)。P1 最终报告为 `docs/geographic-p0p1-final-20261015.md`；P3 tag 已包含12条真实诊断、完整预期11/12，仍待整阶段验收。以下第0节之前的旧轮次追记保留为历史，不能覆盖这个版本及第0节当前状态。

2026-10-15 P2 并行准入补充：用户要求在 P0/P1 tool 修复期间，另开独立工作树先实现 P2 可独立部分，并考虑 P3 接口。已整理 [P2 启动上下文](/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.omx/plans/geographic-relevance-p2-start.md)：承接当前 P1 未提交代码快照，新增纯计划/冻结校验与独立实验读写，固定 P3 版本完成 fixture 离线兼容验证；本轮不接正式确认事务、Run/Job、前端或 P3 运行器，保留全部地域启动阻断。P1/P3 尚待原会话验收；这不是 P2 整阶段通过，也不改变正式接入前置。本轮仅整理文档与只读接口清单，尚未实施 P2。

2026-10-15 P3 最新局部返修：新增可独立关闭的繁体内容别名与人物来源语义提示（names-v2实验入口）；289项本地回归通过，真实调用0，语义改善尚未验证。原名录、旧版与留出证据保留，本批留出未重跑。P3两项返修本地验证完成、待原会话验收，不进入P4。[返修报告](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/experiments/geographic_relevance/artifacts/program-place-alias-role-prompt-20261015-01/REPORT.md)。

2026-10-15 P3 留出试验最新状态：用户已授权并完成固定10条真实留出评测，10调用/10传输/0重试，9条通过结构和来源校验、1条失败；本地1、省级未明2、IP线索2、未解决5。发现繁体地名候选覆盖与短文本状态疑点，不据此改版或重跑。旧评论AI参考一致4/10不是准确率。P3真实模型试验完成、待原会话验收；正式链路未接入，不进入P4。这10条已消费，下文“留出未动/不读不跑”均属此前轮次历史。详见[本次报告](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/experiments/geographic_relevance/artifacts/real-model-post-only-names-holdout-20261015-01/REPORT.md)。

2026-10-15 P3 当前评测：恢复简单输出后已真实重跑同16条开发集，可消费15/16（v2为13/16、原v1为16/16），不是准确率；第3/10例恢复，第9例仍规范名串用，第13例退回省级保守，第15例保持不足状态。程序省市/IP归省保留，代码和名录未调；本轮16调用16传输0重试，留出未读未跑，待原会话验收，不进入P4。[本轮报告](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/experiments/geographic_relevance/artifacts/real-model-post-only-simple-20261015-01/REPORT.md)。下文各轮历史数据不描述本次结果。


更新日期：2026-10-15。保留confirmation，固定dev set识别7/7、可落地set5/5；新增自然事件输入补测未落草案0/1（首次SSL失败1/0，en0直连4/4），累计507请求499响应；不改当前版本，待原会话验收，P0未整体通过、未进入P1。详情见 [补测报告](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/NATURAL_EVENT_PROBE.md)。


当前实施补记（2026-10-15）：P1 已在独立工作树本地实现，最终范围为最细到城市，接受 34 省级（含直辖市）及 293 地级市。报告见 `/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo/experiments/geographic_scope_draft/REPORT.md`。此决定覆盖此前“只能城市”的中间解释；吉林需在吉林省/吉林市间澄清，不支持的区县同名不干扰唯一受支持城市。未接通地域执行，未进入 P2。下段 P0 指标与“未进入 P1”描述保留为启动前历史状态。

更新日期：2026-10-15。当前状态（2026-10-15）：**按用户选择保留减少重复确认版本（geo-intent-p0-bridge-minimal-v1 / confirmation），地域识别与草案落地结果已整理、待原会话验收；P0未整体通过。** 该组18dev、50请求50响应；set识别精确率/召回率均7/7，可落地地域写入5/5，缺资源2例正确暂缓，clear落地1/1；整体实际配置13/14，严格实验记录15/18。本次新增真实请求0，累计502请求495响应不变，未进入P1。 当前交接见 [减少重复确认版本](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/CONFIRMATION_HANDOFF.md)，历史四候选结果保留于P0 handoff/REPORT。
基线：本轮开始核查时应用 HEAD a4997d3、工作区干净；文档整理结束时发现并行的 report_collection/analysis.py、expression.py、tools.py 及报告测试修改，本轮未编辑这些业务文件。实施前重新核对 HEAD、diff 与测试状态，不把提交号当全部业务已稳定或线上已部署。业务文件路径仍为 /Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo。爬虫字段为此前已核查结果，实施时重核版本，不声称是最新远端版本。
本文是唯一主实施计划。第 8 节的 P0–P5 是唯一开发阶段编号，第 1–7 节是技术设计章节，不是另一个实施顺序。此前草案专项的 A/B/C 编号停用，其内容已合并到本文件；其他文档仅提供验证清单、案例或历史来源。需求来源：.omx/interviews/geographic-relevance-20261013T091503Z.md。无新增依赖。

## 0. 阅读与执行入口

目标：用户明确要求调查某地时，系统识别并展示地域要求；确认后，搜索、相关性判断、风险精选和报告共同遵循冻结范围。先验证识别和判定，再逐段接入正式链路。

| 唯一阶段 | 要交付的能力 | 当前状态 |
|---|---|---|
| P0 | 离线验证用户地域意图识别与工具参数 | 保留confirmation，旧dev set5/5；自然事件输入补测未落草案0/1，失败及fixture局限保留，累计507/499请求响应；待验收、未整体通过 |
| P1 | 地域进入草案，支持修改、保留、澄清及确认预览 | 最终 P0/P1 与已验收资源工具已合并、回归并固定到 `031e0cd`；地域执行继续阻断；详见最终报告 |
| P2 | 冻结地域、实际搜索计划及执行输入 | 新启动文件 P2-offline-v2 已整理；本轮仅离线模块，不接正式链路；尚未实施 |
| P3 | 原帖地域判定、独立IP统计与最小记录 | 固定 tag `p3-post-only-names-v2-local-20261015` / `2120c53`；最新12条真实诊断完整预期11/12，实体集合及档位12/12；仍待原会话验收，整阶段未通过，不进入P4 |
| P4 | 接入跨搜索词的地域优先选择、精采和恢复 | 已整理P4-offline-v1并行独立计划，尚未实施；正式接入及整阶段验收仍待前置 |
| P5 | 展示追溯、报告标记、完成质量验收与启用条件 | 未开始 |

按 P0 → P1 → P2 → P3 → P4 → P5 推进，每段以第 8 节的验收交付结束。以前所说 A 对应 P0，B 对应 P1，C 被展开为 P2–P5，因此不是与旧五个开发包一一对应。当前P0隔离真实模型评测已按用户授权完成，待原会话验收。原模型调用授权限同一协调模型及FixtureDispatcher实验，不自动延伸至其他阶段、采集或部署。
2026-10-15 P1 准入补充：用户要求保留 confirmation 候选并直接进入 P1 本地开发，不再以补做 P0 独立留出集或正式卡片展示作为开发前置；P0 原判与局限保留，不标记整体通过。P1 只接草案保存和展示，地域执行继续阻断。详细边界、接口、名录及测试见 [P1 启动上下文](/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.omx/plans/geographic-relevance-p1-start.md)。本轮只是整理计划，尚未实施 P1；没有新增模型调用授权。
2026-10-14 并行实施补充：用户允许先做 P3 独立模块与离线验证，以显式 fixture 范围替代上游输入；具体边界及接口见 [P3 并行约定](./geographic-relevance-p3-parallel.md)，新窗口入口见 [P3 启动上下文](./geographic-relevance-p3-start.md)。本轮不做 P4、不改对话端或正式流水线；P3 本地交付不等于整阶段通过。上述顺序继续约束正式接入和最终验收。

地域任务只有在端到端能力及验收就绪后才允许正式启动。中间阶段允许展示真实的草案配置，但后端须通过能力/待澄清 blocker 阻止假装已支持的执行。无地域任务使用原路径。

## 1. 已确认需求与实施边界
用户本次调查对象、议题或事件名称中包含地域，即默认配置该地域，无需特意强调“某地的舆情”；用户后续明确更换或取消时以最新要求为准。由现有对话模型识别并在草案中展示，账号背景、资源名、助手建议、引用举例以及仅改标题不作为新增地域要求。2026-10-15 用户已授权按[事件地域修改方案](/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.omx/plans/geographic-relevance-p1-event-place-plan.md)实施并独立测试；模型创建工具必须显式给出地域判断，具体契约见 P1 启动上下文。确认和执行边界仍独立：第一版执行范围建议为 dy + search；其他任务保持原状。不能给不支持的平台/模式显示已启用而实际绕过。
用户明确要求“只要当地、不补充”时使用 local_only，只开放 local；其余地域草案推荐 local_first_with_supplement，展示后与任务方案一起确认。下述省级/IP补充仅适用于允许补充的城市目标；省级目标不自动扩展到全国，地域层级未解析不得猜测扩展。
- local：帖子本身实质讨论目标城市，允许现有 weak/strong；优先级最高。
- province_unknown：内容实质涉及所属省级地域、城市未明，允许现有 weak/strong；仅本地名额不足时补充。
- ip_only：内容无确定地区、已有 IP 支持所属省级地域；最后兜底，实施策略采用有效 strong 限制，不将模型失败当作高可疑。
- other_region：明确其他地区且不涉及目标城市；风险再高也不纳入。
- unresolved/conflict：证据缺失、矛盾、模型失败或引用不合法；不能伪装成确定地域。仅没有内容地域信息且有效 IP 支持、没有冲突时，才可能进入 ip_only。
none 不入选；现有身份/重复/已审等排除机制不因地域加分复活。每档内使用现有风险总分及其排序规则，不承诺 strong 永远高于 weak。
不前置媒体下载、ASR/OCR；不新建评论独立召回体系；不按地名次数判相关；不扩大搜索到全疆补量。补充是在已确认的地域搜索结果里选择，不是自动去搜索全疆。

## 2. 代码事实及对应改动

| 改动单元 | 现有入口/证据 | 具体改法 |
|---|---|---|
| 草案工具 | [backend/investigation_creation/configure.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/investigation_creation/configure.py:13) | 新增可选 relevance_scope_change，省略保留，action=set/clear；变化字段白名单同步。地域放草案顶层，防止换 lexicon_ref 重建 investigation 时丢失。set/clear 附用户原话，来源 ID 由服务端核对绑定。 |
| 契约与临时采用 | [backend/investigation_creation/contracts.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/investigation_creation/contracts.py:320)；[backend/investigation_creation/approval.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/investigation_creation/approval.py:19) | 扩展 Draft、ProposalDraftConfiguration、ConfirmationPreview、ResolvedExecutionConfiguration、ConfirmationResolution、V4 转发白名单；历史缺省字段序列化时省略。 |
| 计划冻结/确认 | [backend/investigation_creation/resources.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/investigation_creation/resources.py:725)；[backend/investigation_creation/store.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/investigation_creation/store.py:1248) | 新增纯函数 scope_search_plan.py；在确认事务中核验 scope、已确认搜索词原样执行的映射与草案一致。保持原词库选择校验，不删除 sorted(ordered)==sorted(terms) 等约束。 |
| 冻结与恢复 | [backend/investigation_creation/adapters.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/investigation_creation/adapters.py:501)；[backend/investigation_creation/frozen.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/investigation_creation/frozen.py:102)；[backend/audit_agent/job_store.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/audit_agent/job_store.py:37) | 扩展 ensure_job 白名单、nullable JSON 列、插入/更新/反序列化、执行载入。Run/Job 中同一 scope payload 必须一致；worker 恢复复用冻结值。 |
| 确认展示 | [backend/investigation_creation/frozen_preview.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/investigation_creation/frozen_preview.py:12)；[backend/investigation_creation/public_projection.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/investigation_creation/public_projection.py:203)；[Audit_assistant/src/features/investigation/TaskConfirmationCard.tsx](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/Audit_assistant/src/features/investigation/TaskConfirmationCard.tsx:20) | 预览和历史预览输出 scope 与实际 queries；更新 types、confirmationView、workspaceRecovery、配置抽屉。卡片展示三档选择和地域待核实说明，不增加全局开关。 |
| 证据与地域模型 | [backend/audit_agent/triage.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/audit_agent/triage.py:168)；[backend/audit_agent/pipeline.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/audit_agent/pipeline.py:2225) | 新增 relevance.py（契约、归一化、模型调用及验证）。从完整候选字典建证据，不复用现有只含 id/text 且截断的风险 prompt。 |
| 选择/采集编排 | [backend/audit_agent/pipeline.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/audit_agent/pipeline.py:813)；[backend/audit_agent/pipeline.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/audit_agent/pipeline.py:2111)；[backend/audit_agent/triage.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/audit_agent/triage.py:307) | 后续 P4 新增 scoped_selection.py，消费原风险结果和 P3 地域结果，接候选汇总、三档最终选择和精采时序。原评分引擎保持不变；无 scope 继续旧路径。复用账号管理和 detail 调用，不重写原预筛选。 |
| 判定持久化 | [backend/audit_agent/triage.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/audit_agent/triage.py:323)；[backend/audit_agent/job_store.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/audit_agent/job_store.py:119) | 新增 relevance_store.py，与 JobStore 使用同一数据库路径；建候选检查点表及 append-only 判定/状态事件表。candidates.json 仅兼容诊断，不是地域状态唯一来源。 |
| 只读查询/报告 | [backend/api/investigation_creation.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/api/investigation_creation.py:247)；[backend/reporting/integration_source.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/reporting/integration_source.py:221)；[backend/reporting/aggregate_source.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/reporting/aggregate_source.py:37) | 新增 run-bound 筛选记录接口；复用 principal->run 访问校验。报告按精确 source_job_id/content_key 获取纳入依据，冻结在发布版本中；补充不能计为已确认本地。 |

建议新增业务代码文件共 5 个：investigation_creation/scope_intent.py、investigation_creation/scope_search_plan.py、audit_agent/relevance.py、audit_agent/scoped_selection.py、audit_agent/relevance_store.py。离线评测入口与样本另外放 experiments/tests，不计入业务模块。不要预先建立通用地理知识平台或新的工作流框架。

### 2.1 草案自动识别地域的具体接法（P0–P1）

已核查：[conversation.py:1255](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/investigation_creation/conversation.py:1255) 读取对话历史和原始用户消息；[conversation.py:1873](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/investigation_creation/conversation.py:1873) 使用 settings.conversation_model。保留当前协调模型和 provider 配置，每轮不额外增加一个 extract_region 模型调用。

识别由 creation 业务指导承接，工具 schema 负责传参，scope_intent.py 负责字段/来源校验及地域正常化。不要修改全局 Hermes 身份或把资源、报告正文当用户调查要求。

拟加入 creation 提示的指导原文（P0 中先作为隔离候选测试）：
> 在配置本次调查时，识别用户是否明确限定内容讨论的地域。调查某地与账号来自某地、资源名称包含地名、引用例子不同。明确地域时通过 configure_investigation_draft 的 relevance_scope_change 设置，source_quote 引用当前用户或本任务较早用户要求的原话。已有草案只改标题、规则或词库时省略该字段；明确换地域时 set，明确取消时 clear。用户说“只要当地、不要补充”选择 local_only，否则提出 local_first_with_supplement 供方案确认。地名或指代无法确定就澄清，不擅自猜测；新一轮调查不因旧报告提到某地而继承范围。资源尚未齐备时不创建占位草案，待正常创建时保留此前范围。识别、草案修改都不代表已经启动或已经约束采集。

拟扩展现有工具，例子为更新操作：
```json
{
  "operation": "update",
  "draft_id": "真实草案ID",
  "expected_revision": 2,
  "relevance_scope_change": {
    "action": "set",
    "target_text": "克拉玛依",
    "source_quote": "调查克拉玛依最近的负面舆情",
    "selection_policy": "local_first_with_supplement"
  }
}
```
省略整个 change 表示保留。clear 仅携带 action 与取消要求的 source_quote；set 才要求 target_text 和 selection_policy。不使用 null 清除，不传整份配置替代局部更新，不让模型填写可信 message ID、行政代码或 hash。

来源由后端从当前 principal/session 可访问、截至当前 application turn（包括本轮）的 user 消息核对，附真实 message ID、sequence、当前操作 turn。对更早要求需核对与本次任务的关联，不能只在全部历史里匹配任意地名。引用重复且来源不明时返回歧义；禁止从 assistant、工具正文或未来消息匹配。引用存在只证明来源，不证明模型语义理解正确。

[tools.py:534](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/investigation_creation/tools.py:534) 已持有当前 application turn；[investigation/store.py:953](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/investigation/store.py:953) 可据此取真实用户消息。runtime turn 与 application turn 不混用。

地域的原始 target_text 必须保留。标准名称、层级、所属省和别名来自受控映射；当前未发现现成行政区划解析服务，P0 用明确标记的 fixture 验证接口，P1 正常化未解析时保留待解决状态，不猜省市。无须先增加面向模型的地域查询工具；正常化返回 REGION_AMBIGUOUS 及真实候选时询问用户。

2026-10-15 用户最终纠正覆盖早先范围：P1 最细到城市，接受单一省级、地级市或直辖市；区县、县级市、街道及非市地级类型待澄清，不自动扩大到所属城市或省。简称在支持省市中匹配，朝阳/克拉玛依唯一匹配时直接接受；吉林有吉林省/吉林市两个受支持候选，需澄清。未知或多目标保留原文，不做模糊纠错。使用用户授权的固定版本 cn-division 作为后端本地基础名录并以官方材料补齐和独立核验；不把全国表塞入提示词，不以 P0 少量测试映射声称全国支持。名录负责名称、层级和父省核对，不负责内容相关性或 IP 定位。P1 当前进展及测试以独立工作树 experiments/geographic_scope_draft/REPORT.md 为准。

多轮和失败规则：
- 第一轮“查克拉玛依”，后续“采用这套词库”：正常 create 时带上最初要求；尚未有资源时原始消息是恢复来源，不创建占位草案。
- 已有草案只换词库/规则：省略 scope change 保留。明确改大庆才 set，明确不限地区才 clear。
- 复制旧轮：通过已有 copy_investigation_draft 保留已确认范围并要求本轮预览；独立新轮不从旧报告任意继承地名。
- 参数/来源/临时采用校验失败时整次修改不提交；沿用既有原子事务、revision 与回执幂等。
- 地域含糊时不保存猜测值。若已有可启动草案，需持久化与该草案绑定的最小 clarification blocker（draft/session/principal、来源消息、候选/状态、解决 revision），确认事务也要检查；刷新不能绕过。无草案时只沿用消息追问。

相关代码同时覆盖 configure.py、tools.py、contracts.py、approval.py、service/store、resources/public_projection、前端 types、confirmationView、TaskConfirmationCard、workspaceRecovery 与配置详情。临时规则路径的 ProposalDraftConfiguration 是严格契约，顶层 scope 不能漏传。后台未就绪时返回真实 blocker，前端不显示“已限定采集”。

### 爬虫第一版无需为地域判断改业务逻辑
[/Users/ext.wanghongtao6/Documents/Codex/projects/MediaCrawler-media-retry-20261009/store/douyin/__init__.py:210](/Users/ext.wanghongtao6/Documents/Codex/projects/MediaCrawler-media-retry-20261009/store/douyin/__init__.py:210) 已保存 comment_id、parent_comment_id、aweme_id、user_id、sec_uid、ip_location、content；帖子保存作者 ID、author_ip_location 和 ip_location。
[backend/audit_agent/crawler_adapter.py](/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo/backend/audit_agent/crawler_adapter.py:929) 原样读取 JSONL 字典。应用端归一化即可；话题先从现有正文提取并保留原文位置。新增结构化 POI/话题不是第一版前提，不新增采集请求。候选不开子评论，因此缺失父回复上下文是正常情况，不擅自增加采样量。

## 3. 冻结契约：已确认搜索词与地域策略分别保存
建议 Draft 顶层 relevance_scope 为可选结构：
```json
{
  "schema_version": "geo-scope-v1",
  "target": {"name": "克拉玛依市", "aliases": ["克拉玛依"]},
  "province": {"name": "新疆", "aliases": []},
  "topic": null,
  "selection_policy": "local_first_with_supplement",
  "content_unit": "parent_post_event"
}
```
省级映射来自经过核实的配置/映射，不由模型自由猜测；歧义不能自动放宽到其他省。用户未限定议题时 topic=null，“负面”交给风险判断，不能自动变成某个火灾事件。
执行配置的 relevance_scope 在确认时加入 effective_policy 和 execution_queries，并参与现有配置 hash：
```json
{
  "effective_policy": {
    "version": "geo-select-v1",
    "selection_policy": "local_first_with_supplement",
    "tier_order": ["local", "province_unknown", "ip_only"],
    "ip_min_suspicion": "strong",
    "geographic_selection_strategy": "geo_select",
    "relevance_model": "沿用现有筛选模型配置的实际标识",
    "prompt_version": "geo-relation-v1",
    "candidate_count": "确认时解析为整数",
    "comment_limit": "确认时解析为整数"
  },
  "execution_queries": [
    {"source_term": "克拉玛依市包过", "query": "克拉玛依市包过", "order": 0}
  ]
}
```
以上是允许补充的城市目标示意。local_only 时冻结 tier_order=["local"]，不能与 selection_policy 矛盾。字符串占位必须在实现时替换为严格类型的实际值；不能作为可执行快照。
**关键选择：**既有 resolved_search_terms、execution.keyword 和词库主词/变体选择继续保留原有语义。实际 query 就是权威解析后的已确认词，逐字不变；不按 scope 再拼地名、空格或别名。若保留 execution_queries，它仅承载词序、来源及既有额度设置，优先复用现有搜索词，不维护第二套生成结果。前端展示已确认搜索词，映射相同不必重复列“原词/实际词”。重复的精确 query 沿用已有 resolver 处理并保留来源，不增加次数/名额，也不预先指定未来帖子的名额归属。示例的 geographic_selection_strategy 仅指 P4 最终选择，不替换原风险评分引擎配置。
正式/临时资源路径都校验 query 与当前事务内权威解析的对应已选词一致，并独立核对 scope/来源；只把额外计划写入 hash 不等于来源已验证。省级和 IP 补充不新增省级搜索词。只改地域不自动重做词库/规则，后续资源修改仍走现有明确授权的编辑/采用流程。
risk triage 现有 terms_for() 从实时词库读，见 pipeline.py:2125：新地域任务若要求恢复一致，需在有效策略快照中保存本轮用到的 TriageTerm/search_groups 与风险参数引用或内容 hash，并让地域分支读取冻结内容；不修改无地域旧任务。不能宣称所有风险输入已冻结却继续依赖会变的 live 词库。

## 4. 地域判断精简：原帖定地域，评论仅作 IP 统计

**2026-10-15 用户变更：**暂停评论同一事件判定。新版本不向地域模型发送评论文字，不用评论提供、纠正或否定原帖地点。原帖地域与评论 IP 统计分开。旧评论关联实验、代码、验收和失败证据保留为历史，不作为新版本已完成的证明。本节是当前设计；`post-only-v1`独立模块已完成本地验证及16条真实开发试验，仍有那拉提父子地点聚合与难理解短句状态缺口；没有人工金标或留出效果结论，不自动启用。评论辅助定位可后续单独补充，不作为本版完成前提。交付见[本地报告](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/experiments/geographic_relevance/artifacts/post-only-local-20261015-01/REPORT.md)。 最新逐例结果见[真实开发报告](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/experiments/geographic_relevance/artifacts/real-model-post-only-20261015-01/REPORT.md)。

### 4.1 输入与职责

模型仅接收本帖实际可用的标题、正文、缺失/截断标记，以及从这些文字召回的有限地点候选及可靠行政归属。候选只帮助识别地点，不代表该地点就是帖子所述事件发生地。模型负责判断原帖实质涉及哪些地点；程序负责来源校验、地点资料核验及与目标省市比较。

不发送评论原文、评论 ID、父回复、作者身份、IP、搜索词或旧审核结论。不从不可见视频猜地点。目标配置不能代替帖子证据。题材/风险仍归原有功能；本轮 topic=null，不加议题评分。将来明确配置议题时只判断原帖，不重新引入评论关系。

完整候选与原始评论仍保留在本地记录中；评论稳定用户身份仅用于第5节的去重/IP统计。停止输出 comment_links、relation、assertion、link_kind、author_clarification、shared_entity、parent_evidence、requires_parent、candidate_dependencies；不是把这些字段默认为正确，也不拿旧版空评论数组冒充新版。

### 4.2 一次调用、最小回答

当前AI协议仍为 `post-only-simple-v1`，仅两个必填语义字段；程序名称消费器单独版本 `post-only-simple-names-v1`，不改变AI提示或输入。程序保留新版行政归属及IP归省；旧v1的16份真实响应只作本轮离线兼容，simple本轮已真实评测同16条、15条可消费，仍有来源与粒度缺口，见最新报告。形状示例：

```json
{
  "status": "located",
  "locations": [{"field": "title", "place": "新疆"}]
}
```

- located：原帖有肯定、实质绑定的可核验地点；locations 至少一项。明确省级但城市不明仍属此项，保留省份。
- no_location：已成功分析可用原帖，确无内容地点；locations=[]。不能用于模型失败或必要内容缺失。
- insufficient_context：必要原文/视频信息不可用、地点资料未覆盖或简称歧义未解，无法可靠完成当前判断；不伪装无地点。
- conflict：原帖对同一对象给出不能同时成立的地点断言；locations 引用冲突各方。不因原帖实质比较多个城市就报冲突。

AI提示仍要求place精确选择该field实际提供的原文地点选项。程序允许受限规范名兼容：仅同字段已发送、非歧义且唯一实体的options.name可绑定实际原文候选，例如“克拉玛依市”→原文“克拉玛依”；固定按最长原文再字典序选择并审计多来源，不按目标消歧、不模糊纠错，原始响应和实际原文分别保留。旧冻结失败不覆盖；只用 title/desc 两个已有字段，不新建 C/L 编号，不让模型抄原文、长 ID、行政链或最终 local 档位。程序按字段取实际发送原文、保留地点所有对应 span 与资料来源。资料未覆盖时不能让模型凭记忆补归属。上述状态由模型明确返回，缺失/非法值为失败，不能默认 no_location。

若现有消费者需要 context_sufficient，新版本适配器可按明示状态映射：located/no_location 为 true，insufficient_context 为 false，模型或结构失败为 null；conflict 由冲突状态直接阻断。该映射属于新契约，不能用来修补旧响应或悄悄修改历史判定。

2026-10-15 程序名称修复：保存的16条真实开发响应零调用离线重消费，第9例来源兼容恢复local，其余15例不变；可消费15→16不等于准确率。见[名称修复报告](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/experiments/geographic_relevance/artifacts/program-place-name-binding-20261015-01/REPORT.md)。留出仍未读未跑，旧真实评测报告保持原样。

### 4.3 程序校验与地域派生

1. 校验版本、JSON类型、状态与引用一致性；字段必须实际发送，地点须来自该字段原文的候选。未知字段、跨来源引用、编造地点、空证据定位与错误类型拒绝；原始响应与程序提取原文分开保存。
2. 地点有可靠省/市归属且实质涉及目标市→local；仅确认目标所属省、城市未明→province_unknown；明确其他地域且不涉及目标→other_region。多地实质讨论包含目标可local，具体语义由模型选择支持地点。
3. 模糊简称、资料缺口与无法判断→unresolved；真实矛盾→conflict。不能预设只搜目标城市、取第一条候选、靠地名次数或关键词替模型判断实质涉及。
4. 仅 no_location 且程序/模型成功时，才可按第5节检查 ip_only；不足、冲突、非法引用、模型失败均不得IP兜底。IP支持不等于事件地点。

来源真实不等于理解正确；依然要测原帖标签/正文关系、举例/否定/假设、行政层级和多地比较。模型任务减少不保证自动提升准确率。

### 4.4 评论用途与明确取舍

评论原文保留供人工查看，不进入本次地域模型。程序仅统计完整已采样评论的用户/IP分布，不分析它说的是哪件事。既有风险功能对评论的处理保持原样，此变更仅针对新增地域模块。

不再存在 basis=comment_supported 的新结果。评论里即使明确说“视频在克拉玛依”，也不能给原帖本地资格；评论地点更正也不自动改判。代价是降低仅评论可定位帖的召回，并失去评论自动纠错能力。原帖本身也可能错误，新版本不能核实事实真伪；这些是范围取舍，不能把旧评论版参考答案机械当新版本正确答案。

### 4.5 示例与验证口径

| 可用内容 | 新版本预期 |
| --- | --- |
| 原帖实质写克拉玛依景区收费 | 来源与归属可靠时local |
| 原帖只写新疆发生火灾 | province_unknown，不补克拉玛依 |
| 原帖明确大庆住宿经历 | other_region，不受评论新疆IP覆盖 |
| 原帖地点不明；评论“视频在克拉玛依” | 评论不定位；仅在原帖成功无地点时才检查IP支持，否则unresolved |
| 原帖空白/必须看不可用视频；评论给出城市 | insufficient_context，不靠评论或IP补成功 |
| 原帖只给歧义“魔鬼城” | 无法消歧则unresolved，不从目标猜归属 |
| 原帖实质比较多个城市，包含克拉玛依 | 可local，不自动conflict |

先验证“任意替换/删除评论文字、保持用户与IP不变，地域请求/文本判定不变”；再测原帖语义和IP分层。旧same-event指标停用，原帖local/外地精确性、明确省级保留、无依据升市、来源失败率、待核实比例及成本分别报告。既有16条仅可作开发诊断；新任务标签需按原帖依据复核，旧评论版结果保留不改写。留出集继续不读、不跑；不宣布P3整体通过或进入P4。

### 4.6 程序归属保留与简单协议恢复

用户撤回v2新增的省份关系判断。当前simple入口不输出independent_scope，不默认或删除该字段；省与具体地点同时出现时保留保守聚合，粒度损失如实记录。复用既有scope/IP及名录，不修改旧冻结版本。以下v2试验结果是历史限制记录，不是当前模型输出要求。

P3 fixture 允许省或市目标，最细到城市；直辖市保持真实省级代码并执行城市策略。全国省／地级候选与旧有限县级／POI资料组合，歧义不自动消解。P1 形状的对象只作纯测试适配，不代表正式草案已可执行。

v2 实验在明确 province 引用添加 independent_scope 布尔值：独立省级依据为true；仅为已选同省具体地点的上级为false，false引用保留但不参与地域优先级。此语义不能由程序证明。难理解文本用insufficient_context而非no_location。真实16条中那拉提与难理解短句改善，另3条因漏字段、规范名串用及歧义额外字段失败；因此v2不替代4.2所述v1默认协议，详见本轮报告。来源仍精确校验，失败保留，不自动补标记或丢非法引用。

IP独立模块精确匹配固定名录后归省，稳定用户归省后去重；未知／歧义不算已确认外省，不进有效省份分母。跨省冲突单列。“新疆克拉玛依”等未登记拼接返回未知。P3复制数据快照，不调用P1城市优先解析器，不改P1业务。全国县区辅助条目仅用于IP（旧有限地点资料内已有县级地点仍保留），不声称全量POI或最新官方表。

## 5. IP 兜底是程序规则，不交给模型猜“大多数”
保留帖子 ip_location 与 author_ip_location 的来源，不能无说明合并。去掉“IP属地：”等展示前缀，规范省级别名；空值为 unknown，不能作为外地票。
按稳定 commenter ID 去重；同一用户多条评论一票；同一用户属地互相冲突时不算有效票。无稳定 ID 的评论保留原文，但不用于“独立人数多数”加分。
记录 sampled_comment_count、unique_commenters、valid_ip_commenters、target_province_commenters、valid_coverage、target_share。统计发生在完整已采样列表上；原帖版地域模型不接收评论文本。
作者有效省级属地支持，或评论分布达到已冻结门槛，才可获得 ip_only 支持；内容明确外地、冲突、模型不能完成地域判定时不得仅靠 IP 洗成兜底资格。
评论分布的初始离线实验参数可设 min_valid_users=10、min_valid_coverage=0.5、min_target_share=0.6（参数组合仅为可执行实验初值，不是已验证生产策略）。以样本敏感性比较后定版并冻结；如果证据不足以定版，作者 IP 兜底与评论分布贡献分别报告，不发布“大多数网友”结论。
ip_only 必须有效 strong（模型结构校验通过，非调用失败回退），属于补充待核实，不计为已确认新疆或目标城市事件。用户没有要求弱可疑 IP 兜底，本版不自行放宽。

## 6. 执行编排：两阶段采集、三档选择
旧循环每个词立即 score->pick->run_detail，见 pipeline.py:2196、2279、2305；直接在此加地域分无法保证跨词的本地优先。
职责边界：原预筛选继续按同一 query 的原搜索词、候选 rank、评论、词库、规则和参数产生风险结果，原评分引擎代码保持不变。P3 独立产生地域结果，不改写风险分数；P4 消费两份结果，只改变地域入选资格、地域优先顺序及其必要的精采编排。接入前后风险结果一致须有直接对照；保存冻结风险输入由适配层完成，不在原引擎中新增地域逻辑。无地域任务走原路径。[固定 P1 pipeline.py:2230–2233、triage.py:229–277；P2 v2 E8、E9]

以下是后续 P4 接入，不是本轮 P2 修改预筛选的授权：
1. 按冻结 query 顺序复用原搜索/候选采集，原风险引擎按原输入评分并保留原风险排序；P3 独立建地域判定，两份结果分别保存，不规定调用串行或用地域结果重算风险。候选阶段不向正式审核 content_callback 发送内容。
2. 在所有计划词完成候选阶段后，按冻结 selection_policy 选择：local_only 仅本地；允许补充则 local -> province_unknown -> ip_only。每档内保持原query序、各query原ranked顺序，不新增跨词风险总榜；保持 source_term 的每词上限、任务上限、重复/已审排除和身份规则。首次候选扫全计划会增加首条正式结果等待，UI 显示候选阶段进度而非假卡住。
3. 对选中的候选复用现有 run_detail；交付原审核回调前先完成必要的地域复核，不能透传使内容提前审核。精采成功登记 collected；失败按原类型处理，不能将“失败替换”泛化为空结果/媒体失败同词自动补挑。P4并行子范围的批次、跨档关闭、重试和变档停止点按启动文件第5.1、7节验证，不退化为随便选排名第一。
所有 source queries 已完成或成功返回零候选，才开放补充池；登录/限流/中断等未完成状态不能算“没有本地”。失败按既有类型处理并恢复，保留候选，不能靠切账号重复模型调用。
地域最终选择不能通过随机 compare/rank1 绕过已确认地域条件；geo_select 仅是 P4 最终选择策略，不改原评分引擎、评分公式或全局设置。原预筛选配置及执行模式须如实冻结，不能在 P2 暗中启用/切换预筛选；模式不具备所需风险结果时由后续接入明确支持边界，不静默替换。无地域任务维持 off/select/compare 原行为。scope 存在但平台/模式不支持或配置校验失败，明确报错。
选择依据须引用冻结 scope_hash、candidate evidence_hash、risk_input_hash 和策略版本。同内容多 query 保留全部来源及各 query 原评分上下文，不能把最早词的评分套给其他词，也不新增跨词取最高分。每词/任务额度不变；候选被哪个词实际选中，就临时预留该词及任务名额，精采成功才结算归属，失败按既有失败/替换规则处理并释放预留。不能因最早召回就把帖子锁给早词；同一帖子不双占、不重复交付。固定 P1 仅在精采返回内容后赋 selected_by_key[content_key]=keyword（pipeline.py:2298–2300），接入须保持这一结算含义，并补并发/恢复的临时预留。[P2 v2 E9]

### 恢复与状态
relevance_candidates 表：job_id/content_key 唯一，来源 query 集合、raw_ref/evidence_hash、当前判定引用、risk输出引用、选中 tier、状态、delivery receipt。
relevance_events 表：append-only event_id、job_id/content_key、scope_hash、policy_version、input_hash、判定/理由/引用、阶段和时间；多次输入变化追加，不覆盖历史证据。
逐 query 保存 pending/collecting/collected/evaluated/failed 检查点；候选保存 captured/evaluated/eligible/selected/collected/handed_off 等状态，下载成功不等于审核完成。
并发选择在 DB 事务中预留名额；进程崩溃后依赖已有 detail_completed 及 DB 状态对账，复用 callback 的幂等边界。旧 load_collected_selections 不能被用于认可一个没有合法地域决定的新地域任务条目。candidates.json 保持旧消费者兼容，但不用于代替新事务状态。
若新精采文字和候选文字明显不同或出现明确地点冲突，在正式回调前仅重跑文字地域验证；失败不能沿用过期本地判定。新增评论独立本地话题不反向提升原帖；不新增视频地域审核阶段。

### 无选中内容时的结束原因
当前 triage_found_nothing 会影响是否按无可疑正常结束（pipeline.py:2394）；新分支不能把“全部地域待核实”写成“没有风险”。应区分 scope_no_eligible、scope_inconclusive、risk_none、execution_failed，在任务结果摘要保留候选/排除/待核实数量。复用现有结束机制并增加原因投影；采集/模型基础设施失败遵循失败或可恢复路径，不伪装成筛选效果。无选中帖子时不虚构审核报告。

## 7. 记录接口、UI 与报告
建议新增 GET /api/investigation-runs/{run_id}/relevance-records?cursor=...&tier=...&status=...，调用现有 service.get_run(run_id, principal) 完成 owner 校验后，仅按该 Run 的 job_id 取数据；不接受任意文件路径，不跨账号读 raw evidence。分页、固定枚举过滤、截断安全预览。需要 workspace 参数时验证其与 run 的绑定；未发布报告也应可查询筛选依据。
最小 UI：任务配置卡显示目标/补充范围/已确认搜索词，原样执行时不重复拆“原词/实际词”；记录列表显示地域档位、风险、原文依据、纳入/排除理由。新版本local仅展示原帖直接依据；历史comment_supported仅按旧版本标记，不计入新原帖版效果。
报告来源依赖审计结果，但增加精确 run/job/content_key 对应的 selection_basis。两种补充分别标“省级相关、城市未明”“仅IP支持、地域待核实”。不能把审核总量直接作为已确认本地事件数；原有风险判定保持原义，不改评论归因规则。
每个发布版本冻结这些记录或其不可变引用，旧报告缺省字段保持原渲染。聚合报告按 source_job_id 保留来源 scope，不从报告查询 prompt 或当前 mutable 词库推断。report_collection 仍有并行工作，见本文基线说明；本功能仅增加必要 provenance 投影，不重做报告查询过滤功能，实施前重核工作区，不能覆盖其他改动。

## 8. 唯一分段计划：P0–P5

### P0：离线验证用户地域意图识别
**目的：**先证明协调模型能把本次用户要求映射为正确工具参数；不是判断候选帖子地域，也不接入产品业务写入。
**改动与交付：**在隔离实验入口加载当前 creation 提示与工具 schema 副本，只为副本增加第 2.1 节候选指导和 relevance_scope_change。原工具的其余必填参数、资源采用授权边界不变。记录模型实际工具请求，配置/生成/保存/启动调用仅捕获，不进入真实业务 service；需要的读取返回明确的本地 fixture。使用当前配置模型，不用 Scripted/FakeAgent 的预定动作证明识别能力。
**样本与判分：**每例提供历史、当前用户消息、已有草案范围/轮次、资源是否就绪、人工期望 set/clear/keep/clarify/defer/no_task 及目标/策略/来源。keep 允许正常业务更新但必须省略 scope change；clarify/defer/no_task 不得猜地域写入。

| 必测输入或上下文 | 人工期望 |
|---|---|
| 用户明确“调查克拉玛依的负面舆情”，资源就绪 | set 克拉玛依，提出允许补充的预览策略 |
| “查新疆欠薪” | set 新疆，不解释为某城市或全国 |
| “只要克拉玛依，不要其他地区” | set + local_only |
| 草案已有克拉玛依，“改查大庆” | set 大庆 |
| 草案已有地域，“不限制地区了” | clear |
| 草案已有地域，“只改标题/换词库” | keep |
| “账号作者来自克拉玛依”，要求查该账号 | 不凭作者背景 set 地域 |
| 只读资源名含地名，或全国调查举城市例子 | 不设城市限制 |
| 仅粘贴克拉玛依帖子，未表达调查意图 | no_task，不创建草案 |
| “查朝阳”，无法确定行政地区 | clarify，不任选候选 |
| 先说克拉玛依，几轮后采用已展示资源 | set 原范围，引用最初用户消息 |
| 原报告谈克拉玛依，独立新轮要求全国 | 不继承旧轮地域 |

**验收：**schema/来源/范围转换判分器的单元测试通过；真实模型对固定关键反例不误设地域、不错误清除、不产生越权业务调用；记录每例实际参数、错误及耗时。开发/留出对话按会话分组，语义门槛建议留出集 set 精确率与召回均 ≥95%，必须同时报告样本数；无足够已标注真实对话时只称试验，不宣称识别稳定。mock 测试和真实模型评测分别出结果。2026-10-14：原三项返修及声明判分分句/否定小修均已复验关闭，本地框架验收通过，248 项针对性测试及 25 项回归独立复跑通过；本次已获明确授权完成真实评测，开发两轮后冻结留出一次；新增实验读取回执修补后255项针对性及25项回归通过。dev最终15/18，留出原判13/16、逐例复核14/16，set复核精确率4/4、召回4/6；累计110请求109响应，召回未达95%建议门槛，34条仍全为合成数据。真实评测已完成、待原会话验收。交付位置、测试证据与限制见 geographic-relevance-p0-handoff.md；P0 尚未整体通过。

### P1：草案保存、修改与确认预览
**前置：**按 2026-10-15 用户准入决定，允许基于已选 confirmation 候选进入 P1 本地实现；P0 的原判、未通过状态和未验证项仍保留。正式启用仍须满足完整链路验收，P1 不开放地域任务执行。
**改动与交付：**按第 2.1 节接 configure/scope_intent/草案契约/临时采用/来源/待澄清；增加预览及配置详情字段，支持省略保留、set、clear。用户确认仍使用已有确认入口。地域执行能力未就绪时服务器返回 blocker，不允许产生地域 Run/Job。
**验收：**四类正式/临时资源组合；改资源不丢 scope，改 scope 不丢资源；整体更新失败无部分提交；跨用户/会话来源拒绝；第一轮要求在后续采用时保留；刷新/重启恢复；新旧轮隔离；有澄清 blocker 不得确认。交付代码、针对性测试和预览证据。

### P2：冻结范围与实际搜索计划
**前置：**P1 通过。
**本轮独立范围：**按 [P2-offline-v2](./geographic-relevance-p2-start-v2.md)，从最终 P1 tag 开发纯计划、双来源与权威输入校验、实验冻结恢复、风险输入回归和指定 P3 fixture 接口验证；旧“P1未提交代码导入”安排停用。下列正式确认事务、Run/Job 和前端接入不属于本轮，待联合接入验收；地域启动阻断保留，离线子范围完成不等于 P2 整阶段通过。
**改动与交付：**按第 3 节接 scope_search_plan、草案确认事务重算、严格临时规则校验、快照/hash/Job 白名单与执行输入冻结。前端显示已确认搜索词，历史配置只读冻结值；不新增地域搜索词改写。
**验收：**query 与权威已选词逐字一致、篡改拒绝；scope 与原 draft revision 一致；Run/Job 匹配；旧缺省字段/hash 不变；实时资源修改不改变新地域任务的已冻输入；local_only 不开放补充。确认分支用隔离测试验证，正式能力 blocker 在后续未完成时仍保留。

### P3：原帖地域判断、独立IP统计与最小记录
**前置：**正式接入及整阶段验收要求 P2 通过，使用既有候选数据和冻结范围离线验证。根据 [P3 并行约定](./geographic-relevance-p3-parallel.md)，可提前以明确标记的 fixture 范围实现独立模块、独立实验数据库和离线验证；不伪造真实冻结凭证、不接正式流水线，真实语义评测缺口单列。
**改动与交付：**按第4–5节实现显式版本的原帖地域输入/响应；模型只看标题/正文与地点资料，程序校验来源、核验行政链、统计IP及同步最小记录。评论文字不参与定位，不再实现评论事件关联；保留旧版代码和所有证据，新旧判定按版本隔离。不能仅将旧comment_links清空便声称新方案完成。
**验收：**证明评论文字变化不影响地域模型请求与文本判定；原帖引用/状态/行政层级/错误处理/IP/存储可靠，模型失败不转IP成功。真实模型单独验证本地/外地、省级保留、标签误导、歧义地点、否定/假设/多地比较及内容不足。旧same-event门槛退出本版本，不降低为“无需测语义”；使用按新范围复核的原帖标签，报告仅评论可定位案例的预期召回损失。先独立开发，真实评测预算另按具体轮次固定；当前留出不动，不修改正式精选或进入P4。

### P4：地域优先选择、精采与恢复
**前置：**P3 记录可靠且判定质量满足门槛。
**并行独立子范围：**按用户2026-10-15最新准入，可先实施 [P4-offline-v1](./geographic-relevance-p4-parallel-start.md)；只新增独立选择/实验状态/注入式离线编排，不改P2/P3/原pipeline及正式确认，不解除地域启动阻断。P2未交付时只用fixture投影，不声称实际接线兼容；P3已知语义限制保留。独立子范围完成不等于满足上述正式前置或P4整阶段通过。
**改动与交付：**按第 6 节接 scoped_selection 与 pipeline 的地域选择编排，复用原搜索、候选采集及原风险引擎，原评分代码不改。P4 消费风险和地域两份结果，做跨词候选汇总、按策略最终选择、现有额度内的临时预留/成功结算、精采回调及恢复；不重写预筛选或视频审核。先以已有候选回放/旁路比较最终选择差异，记录选择依据，再在隔离端到端测试中验证真实调用边界。
**验收：**接入前后同一完整输入和人工风险响应，原风险请求、命中、身份排除、分数及风险排序一致；只允许地域入选资格、地域优先顺序和精采时序变化。本地 weak 优先省级 strong；省级 weak 优先 IP strong；none 和明确外地不入选；早词补充不挤掉晚词本地；全部计划词完成才开放补充；未完成不冒充无本地。A/B 均搜到 X、A 选 Y 后 B 仍可选 X；A 精采失败不永久占有 X；并发/恢复不双占、不多计。精采失败替换/账号轮换/暂停恢复不重复；过期 detail receipt 不绕过地域判定；无地域路径的原搜索、评分、选择模式、提前停止、精采及恢复保持回归。

### P5：追溯展示、报告与最终启用验收
**前置：**P4 通过。
**改动与交付：**按第 7 节接只读鉴权接口、依据列表、候选/排除/待核实统计与报告冻结来源；本地、省级及仅 IP 补充区分。打通从用户要求、草案确认到筛选与报告的离线端到端回放，完成关联前后端回归和成本报告。
**验收：**跨用户读隔离；各轮/发布版本不串范围；报告计数可对账；无候选、全待核实和执行失败分别显示；前端类型检查/目标 Playwright/构建通过；冻结的模型与策略达到质量门槛。固定明确外地、仅评论定位和伪造证据反例不得违规晋入本地。
**启用边界：**仅支持的平台/模式、全链路具备且验证通过后才解除地域能力 blocker，允许正式地域调查；不支持情况不能静默普通执行。真实平台小批量采集及部署仍需对应授权；离线验收完成不等于平台召回覆盖已验证。交付剩余限制和回退方法：停止新地域启动，保留历史 scope/报告和只读依据，不把旧地域任务恢复为无地域任务。

## 9. 验证与风险
详见同目录 geographic-relevance-verification.md 和 geographic-relevance-cases.jsonl。程序测试证明约束及数据链路；真实模型评测证明语义质量，两者不能替代。
关键风险：原帖标签/地点绑定可能错误，评论自动定位及更正能力已移除；省级/IP候选地域未证实；候选不含视频存在覆盖上限；新候选扫全词使首结果变慢；旧临时规则/快照检验容易被改坏；恢复若漏字段会混用判断；未冻结的实时风险词库会让排名漂移。
对应缓解：原帖来源绑定+地点归属核验+冲突/不足状态，评论仅统计IP；补充单列；保留未知且不承诺视频召回；候选进度和预算；已确认词原样执行且独立校验地域；事务事件/一致性检查；仅新地域路径冻结实际风险输入。
原规划轮次的验证仅限文档和引用检查，未执行业务测试、模型调用、部署或爬虫。P0 本地实施与测试的独立证据见 geographic-relevance-p0-handoff.md；最新P0隔离真实模型评测已完成，原判/离线复核、冻结及110次请求证据见该交接和实验REPORT；P0未整体通过，未进入P1。
