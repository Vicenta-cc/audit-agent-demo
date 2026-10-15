# P2 启动上下文 v2：承接最终 P1，固定 names-v2 P3，先完成离线冻结模块

版本：P2-offline-v2。日期：2026-10-15。本文替代 P2-parallel-v1，旧版仅供历史对照。

用户最新澄清覆盖旧搜索设计：词库/临时搜索词生成及草案选择已经确定实际搜索词，例如 `克拉玛依市包过`。确认后原样搜索，P2 不再追加地域、不调整空格、不剥除地域或重新生成词。下文的 query 仅指已确认搜索词的执行映射；地域字段独立交 P3 判断帖子，不是再次改写搜索词的指令。

用户最新衔接约束：保留原预筛选能力及评分引擎代码。P3 独立输出地域判断；P4 消费原风险结果和地域结果，保留已确定的跨词本地优先及分档补充。P4 允许必要的候选汇总、最终选择与精采时序接入，不借此重写搜索、黑话库、审核规则、自命中排除、身份过滤或评分公式。取消同帖预先归最早搜索词的规则；名额按实际选中并精采成功的搜索词结算，不能因最早召回而锁死其他词的合法候选。

## 1. 目标、范围与完成含义

本轮目标：基于已冻结的 P0/P1 最终代码，实现可校验、可保存恢复的地域执行计划，并将恢复后的计划交给指定 P3 进行离线请求构造和人工响应消费。完成后交原会话验收。

本轮交付的是 **P2 独立模块及 P1→P2→P3 离线兼容**。不接正式确认事务、Run/Job 或采集运行器，不开放地域调查启动，不把测试来源冒充正式确认来源。当前 P1 的地域阻断是已验证行为，保留它。[E1、E5]

真正运行时的顺序为：

```text
用户要求或点选地域
  → P1 保存当前草案及真实来源
  → P2 用户确认时校验并冻结地域、资源、搜索计划及策略
  → P4 执行层按冻结 query 采集候选
  → 原预筛选引擎输出风险结果；P3 独立判断原帖地域并统计 IP
  → P4 消费两份结果，按地域档位、原风险排序与额度选择，再精采和交付审核
  → P5 展示依据和报告
```

阶段编号是开发拆分，不表示运行时只依次调用 P1/P2/P3 就能抓取。P3 不负责搜索、选中名额或正式交付；当前旧运行器仍是逐词评分后立即精采，不能靠增加地域字段得到跨词地域优先。[E9、E11、E12]

原预筛选与 P3 是两份独立结果；以上不规定模型调用必须串行。跨词地域优先会改变 P4 最终选择及精采的时序，不改变原预筛选对相同输入的判断。原评分引擎保持只读，必要适配放在冻结输入及 P4 编排边界。全计划候选汇总及新选择器属于后续 P4，不属于本轮 P2 实施。[E8、E9]

下面第 5–9 节是新实施窗口的允许范围，第 10 节是后续联合接入清单，不是本轮待执行事项。本轮实施不包含联网、模型调用、采集、提交、推送或部署。

## 2. 固定基线与阅读顺序

### 2.1 两份代码基线

| 用途 | 固定 tag | 精确 commit |
|---|---|---|
| P2 的完整应用开发基线 | `checkpoint/geographic-relevance-p0p1-final-20261015` | `031e0cd26379ee44c2372c9a28536c377ab3d4b9` |
| P3 离线依赖来源 | `p3-post-only-names-v2-local-20261015` | `2120c53379d1b79c45e3cb8232b9c9b96657b183` |

固定文件清单及字节 SHA256：[输入清单 v2](./geographic-relevance-p2-pinned-inputs-v2.json)。旧 input-observation 是旧时刻观察，不作为本轮导入依据。

P1 来源树：`/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo`。
P3 来源树：`/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo`。
主文档目录：`/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.omx/plans/`。

从 P1 精确 commit 建立独立 `codex/geographic-relevance-p2` 工作树。先检查同名分支/工作树是否已有工作；不重置、不覆盖。不要从 `a4997d3` 重新开发，不再拼接 P1 未提交文件，不整份 cherry-pick P3 快照。P3 tag 还包含较大的实验材料，本轮仅导出所需原样模块和名录到实验依赖快照。

开工核对 tag 解析到上述 commit，再用 `git show <commit>:<path>` 或等效方式取固定文件。来源工作树的 HEAD、未提交内容可能不等于 tag；不能用路径相同代替版本一致。检查任一不符就报告准确差异，不悄悄转用“当前最新”。

不要复制 `.env`、密钥、业务数据库、模型原始输出、媒体、虚拟环境或 `node_modules`；可使用现有解释器。导入清单区分运行依赖、测试代码和只读证据，记录目标路径与摘要。

### 2.2 阅读材料及冲突处理

