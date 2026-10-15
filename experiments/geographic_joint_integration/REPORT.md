# P2/P3/P4 离线联调报告

日期：2026-10-15。状态：**P2/P3/P4 离线联调完成、待原会话验收；正式链路未接入、地域启动阻断保留。** P4原会话返修复验仍待完成，本轮继续依据后续用户指令，不代替该复验。

## 启动依据及版本

S0完整读取启动文件及引用材料、核对固定Git对象/导入清单/返修证据。P2两项修复复核存在且与源码一致；P4风险绑定返修源码与REPAIR_MANIFEST一致，但没有找到原会话明确复验通过。最初因此停在S0。用户随后明确要求“为啥不联调，你刚刚不是修改过p4代码了吗 直接链条 我求你了”，本轮按该后续指令继续离线联调；**没有将P4实施者自测、旧验收或本轮结果视为原会话通过**。P4原会话复验仍待完成；启动文件第0/8节该门槛为明确例外，不能报告为满足。

- 独立工作树：`/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p234-offline/xhs-audit-agent-demo`；分支 `codex/geographic-relevance-p234-offline`。
- P1 HEAD：`031e0cd26379ee44c2372c9a28536c377ab3d4b9`；tag `checkpoint/geographic-relevance-p0p1-final-20261015`。
- P3：tag `p3-post-only-names-v2-local-20261015` → `2120c53379d1b79c45e3cb8232b9c9b96657b183`；保留P2/P4各自原样11文件导出，无整分支合并。
- P2：独立来源树两项修复后的未提交快照，21 authored文件/11 P3导出匹配其fix-source-audit。规则来自prompt_profile_snapshot，search_terms_max仍为1–200。
- P4：独立来源树风险证据绑定返修快照，23 authored、9保护历史、19返修证据匹配REPAIR_MANIFEST；该manifest SHA256为 `d00972fe6b41a77df776031a9f6788494c75317c0f0791d11820c5700d5f0f8a`。旧DELIVERY_MANIFEST仅保留历史，不作为返修源码权威。
- [INPUT_MANIFEST](artifacts/joint-20261015-01/INPUT_MANIFEST.json)登记209个导入文件、每文件来源/目标/字节/SHA256、验收状态及用户继续指令。导入前后、复制后和最终审计均验证相同字节。

## 实际实现

链路是：临时库真实P1资源解析/地域来源/设置 → P2 capture/build/validate/save/load → 新适配器建立三摘要绑定 → 冻结候选原文/评论/身份 → P2冻结原风险引擎 → 固定P3 prepare/consume/完整GeoRecord复核 → 原P4完成屏障、原排序、预留、精采、复核、结算、受控交付及恢复。

主链没有使用P4手填fixture_plan替代P2输入，没有复制状态机。四资源组合均走真实resolver；每词有效picks保持P2的1。为本实验选择select发生在测试捕获前；adapter收到off/compare仍拒绝。三类摘要分别保存P2原计划、P4投影、关联绑定；恢复的identity、摘要、execution ID来自调用方独立保存的expected，不能从待验payload自取后自证。

新增实现源码：

| 文件 | 用途 |
|---|---|
| adapter.py | 上游清单核验、P2→P4投影、外部绑定恢复、raw/normalized证据及风险/P3端口 |
| scenarios.py | 真实临时资源/计划构造及受控capture/detail/review/deliver注入，不替代P4状态机 |
| replay.py | 要求guard先安装的独立Python恢复入口 |
| run_offline.py | 业务导入前防护、受限临时根、明确子进程白名单、JUnit及意外调用判败 |
| audit.py | P1/P3 Git字节、209导入来源/目标、增量白名单、语法/空白只读核验 |
| tests/test_geographic_joint_adapter.py | 16个绑定/篡改/缺省/不支持契约用例及逐例证据 |
| tests/test_geographic_joint_execution.py | 38个真实计划驱动选择/预算/恢复/反例用例 |
| tests/test_geographic_joint_risk.py | 16个真实resolver风险请求/评分/参数/来源/IP对照用例 |

源码逐文件字节及SHA见[SOURCE_INVENTORY](SOURCE_INVENTORY.md)。另新增README、[INTERFACE_MAP](INTERFACE_MAP.md)、[CASE_MATRIX](CASE_MATRIX.md)、[INTERFACE_GAPS](INTERFACE_GAPS.md)、本报告及本轮独立证据目录。原P2/P4 runner回归输出只新增各自 `artifacts/joint-regression-20261015-01/`，不覆盖旧文件。P4重定向harness在内存中仅替换输出目录和实际命令记录，原runner源码字节不变。未引入依赖。

## 反例与关键结果

`old-risk-post/comments/identity`保留相同execution/query/content/rank和occurrence ID，分别更改原文、评论、认证信息后注入旧RiskRecord。原P4在首次评估持久化前及query完成屏障均抛出 `risk_capture_evidence_mismatch`，评估未落盘、精采0、交付0。认证反例同时保存原引擎当前 `discard` 和旧 `weak`，证明当前内容不会借用旧结果进入交付。

`detail-change-post/comments/identity`则在首次捕获已绑定后进行精采同档变化：重新做地域复核、保持最初风险、风险调用一次、正常交付。`detail-change-tier`变档不交付，但保留已采成功额度。两种时序没有混为一个拒绝规则。

四资源组合保存原P1基准与恢复联调的完整prompt/kwargs、规则文字、词/词组、身份排除、自命中、完整分数及原排序对照。`resources-*`证明晚词local weak优先早词province strong；`province-before-ip`证明省级weak优先IP strong。同档仍是原词序及原rank_candidates序。

