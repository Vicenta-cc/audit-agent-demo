# P4 并行启动计划：复用原风险预筛选，独立实现地域选择与离线恢复

版本：P4-offline-v1；日期：2026-10-15。

本次计划核验：已核对两个固定tag、commit/tree及43份Git文件字节摘要，P2两份文档观察摘要和本文件5个本地引用均有效；独立复核指出的批次跨档、精采后变档、P3混合省市依据三项问题已在本文补清。这里只证明计划引用和边界经过核验，未实施P4、未运行业务测试，不等于模块验收。

## 1. 用户已确认的目标与本轮停止点

保留原搜索词、词库、规则、候选采集参数、风险预筛选引擎及风险排序。P3 独立提供地域结果；P4 消费原风险结果和地域结果，按地域优先选择精采对象。无地域任务继续原路径。不新增入口、开关或地域版风险评分。

本次用户进一步确认：**同一地域档内沿用原预筛选顺序，不另造排序规则。** 固定 P1 的原流程是按原搜索词顺序处理，各词内使用原 `rank_candidates` 列表。因此本轮执行为：地域档位在外层，各档内保留原词序和各词原 ranked 顺序；不跨词重算分数、不建新的混合分数榜、不强制 strong 排在 weak 前面。[F1、F2]

用户授权提前并行开发的仅是：**独立选择模块、实验执行状态记录、受控离线编排与回归验证**。P2 可同时开发冻结模块；双方不改对方代码。本轮不接正式 pipeline、Run/Job、确认事务、数据库或真实采集，地域启动阻断完整保留。

这是 P4 的提前独立子范围，不等于 P4 整阶段通过，不宣称 P2 已完成或 P3 语义质量已验收。正式接入仍按总方案的前置要求另行验收。

最简单的未来运行关系：

```text
原搜索词 → 原候选采集
             ├→ 原风险引擎 → 原风险结果及原排序
             └→ 指定 P3   → 地域档位及依据
                         ↓
                 P4 地域选择与名额预留
                         ↓
                 原精采 → 校验 → 原审核交付
```

两个判断不强制串行，不能让旧选择器先只留下若干最终 picks，再判断它们的地域，否则可能漏掉原候选里的本地内容。本轮实验调用受控替身，不触发上图的真实采集或审核。

## 2. 固定输入、必读材料与工作树

| 用途 | 固定 tag | 精确 commit |
|---|---|---|
| 完整应用/原预筛选基线 | `checkpoint/geographic-relevance-p0p1-final-20261015` | `031e0cd26379ee44c2372c9a28536c377ab3d4b9` |
| P3 原样离线依赖 | `p3-post-only-names-v2-local-20261015` | `2120c53379d1b79c45e3cb8232b9c9b96657b183` |

必读：

1. 本文件和 [P4 固定输入摘要](/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.omx/plans/geographic-relevance-p4-pinned-inputs-v1.json)。
2. [P2 v2 启动文件](/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.omx/plans/geographic-relevance-p2-start-v2.md)，尤其第 5.3–5.5、6–7、8、10 节；[P2 固定清单](/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.omx/plans/geographic-relevance-p2-pinned-inputs-v2.json)。
3. [总方案](/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.omx/plans/geographic-relevance-implementation.md)第 1、4–8 节和[验证清单](/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.omx/plans/geographic-relevance-verification.md)。旧“地域改写搜索词”“同帖预归最早词”“评论定位原帖”均已停用。
4. 固定 P1 内 `docs/geographic-p0p1-final-20261015.md` 和本文件 F1–F5 对应代码/测试。
5. 固定 P3 内 `experiments/geographic_relevance/P3_CODE_TAG_20261015.md`，及 F6–F8 接口。现有真实诊断不是正式质量验收；本轮不读写原始样本或追加模型调用。

P1 Git 对象来源：`/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo`。
P3 Git 对象来源：`/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo`。

核对 tag→commit→文件摘要后，从固定 P1 建立自己的 `codex/geographic-relevance-p4` 工作树；已有同名树先查归属，不重置、不覆盖。不要从主文档目录的当前 checkout 开发，不从旧 `a4997d3` 开始，不混用来源工作树未提交成果。