1. 本文件及输入清单 v2。
2. 固定 P1 中 `docs/geographic-p0p1-final-20261015.md`，以及地域事件、直接选择、选择历史报告；以固定代码核对报告中的历史描述。
3. 固定 P3 中 `experiments/geographic_relevance/P3_CODE_TAG_20261015.md` 与 `artifacts/real-model-names-v2-diagnostic-20261015-01/REPORT.md`。
4. [主实施计划](./geographic-relevance-implementation.md)第 1、3–8 节与[验证清单](./geographic-relevance-verification.md)，其中旧状态由本文件和固定 tag 覆盖。

P3 最新 tag 已包含 12 条新编诊断文本的真实调用记录，完整预期 11/12、实体集合及档位 12/12；不能继续描述为“修复后从未真实验证”，也不能据此宣布正式质量验收通过。该批样本已使用，本轮不重跑、不改 prompt，不把人工响应测试计入语义准确率。[E10]

产品边界以用户最终决定为准：**最细到城市**，接受省级地域、地级市和直辖市；不等于只能选城市。旧版仅保留在 [P2-parallel-v1 历史](./geographic-relevance-p2-parallel-v1-history.md)。本文的“v2”是启动文档版本，不要求把尚未实施的计划 schema 升为 v2。

## 3. 代码证据索引

行号对应第 2 节固定 commit。P3 下列链接在整理时已核对与 tag 一致；开工仍按 Git 对象核对。规范要求引用此表，避免把历史报告当当前接口。

| 编号 | 固定代码事实与位置 |
|---|---|
| E1 | P1 范围、pending/clear、统一阻断：[scope_intent.py:134](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo/backend/investigation_creation/scope_intent.py:134>)、`:231`、`:246` |
| E2 | 来源有 conversation/ui_selection 两类，UI 不携带伪造消息：[scope_intent.py:76](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo/backend/investigation_creation/scope_intent.py:76>)；复制绑定与 lineage `:252` |
| E3 | UI 同事务写草案 revision 和操作记录：[scope_selection.py:104](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo/backend/investigation_creation/scope_selection.py:104>)，构造来源和保存 `:173–189` |
| E4 | 任务参数唯一权威是统一设置，不是草案自带参数：[resources.py:120](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo/backend/investigation_creation/resources.py:120>)；正式/临时资源解析 `:512`，搜索词顺序 `:729`，执行解析 `:749` |
| E5 | service 先阻断，再在资源 fence 内检查设置 revision、解析并确认：[service.py:571](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo/backend/investigation_creation/service.py:571>)、`:586–618`；store 再次阻断和事务重读：[store.py:1277](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo/backend/investigation_creation/store.py:1277>)、`:1312`、`:1390` |
| E6 | 临时采用内容与编辑器版本边界：[service.py:357](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo/backend/investigation_creation/service.py:357>)；正式/临时召回快照不是相同字段：[contracts.py:828](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo/backend/investigation_creation/contracts.py:828>) |
| E7 | 严格执行契约和转发白名单：[contracts.py:763](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo/backend/investigation_creation/contracts.py:763>)、`:920`、`:1046`、`:1076`；Run→Job：[adapters.py:501](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo/backend/investigation_creation/adapters.py:501>)；载入一致性：[frozen.py:102](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo/backend/investigation_creation/frozen.py:102>) |
| E8 | 评分分组使用列表对象身份；搜索自命中排除依赖 search_keyword：[triage.py:104](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo/backend/audit_agent/triage.py:104>)、`:229`、`:251` |
| E9 | 当前逐词读取实时词库、全局候选设置，立即 score/pick/detail：[pipeline.py:2111](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo/backend/audit_agent/pipeline.py:2111>)、`:2125–2139`、`:2207–2233`、`:2279` |
| E10 | P3 固定版本、实验结果和待验收状态：[P3_CODE_TAG_20261015.md](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/experiments/geographic_relevance/P3_CODE_TAG_20261015.md>)，以 tag 同名文件为准 |
| E11 | P3 fixture-only、行政/执行层级与状态适配：[relevance_scope.py:36](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/backend/audit_agent/relevance_scope.py:36>)、`:75`、`:121`；地域派生：[relevance_post_only_v2.py:117](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/backend/audit_agent/relevance_post_only_v2.py:117>) |
| E12 | names-v2 两个开关、原名录 IP、响应及调用：[relevance_post_only_names_v2.py:62](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/backend/audit_agent/relevance_post_only_names_v2.py:62>)、`:100`、`:115`、`:131`；档位不等于入选：[relevance_post_only_simple.py:83](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/backend/audit_agent/relevance_post_only_simple.py:83>) |
| E13 | store 是结果写入幂等；旧 evaluate_and_record 调旧协议且先调用后落库：[relevance_store.py:86](</Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/backend/audit_agent/relevance_store.py:86>)、`:107`、`:169` |

## 4. 不重新设计的产品规则