独立子进程从after_reserve状态恢复，没有重复capture/risk/geo，完成一次detail及交付。其他断点涵盖capture/risk/geo/receipt/settle/delivery；返回未记receipt时保留预留并等待完整可信对账。对真实临时规则/词库/设置/草案做修改后，恢复阶段禁止live读取，冻结风险/P3请求及选择保持一致。

完整输入输出及测试节点映射见CASE_MATRIX；JSON记录原bundle、外部expected、原始捕获、三摘要、风险/P3请求响应、状态与调用轨迹。已知固定P3“新疆+乌鲁木齐”有效province_unknown被原样消费，没有增加过滤器。

## 新鲜验证

最终联合轮及原回归均已完成。以下是独立运行结果，不相加冒称一次全套：

| 独立轮次 | 结果 | 退出码/耗时 | 证据 |
|---|---|---|---|
| 最终joint三文件全部70用例 + 原guard17用例 | 87 passed / 0 failed / 0 error / 0 skipped；2既有依赖warning | 0 / pytest569.18s（runner569.30s） | [final-joint](artifacts/final-joint-20261015-01/summary.json) |
| P2原runner完整指定范围 | 682 passed / 4 failed / 0 error / 0 skipped | 1 / 150.83s | ../geographic_execution_plan/artifacts/joint-regression-20261015-01/ |
| P4原runner五份回归及全部scoped测试 | 284 passed，2既有依赖弃用warning | 0 / 531.85s | ../geographic_selection/artifacts/joint-regression-20261015-01/ |

P2的4个失败与精确P1基线JUnit直接按完整node及failure message比较一致，也与P2返修完整轮一致：[p2-regression-comparison.json](artifacts/joint-20261015-01/p2-regression-comparison.json)。它们是 `test_prompts_are_formatted_with_the_setting[10/6]` 的旧提示文本断言，以及 `test_task_creation_caps_search_terms_for_every_source[formal-temporary/temporary-temporary]` 的当前会话有效词库引用要求。未skip/xfail或改原断言，保留非零退出，不能称全仓全绿。

最终joint生成16条adapter契约证据、16条risk契约证据及39份执行JSON（38用例+独立子进程）。外层记录2次有意SQLite拒绝，意外尝试0，实际网络/模型/采集/调度成功0；P4原runner五项防护计数全0。8个新增源码文件编译通过，运行后摘要与测试前冻结摘要一致。

三套范围分进程执行、不叠加guard、不把各轮数量加总成一次全套。完整真实命令在每轮commands/invocation/checks中，所有开发失败轮保留；[RUN_HISTORY](artifacts/joint-20261015-01/RUN_HISTORY.json)逐轮记录实际命令、退出码、数量及耗时。

## 历史失败与修正范围

- guard-smoke：pytest35通过，但runner把两次有意bound-attach拒绝误归意外，故保留exit1；只修正精确节点/目标的外层探针分类。
- adapter首轮18通过/12error、execution-risk首轮50失败：P1默认off被正确拒绝；测试显式设置select后再捕获，adapter gate未放宽。
- risk-check-02：12通过/4失败；测试误以为请求总额7就是有效总额，且选择了超出P1设置上限的live值99；按P1有效限额和合法设置20修正测试。risk-check-03原四项通过。
- execution-check-02：30通过/6失败；测试误认为同分rank1先于rank2，原引擎实际rank降序，导致故障样例没被选中。只校正测试样例/预期，原排序不改。
- execution-check-03：9通过/1失败；限流恢复原实现同时报告awaiting_reconciliation及collection_failed，测试只断言前者；补全原行为断言，check04单项通过。
- adapter追加逐例输出只写测试证据；最终统一轮重跑全部。没有用分次通过相加代替最终全套。

## 边界、审计与未验证项

P1原1570文件逐字节不变；P2/P4导入209文件及来源树匹配；两份P3各11导出与固定Git对象相同。新增源码编译、AST和git diff --check/新文件空白检查记录在最终静态证据；无项目lint/typecheck配置，未安装工具、未改前端。

runner在pytest收集前安装防护，覆盖两类业务import别名，限制数据库至本轮固定临时根/输出根，子进程只允许明确的离线Python入口且继承防护。外层summary只统计外层日志事件；原guard测试中的局部有意拒绝以各测试断言为证，不能把外层数量冒称全探针总数。意外调用任何一次均使runner失败，即使被业务代码吞掉。

P4原会话返修复验仍待完成。人工模型响应只证明接口与程序行为，不证明语义质量/召回/成本。内存sink证明受控幂等；独立进程证明预留后恢复，不证明生产外部严格一次交付。临时DB随run生命周期删除，可通过runner重构并复跑，旧临时路径不作为永久可用库。

正式确认事务、Run/Job/hash穿透、正式库/权限、真实账号/采集、生产回调与对账、P5、前端/卡片/正式报告仍未接入，详见INTERFACE_GAPS。回执WATCH保持原范围，不新增阻塞。不联网、不真实模型、不采集、不启动服务、不写正式DB；未提交、推送、合并、部署或发跨会话消息。来源树与主工作区他人修改未动。

交接清单：[OUTPUT_MANIFEST](artifacts/joint-20261015-01/OUTPUT_MANIFEST.json)单列本轮新增源码、文档及输出证据；[static-checks](artifacts/joint-20261015-01/static-checks.json)、[source-audit-final](artifacts/joint-20261015-01/source-audit-final.json)、[evidence-completeness](artifacts/joint-20261015-01/evidence-completeness.json)提供最终核验。未修改任何原阶段验收结论。