P3 只导出真实 import 所需原样模块/名录到实验依赖目录，参考 P2 第 6.1 节，不整份合并 P3。保存导入 manifest、逐文件 SHA256 和 P4 自己的改动清单。不复制 `.env`、密钥、运行数据库、媒体、模型原始输出或依赖目录；不联网安装依赖。

## 3. 原代码事实及本轮接入依据

行号按上述固定 commit，实施时用 `git show <commit>:<path>` 核对，不依赖移动中的工作树：

| 编号 | 事实与位置 |
|---|---|
| F1 | P1 `backend/audit_agent/triage.py:251` 现有 `score`；`:277` 失败回退 weak；`:307` `rank_candidates`；`:313` `select_candidate` 排除非正分。原风险排序为总分、model_risk、原 rank、engagement，stable tie 顺序不变。 |
| F2 | P1 `backend/audit_agent/pipeline.py:2111/2225–2254/2305` 原流程逐词 score→pick→detail；原词序及每词 ranked 是已有顺序，没有既有的跨词风险总榜。 |
| F3 | 同文件 `:2131/2200/2298/2315`：max_notes 是每词成功上限；wanted 同时受任务剩余额度约束；detail 返回非空才归属成功词；原普通任务达到总额就不搜后词。 |
| F4 | 同文件 `:2289–2295/2343–2371`：认证失败同词最多换账号一次并排除失败候选；typed 媒体失败跳过该词；限流/不完整等传播；空 detail 不计成功且不即时补挑。 |
| F5 | 同文件 `:2151–2176/2381–2400`：完整 receipt 恢复、回调/已有评论一致性、仅成功精选进入正式输出，不能把候选或执行失败写成无可疑；tests/test_triaged_search.py:111/149/409/457/567/756，test_triage_account_rotation.py:265/280/288 有对应回归。 |
| F6 | P3 `backend/audit_agent/relevance_scope.py:75/121`：fixture-only，省/市适配；行政 prefecture 与执行 city 分开；直辖市保留省级代码。 |
| F7 | P3 `relevance_post_only_names_v2.py:62/115/131`：prepare_request/decision/evaluate_relevance；本轮只用前两者及人工响应；P3 结果含 evaluation_status、geo_tier、摘要和依据，不包含风险分。 |
| F8 | P3 `relevance_post_only_simple.py:47–103`：只有成功 no_location 可 IP兜底；不足/冲突/非法来源不兜底；`relevance_store.py:86/169` 是结果写入幂等，旧 evaluate_and_record 不适配 names-v2。 |
| F9 | P1 `scope_intent.py:231/246`、resources.py 预览、service.py:571、store.py:1312 的地域启动阻断；P2 v2 第 8 节也禁止更改这些接点。 |

以上是原行为证据。下面的类型和状态是本轮新增契约，不得在报告中写成现有产品已经具备。

**P3 已知行为与用户确认的消费边界：**固定 P3 `relevance_post_only_simple.py:84–87` 对多地点依据按 local→province_unknown→other_region 聚合；names-v2 继承这一行为。对目标克拉玛依，单独“乌鲁木齐”可返回 other_region，但同时选中“新疆”和“乌鲁木齐”可能返回 province_unknown。用户已明确：P4 按 P3 的有效结果执行，不负责纠正。因而该例返回 province_unknown 时，P4 按省级补充档的原策略、风险及额度条件消费，不额外阻断、不改成 other_region、不加措辞/城市共现规则。此行为作为 P3 已知限制留证，不列为 P4 独立模块或结果消费验收的阻塞，也不要求本轮修 P3。正确消费不等于已证明 P3 的语义正确；正式整体质量验收与现有地域启动阻断仍按原阶段边界执行。

## 4. 与 P2/P3 的并行接口

### 4.1 P2 提供冻结输入，P4 不另做冻结

P2 的实际代码尚未交付验收时，先用 `p4-fixture-input-v1` 测试投影，不猜测尚不存在的具体 Python 函数或字段名。P4 核心不 import P2 新模块、不修改 `scope_search_plan.py`，实验适配器日后才将已验收 P2 输出转换为这个投影。不得同时维护另一套资源解析、地域来源校验、名录或计划生成器。

测试投影至少包括：