- P1 已识别并保存地域，P2 不重新理解用户原话、不增加地域抽取模型或查询 tool。省级/地级市/直辖市支持；区县、街道及不支持的地级类型不自动上卷。pending 即使保留旧 scope，也不能生成可执行地域计划。[E1]
- 保留事件表达识别、来源保护、前端选择历史、最新配置注入 AI，以及已验收的规则/黑话库生成、修改和采用流程。P2 不改提示词及工具契约。[E2、E3、P1 最终报告]
- 原预筛选继续使用既有评分引擎、风险规则及逐搜索词上下文；P3 不修改风险结果，P4 不以地域给风险分加减分。只改最终地域选择及其必要编排，无地域任务仍走原路径。[E8、E9；用户最新衔接约束]
- `topic=null`，仍按原帖实际可用文字定地域；评论文字不参与定位，IP 属地不证明事件发生地。[E11、E12]
- 城市目标允许补充时按 local→province_unknown→ip_only；local_only 仅 local。普通省级目标只允许本省 local，不自动扩大到全国。IP 补充必须有有效 strong 风险结果；模型失败不是 strong。P3 档位与 P4 入选资格分别记录。[主计划第 1、5–6 节；E12]
- 本轮离线执行 profile 明确限定 `dy + search`，作为最小兼容测试范围；不声称其他平台已支持地域执行。creator 或其他平台的地域输入明确返回“本轮计划不支持”，不以空 query 成功或退回普通调查。扩平台/模式留后续产品确认；无地域旧流程不受此新增模块影响。[E4；主计划第 1、6 节]

## 5. 本轮实施内容

### 5.1 新增纯计划模块

新增 `backend/investigation_creation/scope_search_plan.py`，必要专用类型留同文件，优先复用现有类型/校验函数，不重构共享契约。至少提供三种职责：

1. `build_scope_search_plan(...)`：从已验证的内部草案、同 revision 的资源解析结果、权威任务设置、冻结策略及名录生成确定性计划；无数据库、网络或模型副作用。
2. `validate_scope_search_plan(...)`：独立接收调用方的预期 owner/session/draft/revision 和权威输入，重新派生比较；不能只对传入计划重算自身 hash。
3. 严格序列化/解析：结构、类型、版本、内容摘要和不变量检查；恢复时另核对调用方持有的预期计划身份/摘要。计划自身 hash 不等于确认授权。

模型使用独立 `schema_version=geo-execution-plan-v1`，本轮 `validation_origin=fixture`。纯函数不自行查询来源；实验层提供已核对的 fixture 来源记录和绑定上下文。正式授权以后由正式确认适配器提供，不为本轮创建“confirmed”假凭证。[E2、E3、E5、E11]

### 5.2 两类来源与版本演进

| 来源 | 创建计划前的校验要求 |
|---|---|
| conversation | 当前 owner/session 可访问且与任务关联的真实用户消息；核对 source ID、sequence、quote、任务锚点、当前草案及复制链，不从任意旧消息匹配地名 |
| ui_selection | 真实 `source_operation_id` 对应 owner/session/原草案的选择记录，核对 region_code、catalog_version、产生选择的 revision 和配置；不要求消息 quote/turn，不补造聊天记录 |

当前草案 revision 必须等于调用方预期 revision。**来源发生时的 revision 可以早于当前 revision**：后续只改标题、规则或词库时，验证地域身份、策略及来源仍被合法保留；不能要求 selection.committed_revision 永远等于当前 revision。复制草案须验证已有 lineage，不能伪造新选择事件。[E2、E3]

状态规则：无 scope 的历史普通草案沿用原路径；cleared 且无 scope 不造地域计划；pending、resolved 缺 scope、scope/state 不一致、缺内部身份、无权访问、错绑定/版本/名录均明确失败。无来源不是普通调查的替代入口。[E1、E2]

### 5.3 计划必须冻结的内容

