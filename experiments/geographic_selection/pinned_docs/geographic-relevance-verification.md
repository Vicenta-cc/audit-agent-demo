# 地域筛选验证清单（从属主计划）

2026-10-15 P4 并行验证范围：见 [P4-offline-v1 第9节](./geographic-relevance-p4-parallel-start.md#9-必须通过的离线验证)。固定P1 `031e0cd` / P3 `2120c53`，只测独立选择、实验执行状态及受控离线编排；正式pipeline/Run/Job不接，地域启动阻断保留。同档原词序/各词原排序、风险输入结果不变、全词完成屏障、双额度、批次关闭、类型化失败、并发与恢复、交付前复核均逐例验证。P3混合省市依据的现有聚合行为留证，P4按其有效档位正常消费，不纠错或额外阻断，不作为P4独立验收阻塞。此前“不进入P4”是历史停止状态，不取消本次独立子范围准入；正式P4前置与整阶段验收不变。

2026-10-15 用户最新搜索约束：草案确认的词原样搜索，P2 不再加地域。`克拉玛依市包过`、`克拉玛依市 包过` 等词保持各自原文；评分仍用同一个已确认词，不剥地名。变更 scope 不自动改规则/词库；P3 继续独立判断搜索结果地域。以本条覆盖旧“派生地域 query”验收。

2026-10-15 用户最新衔接约束：原预筛选能力与评分引擎代码保持；P3 只给地域结果，P4 消费原风险结果及地域结果，保留跨词本地优先及分档补充、允许必要的最终选择和精采时序接入。不预先将同帖归最早搜索词，按实际选中词临时预留、精采成功结算，失败释放。增加地域接入前后同输入风险结果不变的直接对照；本轮 P2 只验证离线适配，P4 编排验收留后续。

2026-10-15 最新 P2 验证范围：以 [P2-offline-v2 第9节](./geographic-relevance-p2-start-v2.md#9-离线测试与验收)为准，固定 P1 `031e0cd` / P3 `2120c53`。新增双来源（聊天/按钮选择）、revision演进/复制、统一设置权威性、临时采用内容、同一已确认搜索词评分与分组恢复、P3修复开关及档位/入选隔离测试；保留全部地域阻断。第10节列正式确认/Run/Job/P4/P5后续验收，不把本轮实验恢复称为正式贯通或执行恢复。P3指定tag已含12条真实诊断、完整预期11/12，仍待整阶段验收；以下旧轮次状态追记仅供历史对照。

2026-10-15 P3 最新局部返修：新增可独立关闭的繁体内容别名与人物来源语义提示（names-v2实验入口）；289项本地回归通过，真实调用0，语义改善尚未验证。原名录、旧版与留出证据保留，本批留出未重跑。P3两项返修本地验证完成、待原会话验收，不进入P4。[返修报告](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/experiments/geographic_relevance/artifacts/program-place-alias-role-prompt-20261015-01/REPORT.md)。

2026-10-15 P3 留出试验最新状态：用户已授权并完成固定10条真实留出评测，10调用/10传输/0重试，9条通过结构和来源校验、1条失败；本地1、省级未明2、IP线索2、未解决5。发现繁体地名候选覆盖与短文本状态疑点，不据此改版或重跑。旧评论AI参考一致4/10不是准确率。P3真实模型试验完成、待原会话验收；正式链路未接入，不进入P4。这10条已消费，下文“留出未动/不读不跑”均属此前轮次历史。详见[本次报告](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/experiments/geographic_relevance/artifacts/real-model-post-only-names-holdout-20261015-01/REPORT.md)。

2026-10-15 P3 当前评测：恢复简单输出后已真实重跑同16条开发集，可消费15/16（v2为13/16、原v1为16/16），不是准确率；第3/10例恢复，第9例仍规范名串用，第13例退回省级保守，第15例保持不足状态。程序省市/IP归省保留，代码和名录未调；本轮16调用16传输0重试，留出未读未跑，待原会话验收，不进入P4。[本轮报告](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/experiments/geographic_relevance/artifacts/real-model-post-only-simple-20261015-01/REPORT.md)。下文各轮历史数据不描述本次结果。



历史中间解释（已被本轮P3“省或市、最细到城市”口径覆盖，不用于限制P3目标）：当时P1范围更新为单一地级市或直辖市。P1 核验 297 个支持城市及别名；普通省级、区县、县级市、街道/非市地级类型进入待澄清。朝阳/吉林/克拉玛依简称在城市集合中直接确定。后文省级帖子证据仍用于 P3 的内容归属验证，不代表开放省级调查目标。
状态：2026-10-15原帖版真实开发集试验完成、待原会话验收。16条真实调用均通过结构/来源校验，仍有那拉提父子地点聚合及难理解短句状态两项缺口，不能称准确率100%；726项测试及11子测试通过，16条真实响应离线回放一致且存储幂等。留出未读未跑，旧证据保留，不进入P4。详见[新报告](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p3-boundary-v2/xhs-audit-agent-demo/experiments/geographic_relevance/artifacts/real-model-post-only-20261015-01/REPORT.md)。原12条人工响应及旧32条精确重放属于前次本地验证；人工响应不能证明真实语义。所有语义验收阈值仍不是既有结果。


唯一开发顺序见 [主计划第 8 节](/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.omx/plans/geographic-relevance-implementation.md)。本文件 V0–V5 是验证类别，不是另一套开发阶段：P0 对应 V0；P1–P2 使用 V1 中配置及冻结回归；P3 使用 V1–V3 的原帖地域/IP验证；P4 使用选择/恢复回归；P5 汇总 V4–V5。

## V0. 用户地域意图识别（主计划 P0）
- 候选 creation 指导、工具 schema 与人工期望样例在主计划第 2.1 节、第 8 节 P0。
- 新增隔离评测入口，模型使用当前协调模型配置；只读请求来自明确 fixture，所有写入/生成/保存/启动请求仅捕获，不能进入真实业务 service。schema 使用副本，不将实验候选注册进正式产品。
- 记录真实模型实际工具参数及必要提问，不用 Scripted/FakeAgent 的预定动作证明语义能力。按 set/clear/keep/clarify/defer/no_task 与地域、策略、原话引用逐项判分。
- 固定关键反例不得误设置/清除；按会话切分开发与留出数据。建议留出集 set 精确率和召回均至少95%，报告分子/分母、误清除、漏识别、指代失败、耗时及样本不足，不把 JSON 合法率当识别效果。
- 判分器单测可使用合成样例，真实语义结果需要已标注对话和真实模型输出；数据不足仅标记试验。模型评测尚未执行，具体供应商调用仍在当前授权范围之外。

## V1. 程序约束测试（不调用真实模型）
P3 提前并行的具体范围见 [接口与验收约定](./geographic-relevance-p3-parallel.md)：本轮只做 relevance/relevance_store 的独立测试及相关原有回归，使用 fixture 范围与独立数据库。下面的选择名额、query 检查点、正式流水线、API/前端及冻结集成测试仍属后续阶段，不要求本轮实施。V2 的语义门槛保留；无真实模型评测时不得用程序测试替代。

新增/修订tests/test_relevance.py：按新post-only协议验证实际发送原帖字段/地点来源、类型枚举、状态一致性、省级不能升市、行政归属歧义/缺口、模型错误不默认相关，以及评论文字替换不影响地域请求/文本判定。IP仍按完整稳定用户统计，用户/IP变化可改变ip_only支持，不能影响原帖文本定位。旧quote/关系/身份测试作为旧版本兼容回归保留，不是新版本启用条件。Fake响应只证明契约，不证明原帖语义正确；不引入否定正则/地名次数等语义门槛。
新增 tests/test_relevance_store.py：候选去重、事件追加、输入hash变化、query检查点、事务占位、恢复幂等、DB写入失败不可先排除后丢证据。
新增 tests/test_scoped_selection.py（后续 P4）：消费既有风险结果及 P3 地域结果，不重新评分；local weak 高于 province strong；province weak 高于 ip strong；none全部拒绝；其他城市strong拒绝；ip weak拒绝；逐词额度+任务额度；早词补充不得挤掉晚词本地；所有 query 候选完成后才补充。同帖跨词保留各词原风险上下文，A/B 均搜到 X、A 选 Y 后 B 仍能选 X；精采失败释放临时预留，成功才按实际选中词结算；并发/重启不双占、不多计，不能预归最早召回词。
扩展 tests/test_triaged_search.py（后续 P4）：复用现有 FakeCrawler/FakeEngine/回调，原预筛选引擎保持；scope 才接地域选择编排；无 scope 的 off/select/compare、达总限提前停止及原精采恢复行为保持。地域最终选择不通过 compare 绕过地域条件，不能暗改预筛选模式；评论关闭不自动打开；候选 collect_media=False；只选中合法条目进入 detail/callback；账号轮换/暂停/失败恢复不重复判断；精采内容变化重新验证文本；detail receipt 不能绕过地域验证。
原 tests/test_triage_engine.py 和评分引擎保持，新增接入对照测试优先放地域适配/编排测试，不为新增地域功能重写评分测试掩盖变化。相同帖子、rank、评论、搜索词、词库及分组、规则、参数与固定人工模型响应，在恢复前后及地域接入前后，应得到相同风险请求、身份排除、命中、自命中排除、分数及原风险排序；改变目标或地域结果仅影响最终入选与地域顺序。保留高/弱入选、none=0、模型失败回退 weak 不当 strong；初筛 score 不当最终风险等级。实际 P4 编排对照不能用本轮离线适配测试代替。
扩展 tests/test_unified_draft_configuration.py、test_frozen_workspace_configuration.py、test_temporary_ruleset_execution.py、test_search_terms_cap.py、test_investigation_draft_configuration_m3.py：set/clear/省略保留、四类正式/临时资源组合、选择词和变体不变、草案版本幂等、query与权威已选词逐字一致且篡改拒绝、旧缺省hash不变、Run/Job一致、切平台/模式报支持错误、资源后改不影响旧轮。
新增 tests/test_relevance_api.py：跨用户run不可见、run/job绑定、分页/过滤枚举、无任意路径、未发布也能查本轮记录；聚合源不能串scope。
前端 Playwright：确认卡实际query与scope、刷新恢复、历史视图不读取今天的资源、原帖依据与版本展示、补充标记、零候选/失败/进行中状态。代码变化后运行现有 typecheck/build。

## V2. 原帖语义评测（真实模型，已有数据）
1. 只按原帖实际可用标题/正文标注地域、支持字段与地点、资料归属、是否需要不可用内容。评论仅存档/IP统计，不供地域模型或新标签的定位依据。旧评论版AI共识参考不能直接当原帖金标，旧结果不改写。
2. 人工独立标注并仲裁为目标；尚无人工金标时只报告AI辅助诊断，不称准确率。明确记录仅评论可定位案例的预期召回损失、评论纠正失效与原帖事实可能错误。
3. 开发/留出按原有帖子/事件分组隔离；现有16条只用于开发，10条留出继续不读不跑。未来正式评测仍需足量独立标注帖子覆盖省级/本地/外地/标签/歧义/缺文，不因为删评论功能就宣称样本充足。目标至少100条独立留出帖，当前不足仅作试验。
4. 固定prompt/schema/地点资料/provider/policy与请求摘要；新旧任务定义不同，分别报告交付状态和逐例原因，不只比较留下数量或与旧评论参考的一致数。
5. 指标：有依据local的精确性、明确本地召回、明确外地误纳、省级保留、无依据升市、引用失败、上下文不足/冲突、模型失败、tokens/时延。完整分母保留失败；IP支持准确性与内容地域分开，不把IP当事件真值。
6. 建议正式门槛：确定性约束100%；固定外地/省级升市/伪造来源/评论反向定位反例不得违规晋级。独立人工留出local精确率至少95%、原帖明确本地召回至少90%；这是新范围的建议，不是既有效果。旧comment-supported/same-event门槛退出本版本，原失败历史继续保留。
7. IP作者支持/评论分布分别评估，既有fixture阈值不因删评论语义而放宽；不在留出挑参数，不能靠IP填补模型失败。

## V3. 成对压力样例
- 原帖明确新疆 vs 明确克拉玛依，检验省/市区分。
- 实质地点绑定 vs 只作标签/举例/假设，不能按字面命中。
- 完整原帖带后文更正 vs 关键内容不可用，不能截去否定后定位。
- 明确世界魔鬼城 vs 歧义魔鬼城，不能目标城市消歧或选第一项。
- 原帖实质比较多地 vs 同一对象真实冲突。
- 相同原帖+不同评论文字（含另次经历、作者自称、强烈地点更正），用户/IP不变：地域请求和文本判定必须相同。
- 相同原帖+不同用户/IP：内容地域不变，仅满足条件的IP支持可变化；不能把外地或不足改为ip_only。
- 空原帖/不可见视频+评论明确地点：仍不足，不借评论补证。

这些是新版本测试设计；原案例JSONL保留为历史，含评论预期的用例不能原样作为新版金标。程序回归与真实语义评测分开报告。

## V4. 集成验证顺序
先 targeted pytest，再覆盖上述关联回归；前端 typecheck、Playwright目标用例、build。示例命令（实施时使用项目既有Python环境）：
```sh
python -m pytest tests/test_relevance.py tests/test_relevance_store.py tests/test_scoped_selection.py tests/test_relevance_api.py
python -m pytest tests/test_triage_engine.py tests/test_triaged_search.py tests/test_triage_account_rotation.py tests/test_unified_draft_configuration.py tests/test_frozen_workspace_configuration.py tests/test_temporary_ruleset_execution.py tests/test_search_terms_cap.py
```
在 Audit_assistant 目录执行 npm run typecheck、npm test -- tests/investigationCreation.spec.tsx、npm run build；按实际新增前端用例加入目标文件。当前 package.json 没有独立 lint 脚本，不虚报 lint 通过。
真实平台采集单独授权后做：验证候选字段可用率、已确认搜索词原样发出、重试/分页/平台返回的偏差。接口health或模型合法JSON不等于业务通过。

## V5. 完成证据
交付测试命令和摘要、锁定样本/策略版本、原始评测结果、错误案例、scope冻结与恢复证据、报告计数对账、未验证项。独立保存本地/省级/IP三档准确性，不能用总体数字掩盖IP噪声。

## 程序名称兼容回归（2026-10-15）

同字段“克拉玛依/克拉玛依市”“新疆/新疆维吾尔自治区”仅按本次已发送非歧义候选的唯一实体绑定，原文/span不改，原始输出保留。跨字段、歧义“吉林/魔鬼城”、未知/错字、行政链冒充原文、额外字段和映射后重复仍拒绝。多原文别名用最长再字典序固定选取并审计。新旧策略隔离，AI提示和输入摘要不变；旧失败保留。16条保存响应只离线重消费，留出不读不跑。