- `validation_origin=fixture`、execution_id、plan_digest，以及绑定的 owner/session/draft/revision；这些是测试绑定，不是正式确认凭证。
- scope/policy 摘要、selection_policy、执行地域层级、固定 P3 版本和名录/提示/模型配置摘要。
- 权威已确认 query 的有序列表及原搜索上下文、每词成功上限、任务总成功上限、候选/评论参数。
- 原风险引擎所需的冻结输入引用和 triage_mode。本轮实验支持原 select 评分路径；off/compare 不可静默转换为 select，正式支持策略留联合接入。

来源绑定和静态计划有效性仍由 P2 负责；P4 校验投影类型、摘要、预期 execution/plan 身份及策略一致性。自己重算传入数据的 hash 不等于来源授权。

禁止为方便 P4 而修改 P2 输出或读取最新全局设置补值。接口缺项写入 `INTERFACE_GAPS.md`，在实验适配层隔离并列为正式接入前置；存在关键缺项时不得声称 P2→P4 实际兼容通过。后续如收到 P2 成果，登记精确 commit/文件摘要再做额外契约测试，不自动追随工作树 HEAD。

### 4.2 候选、风险、地域三个对象分开

| 对象 | 标识及必要信息 | 约束 |
|---|---|---|
| 候选 occurrence | execution_id、query_id、platform/content_key、原 rank、原帖/评论证据摘要、原 source_keyword | 同帖跨 query 是不同 occurrence；原 rank 不因地域过滤重新编号 |
| RiskRecord | occurrence_id、risk_input_digest、完整原 CandidateScore、规则/词库/参数摘要、原模型响应引用和有效性记录 | 分数、band、reason、hits、model、model_risk、engagement 只读；不把一个词的结果套给另一个词 |
| GeoRecord | 对应候选证据、scope/policy/request摘要、指定 P3输出、固定版本、原始响应和依据引用 | 验证是同一份证据和同一 scope，不能只收一个无来源的 geo_tier 字符串 |

同帖不同 query 即使原文相同，也可能有不同 rank、source_keyword、已采评论/IP，不能只按 content_key 缓存评分或地域结果。可复用的计算须逐项满足相同输入及版本；同帖实体唯一性用于防重复精采，不用于抹掉各 query 上下文。

P3 适配沿用 P2 v2 第 6 节：固定 names-v2，原样 `context_from_scope_state → prepare_request → decision(人工响应)`；保留 fixture 限制，不改 prompt/schema/语义规则，不新建网络客户端，不调用旧 evaluate_and_record。评论文字不进入地域模型，IP统计复用 P3，P4 不按词表或次数重判语义。[F6–F8]

### 4.3 查询完成状态

逐 query 保存 `pending/capturing/captured/evaluated/failed/paused`（名称可按风格调整，含义不可合并）。成功返回零候选是完成；登录失败、限流、尚未执行、存储失败、暂停都不是“没有本地”。完成状态同时引用本次候选集合及评估记录，防止旧结果拼成新 query 的完成证明。

固定的独立策略：全部计划 query 候选处理完成后才开始选择和精采，因而不会让早词补充挤掉晚词本地。新增地域路径不再使用普通流程的“早词采满总额就不搜后词”；这是实现跨词地域优先必须承认的编排变化。搜索词、各词候选上限和最终成功上限都不增加；没有达到所有 query 完成条件时，不能开放补充池。[F2、F3；总方案第 6 节]

模型/基础设施失败与合法“不足/冲突”分开记录。P3合法返回不足/冲突是不可入选的已判定状态；P3调用/协议失败是评估失败，不可冒充无地点或无本地。原风险调用失败回退 weak 的行为不改，不把它伪造成有效 strong。[F1、F8]

## 5. 独立选择模块：只决定谁能精采

新增 `backend/audit_agent/scoped_selection.py`，纯函数/小型数据模型，无文件、网络、数据库副作用。建议接口为 `select_next_batch(inputs, query_states, selection_state)`，输出 occurrence、tier、实际选中query、原风险排名引用及理由；命名可以调整，禁止复制整个原预筛选引擎。

顺序必须按下列规则直接实现和测试：