| 组 | 最少内容及约束 |
|---|---|
| 草案与来源 | draft ID/revision、配置摘要、scope、必要内部 state、两类来源标识及验证绑定、复制 lineage；principal/session 使用调用方预期值交叉核对 |
| 行政身份 | target level/kind/code、province code、规范身份；另存执行层级 prefecture→city，直辖市保留省级代码并执行 city，普通省份执行 province；复用 P3 已有适配，不另写归属算法 |
| 资源 | 资源类型、原始已采用内容或可验证不可变副本、内容摘要；正式资源保存其真实 ID/版本或 runtime hash，临时资源保留实际存在的来源引用/内容，不制造正式 ID/版本 |
| 搜索 | 权威解析得到的有序搜索词、词条/变体来源、上限及全部来源映射；执行 query 与现有 resolved_search_terms 对应词逐字相同；不覆盖或重新生成 execution.keyword，不预先指定未来帖子的名额归属 |
| 权威任务设置 | 统一设置 revision、保存的参数及派生有效值、派生器版本；草案自带 task_parameters 不是权威来源，revision=0 按现有默认设置语义处理 |
| 候选与评论 | 有效每词/任务上限、候选条数、collect_comments、候选评论/精采评论上限、必要的采集参数及其配置来源；严格整数/布尔值，不现场读取新全局默认补齐 |
| 风险输入 | 实际 TriageTerm 全字段、词条顺序、search_groups、审核规则快照、模型/提示及评分/身份排除参数、对应摘要；现有评分 search_keyword 继续使用同一个已确认搜索词 |
| 地域策略 | selection_policy、有效 tier_order、IP strong 限制、固定 IP 实验 profile、状态处理规则及版本 |
| P3 依赖 | 精确代码版本/必要文件摘要、schema、prompt、实际模型标识及调用配置来源；显式 `use_place_aliases=true`、`use_role_prompt=true`；内容名录、原行政名录、内容别名 overlay 分别留版本/内容/摘要 |
| 整体绑定 | 规范 JSON 的计划摘要、各输入摘要、冻结时间等非确定性元数据的独立位置；时间/随机 ID 不进入确定性计划核心 |

资源边界必须保留：临时资源已采用后编辑器升级，不自动改变草案内容；用户在草案内合法修改临时内容、去掉旧 source_edit_ref 后仍可按现有规则冻结。正式/临时规则×正式/临时词库四组合，逐类校验，不强塞统一的正式 version 字段。[E4、E6]

候选数/评论数从“权威有效任务参数 + 显式冻结的 triage 设置”派生，当前参考关系：candidate_count=max(triage_candidates_per_keyword, max_notes)，候选评论数在 collect_comments 为 true 时取 min(triage_candidate_comments, max_comments)，否则为 0。派生时处理严格类型、合法边界及既有参数转换，不自行更改现有上限。[E4、E9]

关闭评论或评论上限为 0 时，不偷偷采评论，不把缺评论当外地；评论多数支持不可用，但有效作者 IP 仍可能单独支持 ip_only。10 人/50%覆盖/60%同省只作为本轮明确标记的评论分布测试 profile，不是作者 IP 的门槛或已验收生产默认。[E12；relevance_ip.py:124–165；主计划第 5 节]

### 5.4 已确认搜索词原样执行与来源保留

从现有权威资源解析与优先级得到有序源词，不另排序、不扩别名、不扩大源词上限。[E4]

用户确认的搜索词就是平台 query。例如词库中选中 `克拉玛依市包过`，冻结后仍为 `克拉玛依市包过`；不添加 `克拉玛依市 `，不插入空格，不把其中地名剥掉再拼。`包过`、`北京路欠薪` 等合法已确认词同样原样执行，不按地名子串猜测、补全或改写。地域识别和结果判断仍走独立 scope/P3 契约。

若执行映射沿用 `execution_queries`，每项 query 必须与对应权威已选搜索词逐字相同；它只记录现有词序、来源及额度设置，不是第二套搜索词生成结果。优先复用已有 resolved_search_terms，不为新增语义维护另一份可独立修改的词表。已有词条上限、变体优先级和精确去重沿用当前 resolver；映射层保留同一 query 的全部资源来源，不做简称/行政名/空格归一化，也不因资源来源增加执行次数或名额。

区分“多个资源给出同一个 query”和“不同 query 搜到同一帖子”。后者保留全部搜索来源及各自原评分上下文，不因最早召回而预占某词名额；P2 不冻结帖子 owner。后续 P4 对实际选中的候选按现有每词/任务额度临时预留，精采成功才结算到实际选中词，失败按既有失败/替换规则处理并释放临时预留。同一帖子不能同时占多个名额，也不能因早词未选中或已满额，就被禁止供晚词合法选择；预留及结算的并发/恢复属于后续 P4。

用户只修改调查地域时，继续保留已确认规则和黑话库/搜索词，不隐式重新生成或改词。若后续需要随地域重做资源，走现有用户授权的生成/编辑/采用流程，不能由 P2 自行推断地名替换。搜索词带目标地名也不保证搜回结果属于目标地，P3 仍逐帖判断；本轮不证明真实平台召回质量。[用户最新澄清；E3、E4]

### 5.5 保留现有评分输入与恢复行为

平台 query 和现有评分 search_keyword 使用同一个已确认词。例如 `克拉玛依市包过` 搜索并传入现有评分，不能剥掉地域变成 `包过`；不新增一套地域版评分搜索词规则，也不要求新增 `risk_search_keyword` 字段。词条和分组保持原有含义，恢复后仍由现有 engine 解释。[E8、E9；用户最新澄清]

每个 query 使用自身原搜索词及原候选 rank/评论等输入评分；同一帖子在不同 query 下保留各自上下文，不能通过同帖去重偷换成最早词的分数。未来评分缓存键必须区分这些上下文及完整风险输入，不只用帖子 ID，也不改为跨词取最高分。P2 只记录来源及冻结输入，不预先选择哪个词最终得到帖子。[E8、E9]