1. 先检查输入/版本/绑定和所有 query 完成条件；不完整返回明确 waiting/blocked，不返回“无可疑”。
2. 先套原身份丢弃、非正分、已审、已成功精采和当前尝试失败排除；地域不能复活原来不合格的内容。
3. 城市允许补充按 `local → province_unknown → ip_only`；local_only 只有 local；普通省级目标只有本省 local，不扩大到全国。
4. **每个地域档内按原 query 顺序处理，每个 query 内稳定过滤原 ranked 列表，再复用原选择含义。** 不改变原 rank、不重算分数、不跨词取同帖最高分、不增设第二种风险排序。
5. local/province_unknown 沿用现有正分 weak/strong；IP只允许原引擎有效模型结果为 strong、P3成功 no_location且有效IP支持、策略允许补充的候选。风险有效性取原模型结果的结构和 band 一致性，不根据 reason 文案猜测，也不修改原引擎的宽容解析规则。原引擎允许有效strong缺risk_score，不能新增必须有model_risk或自定分数阈值的门槛。
6. other_region/unresolved/conflict/invalid_input/invalid_response/model_error 不入选。纯同省其他城市且 P3 返回 other_region 的输入不得补充；本省含同省城市在省级目标下可local。只消费 P3结果，不另做地点判断；第3节混合省市案例若有效返回 province_unknown，也按该档正常消费，不添加纠错或额外排除。
7. 本地候选在当前每词/任务额度、去重和失败排除下已无可选择项，且无待对账的本地精采，才进入下一档；不能因为早词本地为空就先用省级填满总额。失败释放后重新从仍允许的最高档判断，遵守第 7 节各失败类型的原行为。

保留原 `rank_candidates/select_candidate` 的风险语义，不修改 triage.py。无地域输入不启动此模块，由将来正式调用者保留旧路径；坏地域输入报错，不降级无地域。

选择示例：每词上限1、任务上限2；A词有省级strong，B词有本地weak，C词有本地strong。先在local档按原词序选B、C，A不占名额；B、C之间不新增跨词分数比较。若任务上限仅1，保留B的原词序优先；若本地均无候选，才进入省级档。这里改变的是地域档位优先级，原词序、各词原排序和分数均未改变。

### 5.1 批次与词的关闭状态

精采仍顺序执行，不增加并行精采能力。一个选择批次只包含**同一地域档、同一query**的原顺序picks，按当时每词/总额的剩余量一次确定，不能把低档候选预装入本地批次。持久化批次、原picks及词状态，避免故障后重新选成另一批。

- 批次无空结果/媒体失败，且剩余名额仍允许时，该词可以在以后较低档参与选择，这是地域分档编排本身。
- 任一空detail：该词记 `no_refill`，只继续失败前该批已确定的其余picks；批次结束后在本次execution各档关闭，不新开低档或同档批次补挑。
- typed媒体失败：该词记 `closed_media_failure`，取消该词同批未发起的picks并释放其预留，各档均不再补挑；已经完成的不回滚、外部结果未知的不释放。
- 认证重试额度按query计数并跨档/重启保存，不因换档重置。只允许既有的一次换账号重选；排除该词认证失败pick，不重新评分。当前档无候选就按正常档序继续，不能借重试提前选低档。
- `no_refill/closed_media_failure`在恢复时不能默默清零；恢复只完成原批未完的合法步骤。明确重新执行/修复后的再采策略留正式接入，不由本轮自动清除失败。

例如A额度2，仅有local X和province Y：X空detail后不选Y；X成功后，若全计划本地已无合法选择、额度未满，则可在省级档选Y。若A同批local X、Z且X空，Z仍可继续，但不能新补另一条。未发生失败的其他query继续按原词序及档序选择。

## 6. 名额、去重及最小执行状态

新增专用 `backend/audit_agent/scoped_selection_store.py`，使用显式实验路径 SQLite，不能连接 JobStore 或修改 P3 RelevanceStore。它保存选择/精采执行状态及结果引用，不重建或覆盖 P3地域判定表。

最小持久化内容：execution与plan绑定、query完成记录、每个occurrence的输入/风险/地域记录引用、选择批次、实体级预留、精采尝试、成功结算、交付状态，以及 append-only 转移事件。表名使用 scoped_selection 前缀，避免与现有 P3表混淆。