只把 TriageTerm 转 JSON 再恢复成新列表，不能保证分组生效。离线实验应通过只读冻结数据适配器向现有 engine 提供词条和 search_groups，按既有 `terms_for()` 建立合法关联；不修改生产 triage、不写私有 cache 修补。使用确定性人工风险响应，比较恢复前后同内容、同源词的命中、自命中排除、分数与排序。后续正式运行必须提供等效的冻结输入接法。[E8]

另做地域接入前后直接对照：给原引擎完全相同的帖子、候选 rank、评论、搜索词、词库分组、规则和参数，固定人工模型响应，断言风险请求、身份排除、命中、自命中排除、分数及原风险排序一致。切换地域或 local/other_region 等地域结果不能改写这些风险结果；只允许 P4 的最终入选资格、地域优先顺序和精采时序变化。本轮验证输入适配及离线消费，真实 P4 编排对照留第 10 节。

## 6. P3 离线桥接

### 6.1 固定调用及依赖

本轮仅从指定 tag 原样导出这些传递依赖到 `experiments/geographic_execution_plan/dependencies/p3/`：`relevance.py`、`relevance_ip.py`、`relevance_scope.py`、`relevance_post_only.py`、`relevance_post_only_v2.py`、`relevance_post_only_simple.py`、`relevance_post_only_names.py`、`relevance_post_only_names_v2.py`；若测试事件保存，再导出 `relevance_store.py`。另导出原行政名录、source manifest、location_catalog。依赖追加须由真实 import 证明，登记摘要，不整包搬运旧实验或模型输出。

通过独立进程/明确包命名空间调用，避免 P1/P3 `backend` 包混用，不在产品启动路径 import P3。版本要求：[E11、E12]

```text
计划恢复 → 构造明确 fixture 模板
  → context_from_scope_state(state, template, admin_catalog=原行政快照)
  → relevance_post_only_names_v2.prepare_request(
        context, candidate, catalog=内容名录, admin_catalog=原行政快照,
        use_place_aliases=True, use_role_prompt=True)
  → relevance_post_only_names_v2.decision(request, 人工响应)
  → 可选实验 store.record(..., decision_dict)
```

不得调用会触发模型的 evaluate_relevance。尤其不能复用 `relevance_store.evaluate_and_record`：它调用旧 `.relevance.evaluate_relevance`，并期待旧对象的 `.to_dict()`，不是 names-v2 封装。[E13]

### 6.2 名录与输入边界

已核实：两边原行政名录逐字节相同，3218 条，版本 `cn-division-2026.0.1-reviewed-p1-v1`，文件 SHA256 `716c35d358e030b8d499979884fce462d4a2d6293b76fb0bfaad8f59062f01a6`。固定输入清单记录此事实；字节 SHA、P3 canonical JSON digest、records digest 是不同值，不得互换。[输入清单 v2；E11、E12]

原行政名录用于 P1 身份和 P3 IP；内容别名 overlay 仅用于原帖文字候选，不扩展用户可选范围或 IP 解析。以后版本变化需要明确兼容检查，不改 version/digest 假装通过。

candidate 离线适配必须保留 platform/content_key/post_id、原文 title/desc、缺失/截断标记、稳定评论用户身份、作者/帖子 IP 各自来源、source_queries/raw_ref。没有稳定用户 ID 不造 ID；原帖缺文字不以 query 或评论补文。[relevance.py:108–195；E12]

模型只看原帖实际文字及有限核验地点候选，不输入调查目标、query、风险结果、评论文字、作者/IP。相同原帖在不同目标或不同评论文字下，语义请求不应因目标偏好改变；派生档位可随目标改变。[E11、E12；主计划第 4 节]

### 6.3 档位、失败与入选的区别

- P3 返回地域判断，不承诺最终入选。普通城市同省异市是 other_region，不是 province_unknown；省级目标下同省城市是 local；直辖市按 city 执行层级处理。[E11]
- 仅成功 no_location 才可能由有效 IP 形成 ip_only；不足、冲突、非法响应、模型错误不能由 IP 晋级。[E12]
- local_only 下 P3 仍可能标 ip_only；未来选择器必须排除它。P3 没有风险输入，不能自行落实 strong 门槛。本轮用明确标记的测试消费器断言有效策略，不实现生产 P4 选择器。[E12]
- 原 store 仅保证相同请求结果的写入幂等，不保证模型只调用一次；保存计划恢复也不证明执行进度可恢复。[E13]

## 7. 独立保存与恢复

实验存储仅在 `experiments/geographic_execution_plan/` 的显式测试路径/临时目录，报告记录路径，不读取或写入运行服务数据库。

保存计划及所需不可变内容；大内容可存内容寻址的独立副本，但不能只留会被覆盖的绝对文件路径。保存后，修改当前草案、资源、统一设置和磁盘名录，不影响恢复出的旧计划；缺失副本、未知 schema、摘要错误明确失败，不回读“最新”补齐。

区分两种校验：**创建/确认前**与当时权威草案、资源和设置重新派生比较；**恢复后**与原先冻结的身份/内容/预期摘要比较。恢复后不拿实时资源重算并偷偷升级；计划解析成功也不证明 owner 授权。[E4–E7]

实验记录保存 fixture 标识、计划摘要、输入快照摘要、P3 请求及人工结果。请求/结果可重复回放，标识由显式测试上下文提供。执行次数、预算余额、已选帖子和审核交付回执不属于这份静态计划，留第 10 节。

## 8. 代码边界与实施顺序

### 8.1 允许修改

- 新增 `backend/investigation_creation/scope_search_plan.py` 及确实必要的同域专用类型。
- 新增 `tests/test_geographic_execution_plan*.py`。
- 新增 `experiments/geographic_execution_plan/`：原样 P3 依赖快照、导入清单、fixture、冻结风险输入适配、桥接/读写/验证入口及 REPORT。
- 必要文档和重跑说明；不增加依赖。

### 8.2 保持只读

P1 configure/conversation/tools/scope_selection/scope_intent/catalog、资源生成/编辑/采用、service/store、正式 contracts/adapters/JobStore/API/前端；P3 原代码/prompt/schema/原名录/历史证据。保留 P0 冻结代码与历史评测。

不动 MediaCrawler、crawler_adapter、pipeline、triage、风险精选、审核/报告；不加全局地域开关、force_confirm/allow_geo、测试绕过或环境变量后门；不清除 scope 来启动普通任务；不 mock 掉真正的启动 guard。若需要共享接口变化，先完成可独立部分，交付具体补丁需求，不越界实施。

不联网、不安装依赖、不调用模型、不启动采集或调度 worker、不写正式 DB、不发消息到其他会话、不 commit/push/merge/deploy。只使用已有本地依赖，测试临时数据不得进入运行服务。

### 8.3 执行顺序

1. 核验两个 tag/摘要，在 P1 基线创建/复用归属明确的独立 P2 工作树；登记输入和允许改动清单。
2. 先写严格状态、双来源、资源、设置和版本契约测试，再实现纯计划及已确认词的原样执行/来源映射，不新增帖子预算归属算法。
3. 实现实验保存/恢复、冻结风险输入适配；证明实际评分及排序保持一致。
4. 原样导入 P3 最小依赖，完成省/市/直辖市及输入/响应边界的人工回放。
5. 跑第 9 节新增测试与针对性回归，核对生产 guard 与未修改文件；整理第 10 节待接入项。
6. 交 REPORT 与证据，状态为“P2 离线交付待验收”，停止本轮范围；不自行进入正式接入、P4 或宣布 P3 整阶段通过。

## 9. 离线测试与验收

先阻断网络、模型客户端和正式任务调度；使用临时/内存测试数据库与明确 fixture 身份。人工响应及 FakeClient 只证明程序行为，不能证明真实模型识别质量。