- 实体键使用平台规范 content_key，执行中同一实体最多一个有效预留/成功记录。
- 预留必须用DB事务同时检查每词、任务剩余额度，以及该实体是否被占用；不同进程/实例并发也不能超额。
- 可用额度 = 原成功上限 − 已成功 − 有效预留。预留只临时占容量，不能提前记为成功。
- 成功精采才绑定实际选中 query 并结算一次；同帖首次召回不产生永久 owner。
- 确认未成功的尝试按第7节释放；外部结果不明的尝试先阻断对账，不能仅凭时间到期释放后重复精采。
- A/B都搜到X、A选了Y后，B仍可选X；X曾被A预留但已确认失败并释放，也不得永久锁给A。
- 事务提交失败就失败退出，不先执行detail或返回已预留/已结算成功；事件与当前状态同事务落盘。

同一execution只允许一个活动选择/精采批次。选择下一批与预留在事务内校验当前档、query游标、关闭状态及revision，竞争实例不能各自挑后词或低档提前执行；测试并发用于证明排他与额度，不新增并行精采。账号、退出/重试状态仅记录注入接口的身份与结果，不实现另一套账号调度。候选采集账号与精采账号的映射也应留证，真实跨阶段账号适配尚属第10节正式接入。

相同operation/receipt ID和相同payload重放幂等；相同ID不同payload拒绝。固定plan/policy/证据变化新增记录并失效过期选择，不覆盖历史事件。

本轮不引入通用工作流框架、分布式队列或新的调度服务。[F3、F5、F8]

## 7. 离线编排、失败和恢复

新增 `backend/audit_agent/scoped_execution.py`（如纯模块无需拆分可并入相邻新模块），仅接受显式注入的捕获/评分/地域判定/精采/交付/停止/账号处理接口，不自行创建爬虫、模型、账户管理器或运行服务。测试驱动放 `experiments/geographic_selection/`。

实验既要有手写RiskRecord单测，也要用固定P1原引擎和确定性人工模型响应产生实际CandidateScore；冻结风险输入适配仅在实验层，优先复用以后交付的P2适配，不写另一套评分或私有cache。

### 7.1 成功路径及正式回调隔离

候选捕获 → 原风险结果/P3结果分别存证 → 全计划完成 → 选择批次 → 原子预留 → 精采尝试 → 完整结果receipt → 地域证据复核 → 成功结算 → 独立交付/幂等回执。

原 `run_detail` 可以在返回前调用 content_callback。[F4] 因而不能直接照搬旧调用并把正式审核callback透传：实验精采接口须先收集完整结果，不自动交付；如果文字变更需重新取得P3结果，在交付前完成。后续正式接入需单独实现这个边界，不修改爬虫或视频审核。本轮只用受控替身证明候选/未复核detail不能提前交付。

原始“采集成功”与“允许交付审核”分别记录：完整detail收集成功可结算采集名额；地域待复核/变更的内容不交付，不伪造失败释放导致重复采。未通过复核时保留 collected_not_delivered/明确原因，不擅自额外增加采集预算补量。若未来产品要对这种情况额外补量，必须另行确定预算语义。

复核后必须仍满足原选中档位与资格。local→province_unknown/ip_only、任何档位变更、变为冲突/不足/外地或协议失败，都进入 collected_not_delivered 并记录前后依据；本轮不自动按新档交付、不释放成功名额、不追加精采。即使是较高档变化，也记录待后续处理，不临时重排已交付结果。只在同档且资格仍成立时继续交付。此保守停止点避免变档内容绕过尚未选取的本地，也保留原采集预算含义。

### 7.2 不扩大原失败语义