| 测试组 | 必须可重跑的断言 |
|---|---|
| 固定版本 | tag、必要源文件和名录摘要逐项一致；错误版本/缺依赖不静默替换；产品无 P3 import |
| 状态 | resolved 成功；pending 保留旧 scope 仍拒绝；cleared/历史无 scope 不造地域计划；resolved 缺 scope、公共投影冒充内部状态失败 |
| 双来源 | 合法聊天与 UI 选择均成功；UI 无 quote/turn 仍合法；伪造 operation、错 owner/session/region/code、来源版本不符失败；不产生合成聊天消息 |
| revision/复制 | 旧选择后仅改标题/规则/词库仍成功；陈旧当前 revision、后续地域替换、非法跨草案/会话来源失败；合法复制 lineage 保留、伪造链拒绝 |
| 行政 | 克拉玛依市、深圳市、吉林省、吉林市、北京市、新疆维吾尔自治区、上海市；普通省/市与直辖市执行层级正确；吉林歧义、区县、错父省/代码不猜测 |
| 资源四组合 | 正式/临时规则×正式/临时词库；至少从固定 P1 的真实解析函数及临时资源库构造权威结果，不全靠手写已解析 JSON；错内容/权限/采用引用拒绝；编辑器升级不改已采用内容；合法抽屉编辑不误拒绝 |
| 权威参数 | 统一设置覆盖草案自带参数，记录设置 revision 和有效值；设置改变须使创建时权威重算不符；0/default 语义兼容；评论关闭/0 上限不启用采集、不造票 |
| 平台/模式 | 本轮 profile 仅 dy+search；creator 和其他平台的地域计划明确拒绝；普通无地域旧路径不被新增模块改变 |
| Query/额度 | `克拉玛依市包过`、`克拉玛依市 包过`、`包过`、`北京路欠薪` 分别保持权威解析后的原词/空格/顺序；修改 scope 不自动改资源词；重复 query 沿用 resolver 去重并保留全资源来源，不新增名额；不冻结帖子 owner、不追加目标名、不扩省搜索；篡改 query/source/order/limit 后重算自身 hash 仍无法骗过外部权威重算 |
| 风险不变 | query 与评分 search_keyword 均使用同一已确认词，不剥地域或派生另一版；原评分引擎代码不改；恢复前后及地域接入前后，用相同完整输入和人工响应，比较实际风险请求、身份排除、命中、自命中排除、分数及原排序一致；同帖不同 query/rank 上下文不可被合并偷换；不只比 JSON/hash |
| 恢复 | JSON 往返、独立 DB/进程重启后摘要及执行输入一致；改实时草案/资源/设置/名录不改旧计划；丢副本、错预期身份/摘要、未知 schema 拒绝 |
| P3 开关 | 固定发布组合 true/true 可重放；两个 bool 四种合法组合各自可冻结恢复；错类型、开关与摘要不符拒绝，不能恢复时取默认；旧 P3 封装不被桥接调用 |
| 输入隔离 | 相同原帖切换调查目标不改变语义 prompt（后续档位可变）；改变/删评论文字不改变模型输入和文本结论；改稳定用户/IP 仅影响原始证据及 IP；缺文、截断、跨帖/矛盾身份明确处理 |
| 人工响应 | located 本地/省级/同省异市/外省、多地比较；no_location+作者 IP 无评论、评论满足/不足门槛；不足/冲突/非法引用等不能 IP 晋级 |
| 策略消费 | local_only 下即使 P3 返回 ip_only 也不得入选；城市补充次序、省级不扩全国、IP 只有效 strong；无效风险/基础设施失败不当 strong；这是测试消费断言，不是 P4 实现 |
| 原路径与阻断 | 预览、service、直接 store 仍拒绝地域启动，无新增 Run/Job/调度；无地域旧确认/临时采用/冻结 workspace 语义不变；不以 mock guard 得到成功 |

禁止用“字段存在/JSON合法/失败总数相同”替代逐项行为断言。未知布尔、布尔冒充整数、NaN/Infinity、缺必需值、未知策略/schema 均需明确失败。

相关既有回归优先从固定 P1 的实际文件选取：`test_geographic_draft_configuration.py`、直接地域选择/历史/事件地域相关测试、`test_unified_draft_configuration.py`、`test_frozen_workspace_configuration.py`、`test_temporary_ruleset_execution.py`、`test_search_terms_cap.py`、`test_investigation_draft_configuration_m3.py`、任务设置相关测试；实施时先 `rg --files tests` 确认名字。不把不存在脚本当已执行，不改旧测试掩盖失败。

P0 原八份测试已冻结归档到 `experiments/geographic_scope_intent/baseline_tests/`，依照 `BASELINE_TESTING.md` 在旧基线复现；不把它们移回 P1 生产测试集合，不改历史样本/prompt/判分来适配 P2。无修改不强制重跑整套 P0 或前端。[P1 最终报告]

使用已有 Python 环境；本机参考解释器 `/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python`，先核实依赖和固定 P1 的测试运行说明。按实际依赖设置 PYTHONPATH，不临时联网安装。新增/相关测试、项目已有适用静态检查完成后交付；没有独立 lint 脚本则报告实际检查，不虚报。

## 10. 后续联合接入清单：本轮不实施

这部分防止“离线通过以后没人负责接线”。原会话验收本轮后，再以明确任务安排以下接入；保留阶段编号，不把它们称为本轮 P2 已完成。