| 类型 | 本轮实验必须保持的处理 |
|---|---|
| detail正常且返回内容 | 保存完整receipt、成功结算一次；交付另记状态，不把候选当正式内容 |
| 空detail | 不计成功；释放已确认未成功的预留；本词本轮不即时补挑。原已确定同批其他picks可继续，其他词仍可合法选择此帖；不能新增“空结果自动选下一篇” |
| typed媒体下载失败 | 记录明确失败、释放本次未成功预留、跳过本词本轮余下选择；不自动同词补挑；其他词仍可合法选择同内容 |
| 认证/验证失败 | 仅在原账号处理接口允许时换下一账号一次；保留原已评估结果，排除本词失败pick；两次/无账号失败即传播，不改为无限轮换或重新评分 |
| 限流/采集不完整 | 按原类型传播、执行失败/待恢复；不得当零候选或开放补充 |
| DB/完整性/回调异常 | 传播并保留明确阶段；不吞成媒体失败；已采成功但交付异常的，不释放成功名额、不重新精采 |
| 超时/进程崩溃，外部结果不明 | 标记待对账并保留预留；只靠完整receipt和可信状态恢复，不使用过期计时器直接重试 |
| 停止/暂停 | 停止新捕获/预留/精采，保存已有状态；不假装全部query完成，不做无风险正常结束 |

空detail/媒体失败不占成功名额，但“释放”不等于允许同词即时补挑；这一点来自原代码，不能被总方案泛称“失败替换”覆盖。[F3–F5]

### 7.3 恢复和对账

同execution必须绑定相同plan/policy及所需不可变输入；恢复不读新词库/设置/名录补齐，不重新调用已持久化且完整绑定的风险/P3判断。缺内容或摘要不符明确失败。

测试精采前、精采完成但receipt未记、receipt已记未结算、结算后未交付、交付成功但确认回执未记等中断点。完整receipt须核对execution/plan/content/query/attempt及内容摘要，旧/跨任务/部分receipt不能直接认可。不能直接用旧load_collected_selections或旧candidates.json认定地域任务成功。

普通文件receipt与SQLite不是一个原子事务；未知窗口保留并对账，不承诺对真实外部系统 exactly-once。受控交付器用稳定幂等键模拟既有交付边界；只有取得可信交付回执才标handed_off。后续正式回调的真实幂等能力需要单独核实，不凭本轮模拟通过宣称已具备。[F5]

### 7.4 结束原因

独立输出候选/排除/待核实/成功精采/已交付/未完成query等计数和原因，避免把它们都写为triage_found_nothing：

- 正常无候选/无可入选地域内容；
- 地域合法判断仍不足或冲突；
- 原风险结果均不合格；
- 暂停/基础设施或数据失败；
- 有成功采集但未完成复核/交付。

原因可并列并保存逐条依据，不能靠错误优先级抹去失败。无内容交付不生成假审核结果/报告。本轮不改正式结束标记或UI。[F5]

## 8. 允许文件、只读禁区与实施步骤

允许新增：

- `backend/audit_agent/scoped_selection.py`：独立选择与类型。
- `backend/audit_agent/scoped_selection_store.py`：显式路径实验状态。
- `backend/audit_agent/scoped_execution.py`：注入式离线编排，可按必要性合并，最多上述三处生产候选模块；不在产品启动路径import。
- `tests/test_scoped_selection.py`、`tests/test_scoped_selection_store.py`、`tests/test_scoped_execution.py` 及必要同前缀测试。
- `experiments/geographic_selection/`：夹具、原样P3依赖、离线适配/入口、接口缺项、报告及重跑说明。

只读：P2的scope_search_plan及其tests/experiment目录；P1对话/tool/资源采用/地域选择/名录/契约/确认service/store/API/前端；原pipeline/triage/triage_matcher/crawler_adapter/JobStore；MediaCrawler；P3原代码/名录/prompt/schema/store/历史证据；正式审核及报告。

禁止新增入口/开关/allow_geo/force_confirm、环境变量绕过、mock正式启动guard、清除scope冒充普通任务。不得联网、调用真实模型、采集、下载媒体、ASR/OCR、启动worker、写正式数据库、跨会话发消息或commit/push/merge/deploy。使用已有依赖。其他阶段模型/采集授权不延伸到本轮。

步骤：

1. 核对固定输入并建独立worktree，登记只读文件摘要和允许改动。
2. 先写选择/资格/原排序不变的行为测试，再实现纯模块；不先复制旧pipeline。
3. 增加实验DB、原子预留/结算和追加事件；用两个独立连接或进程验证并发，不能只测同一对象内锁。
4. 用原评分引擎+固定P3人工响应贯通离线编排；证明两份结果独立、地域只改变最终选择。
5. 做失败分类、回调隔离和中断恢复测试；记录与旧原流程有意不同的仅为地域优先编排。
6. 跑第9节测试、禁区摘要核对，输出REPORT及接口缺项；交原会话验收，停止本轮。

## 9. 必须通过的离线验证

| 组 | 核心断言 |
|---|---|
| 原顺序 | 同档保留原query序和每query原ranked；原rank与分数不变；不增加跨词分数榜。两个同档候选即使后词更高分，也不据此替换原词序 |
| 档位 | 本地weak优先于省级strong；省级weak优先于IP strong；同档不把strong硬排weak前；明确外地/无风险/身份丢弃/已审不能复活 |
| 策略 | local_only不选省级/IP；省级目标不扩全国；P3 other_region不入选；混合省市依据返回有效province_unknown时按省级档条件消费、不在P4纠错或额外阻断；非法策略/模式不回落普通任务；off/compare不被偷偷切select |
| 完成屏障 | 早词只有省级、晚词有本地且总额1，最终选晚词本地；未搜/限流/失败/暂停不当完成；成功零候选正确完成；未完成时不开放补充 |
| 风险兼容 | 固定P1原引擎，相同完整输入与人工响应，接入前后风险prompt/命中/身份过滤/自命中排除/score/band/model_risk/原排序一致；改变地域不改风险 |
| 上下文 | 同帖不同query/rank/评论输入保留各自risk_input；不能取最早词或跨词最高分代替；过滤地域后不重编号rank |
| P3桥接 | 固定names-v2人工响应生成真实GeoRecord；核对scope/policy/证据/版本摘要；改评论文字不影响语义输入，改IP可改变合法no_location的IP资格；非法来源/协议失败不兜底 |
| IP资格 | P3 ip_only仍需策略允许且原引擎有效strong；weak/none及调用失败回退weak不可入IP档；作者IP/评论IP的阈值与统计复用P3，不另加分 |
| 双预算 | 每词max_notes可为1或多条，同时受task总额约束；预留+成功不能超额；失败不记成功；精采返回内容才结算实际词 |
| 归属 | A/B都搜X，A选Y后B仍可选X；A确认失败释放X后B可选X；同帖多词只有一个有效预留/成功，不因首次召回锁owner |
| 原失败行为 | 第5.1节跨档具体例全部通过：空detail后不新开同词低档批次、原同批其余picks可继续；typed媒体失败释放同批未发起预留并跨档关闭；认证按query最多换一次且重启不重置；限流/不完整/DB异常不吞；其他词仍可合法选失败未成功帖 |
| 并发与持久化 | 双连接/进程竞争同帖/最后一个名额只有一个成功；相同操作重放幂等，不同payload冲突；提交失败不能先发精采或交付 |
| 中断恢复 | 第7.3节各中断点，成功数/预留/词归属不重复；未知结果待对账；旧/跨scope/部分receipt拒绝；恢复不读当前资源、不重评完整缓存 |
| 交付隔离 | 候选不交付；detail返回前的回调不能直接审核；文字变化先复核，冲突/不足/变档不交付；local变省级/IP不得越过其他本地直接交付；成功预算不释放也不自动补采；同档资格仍成立才交付 |
| 结束原因 | 无候选、地域待核实、无风险、采集失败、暂停、已采未交付可区分；不把失败/未完成写无可疑；不生成假报告 |
| 禁区与阻断 | 固定P1/P3只读文件摘要不变；产品无新增模块入口；预览/service/直接store继续拒绝地域启动；无地域原triaged_search/rotation回归通过 |

优先运行固定P1已有 `tests/test_triage_engine.py`、`test_triage_matcher.py`、`test_triaged_search.py`、`test_triage_account_rotation.py`、`test_geographic_draft_configuration.py` 及新增测试。测试均用已有Python环境；先核实路径及依赖，不联网安装。根据新模块实际依赖增加必要静态检查，不虚报不存在的lint脚本；没有前端修改不要求新增前端测试。

若有失败，先在固定P1基线复现并按case/异常区分既有与新增，不仅看总失败数。人工响应证明程序契约，不计真实语义准确率；不重新评测P3已用开发/留出数据。