| 接点 | 后续责任与验收 |
|---|---|
| P2 正式冻结 | resources/service/store 在既有资源 fence/确认事务中核对最新 draft revision、两类来源、资源四组合及设置 revision，重算合法计划；外部提交的计划不是权威输入；正式规则分支也要同等防篡改 [E4–E6] |
| 契约与 Run/Job | 同步 execution/resolution/V4/hash/白名单、JobStore schema/写读/恢复、Run→Job 对账；历史缺省省略、hash不变；丢地域字段不能降级普通调查 [E7] |
| P3 正式来源 | 新增能验证真实确认快照的 frozen-origin 适配；保留 fixture 入口用途；不得只将 fixture 字符串改成 frozen，也不得把手写计划标为正式确认 [E11] |
| 模型调用与存储 | 明确 names-v2 调用/保存封装；调用前基于 scope/policy/model/prompt/名录/证据的身份去重并登记预算，区分语义请求和含来源的审计摘要；风险缓存另绑定原 source 评分上下文；保存失败、并发、超时未知结果及重试预算需单独规定，不能承诺外部调用严格 exactly-once [E12、E13] |
| P4 执行和选择 | 复用原搜索及预筛选引擎，原评分代码不改；消费原风险结果与 P3 地域结果，保留跨词本地优先、全计划候选完成才开放补充及必要精采时序调整；每词/任务额度不变，同帖保留各 query 风险上下文，按实际选中词预留、精采成功结算，失败释放，不预归最早词。测试 A/B 均搜到 X、A 选 Y 后 B 仍可选 X，以及失败/并发/重启不双占、不多计；冻结风险恢复及无地域旧路径做对照，记录执行检查点 [E8、E9；主计划第 6 节] |
| 精采变化与失败 | 新文字变化/冲突时正式回调前重验；模型失败、全部地域未知、无风险、无符合内容与采集失败分别结束，不把未知写成安全无风险 [主计划第 6 节] |
| 卡片与 P5 | 显示已确认搜索词、冻结范围/补充策略、历史只读来源及筛选计数；原样执行不重复拆原词/实际词；报告将本地、省级和仅 IP 支持分开；更新状态不能丢已有地域选择历史 [E3；主计划第 7、8 节] |
| 最终启动 | 当前无条件能力阻断不是通过“有计划字段”即可移除。正式成功确认/Run/Job端到端测试在执行能力就绪后用隔离 DB、受控采集器和明确人工响应走正常入口；未就绪继续验证拒绝路径，不加 bypass。P4/P5、P3语义质量与支持平台/模式验收满足后，才由独立启用改动解除对应能力阻断；不支持情况仍拒绝 [E1、E5；主计划第 8 节] |

静态计划恢复≠执行恢复；写入幂等≠模型调用去重；人工回放≠模型质量通过；P1 最终 tag≠P3 已完成验收。后续接入应分别给证据，不能合并成一个“全链路通过”。

## 11. 风险与控制

| 风险 | 本轮控制与未验证项 |
|---|---|
| 用错旧基线/旧 P3 | 精确 commit+文件摘要、隔离命名空间、names-v2入口断言；不依赖工作树 HEAD |
| UI 选择误拒绝或伪造 | 双来源、真实操作记录及版本演进/复制测试；纯函数不自行声明权限已验证 |
| 恢复后搜法/评分变化 | 冻结权威设置、完整资源/风险输入；搜索和评分沿用同一确认词，实际分数回归；不追加前缀、不回读 live 默认 |
| 试验被误认为正式启动 | fixture 标记、允许文件清单、三层 guard 回归、无正式 Run/Job；正式接入列在第 10 节 |
| 调用预算/质量被夸大 | 本轮零真实模型调用；P3已有诊断状态如实保留；运行期去重与故障窗口后续单测 |
| 名录/平台质量不足 | 固定快照、区分行政与内容别名、未知不猜；真实平台召回、完整地域语义质量与生产 IP 门槛本轮未验证 |

## 12. 交付与新窗口任务文本

交付 `experiments/geographic_execution_plan/REPORT.md`，至少包括：

1. 工作树/分支/HEAD、两个固定输入版本及导入摘要、P2修改清单和禁区无改动证据。
2. schema/函数接口、双来源示例、任务设置/资源绑定、确认搜索词→原样query→全部来源映射、无帖子预归属规则及错误示例。
3. 存储恢复、原引擎同输入在恢复前后及地域接入前后的风险结果对照、P3固定入口/开关/名录/人工响应消费证据；输出示例保持 fixture 标识；P4 编排未实施须单列。
4. 全部实际命令、环境、逐测试组结果、失败/未运行项、网络/模型/调度拦截及零正式 DB 写入证据；不包含凭据。
5. 第 10 节剩余接点与预计共享文件冲突，区分“静态恢复”“正式确认”“执行进度恢复”和“语义质量”。

完成状态固定为：**P2 离线计划模块、冻结恢复及指定 P3 接口验证完成，待原会话验收；正式确认/Run/Job/采集未接入，地域启动阻断保留，P2/P3 整阶段未据此通过。**

可原样发给新窗口：

> 请完整读取 `/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.omx/plans/geographic-relevance-p2-start-v2.md` 及引用材料，按 P2-offline-v2 实施。先核验固定 P1/P3 tag 和输入摘要，从最终 P1 的 `031e0cd26379ee44c2372c9a28536c377ab3d4b9` 建立独立 P2 工作树。仅开发本文件允许的纯计划、双来源与权威输入校验、实验冻结恢复、风险输入恢复回归及指定 names-v2 P3 人工回放。规则和黑话库工具、前端、正式确认/Run/Job、P3原代码及采集运行器保持只读，保留地域启动阻断。不联网、不调用模型、不启动采集、不提交/推送/合并/部署。完成后交 REPORT 和可复跑证据，由原会话验收；第10节联合接入不是本轮授权范围，不自行宣布 P2/P3 整阶段通过。