最低重跑要求：在自己的P4工作树，以已核实Python解释器运行 `-m pytest -q` 加上述五份原回归文件和全部新增测试；提供完整实际命令、退出码和日志路径。新增Python文件再做语法编译及 `git diff --check`；若项目没有现成lint/typecheck配置，明确记录不适用，不临时加依赖。测试入口注入网络/真实模型/爬虫进程拦截，任何意外真实调用立即失败；记录零真实调用，不仅靠实施者口头保证。实验数据库必须使用临时目录或显式实验路径，启动时拒绝指向正式库。

## 10. 后续正式接入清单：本轮不实施

| 接点 | 留待正式接入的内容 |
|---|---|
| P2 | 已验收的真实计划解析、正式确认来源和Run/Job穿透；把测试投影替换为经过核验的适配，不伪造frozen origin |
| P3 | names-v2真实调用/预算/并发去重/结果保存封装与语义质量验收，不改AI语义交给程序判断 |
| pipeline | 在原选择边界加地域编排，消费原评分和P3结果；无地域完整沿旧路径；处理全词候选屏障、真实账号轮换、停止和进度，不重写引擎 |
| 精采与回调 | 真实完整receipt、调用前后故障窗口、原有callback幂等核对、文字变化复核后交付；受控实验不证明真实网络调用严格一次 |
| 数据库/恢复 | 正式路径的schema迁移、Run/Job/execution绑定及历史兼容；不直接把实验DB搬到生产 |
| P5及启用 | 真实筛选记录查询/鉴权、统计与报告分层、成本/首结果等待说明及全链路验收；满足条件前不得解除地域启动阻断 |

预计冲突文件列入报告，而不是提前修改：pipeline.py、JobStore、创建确认contracts/resources/service/store/adapters/frozen、正式回调适配，以及P2/P3正式来源接线。P4独立离线交付不代表这些接点已完成。

## 11. 报告、风险及停止状态

交付 `experiments/geographic_selection/REPORT.md`，至少包含：

1. 实际worktree/branch/HEAD、固定基线、P3导出文件/摘要和P4修改清单；禁区未改证明。
2. 输入投影、RiskRecord/GeoRecord/occurrence、选择输出、状态转移、错误及最小完整示例。
3. 同档原顺序、地域优先和双预算的逐例选择轨迹；原风险结果不变的直接对照。
4. 独立实验数据库路径、并发/故障/恢复/交付幂等证据、实际命令及逐组测试结果；包括失败和未跑项。
5. P2尚未提供或未验收接口列入INTERFACE_GAPS.md，不能用fixture替代真实兼容结论。第3节P3混合省市依据行为另记已知限制，证明按原档消费即可，不列为P4纠错任务或验收阻塞，不将消费测试称为语义质量结论。
6. 第10节待接入项；说明全词候选增加等待/请求成本、普通旧路径不会变化；P3语义质量与真实平台召回未由本轮证明。

主要风险及控制：按content_key误合并上下文→occurrence分离；未知外部结果直接释放→待对账；DB成功但回调未知→交付状态分离；泛称失败替换→逐类型锁原语义；P2并行字段漂移→适配隔离与精确版本；扩大scope→允许文件和guard摘要检查。

完成状态固定为：**“P4 并行独立选择模块、实验执行状态及离线验证完成，待原会话验收；P2实际接线/正式pipeline/Run/Job/真实采集未接入，地域启动阻断保留，P4整阶段未通过。”**

新窗口启动文本：

> 请完整读取 `/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.omx/plans/geographic-relevance-p4-parallel-start.md` 及引用文件，按 P4-offline-v1 实施。从固定 P1 `031e0cd26379ee44c2372c9a28536c377ab3d4b9` 建立独立 P4 工作树，原样固定指定 P3 tag。只新增独立选择模块、实验执行状态与受控离线编排；同地域档内保留原词序及每词原预筛选排序，不新增跨词分数榜。原搜索词、评分引擎、pipeline、P2代码、P3判定、tool/前端/正式确认及Run/Job保持只读。使用fixture计划与独立实验库，保留地域启动阻断，不联网、不调用真实模型、不启动采集、不提交/合并/部署。完成后提供REPORT、接口缺项和可复跑证据，交原会话验收，不自行宣布P4整阶段通过。
