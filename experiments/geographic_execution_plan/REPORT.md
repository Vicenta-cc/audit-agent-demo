# P2 离线交付报告（2026-10-15）

状态：**P2 离线计划模块、冻结恢复及指定 P3 接口验证完成，待原会话验收；正式确认/Run/Job/采集未接入，地域启动阻断保留，P2/P3 整阶段未据此通过。**

本次按独立验收意见修复两项：风险冻结改为原引擎实际消费的 `execution.prompt_profile_snapshot`，计划验证绑定同一字段；搜索词上限参数恢复 P1 的 1–200 合法范围，默认仍为 10。仅修改新增模块与新增测试，没有修改生产评分、规则、黑话库或原搜索词。修复前 REPORT 的原始字节归档于 [pre-fix-report](artifacts/pre-fix-report-20261015.md)，SHA256 `2807ad0e42c36d6821a4fe4d8ec1fc41664427b7eb97108b76c9cfd52e29a529`；原不通过结论保留在 [ACCEPTANCE-20261015.md](ACCEPTANCE-20261015.md)。

## 基线与修改边界

工作树：`/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p2/xhs-audit-agent-demo`。分支 `codex/geographic-relevance-p2`，HEAD 保持 `031e0cd26379ee44c2372c9a28536c377ab3d4b9`，未提交。

| 用途 | 固定版本 |
|---|---|
| 完整应用基线 | P1 `checkpoint/geographic-relevance-p0p1-final-20261015` → `031e0cd26379ee44c2372c9a28536c377ab3d4b9` |
| P3 离线代码来源 | `p3-post-only-names-v2-local-20261015` → `2120c53379d1b79c45e3cb8232b9c9b96657b183` |
| 实施依据 | 主工作树 `.omx/plans/geographic-relevance-p2-start-v2.md`，字节 SHA256 `c604bb4d4d01eb2f7ff9ce617f54619e0abb3acd4cd0c54d933923e6f3860868` |

先读取新版启动方案、输入清单、固定 P1 报告及三份来源报告、固定 P3 tag/诊断报告、主实施计划相关章节及验证清单；37 项固定输入均从 Git 对象核对摘要，见 [preflight.json](artifacts/preflight.json) 与 [pinned-inputs-v2.json](artifacts/pinned-inputs-v2.json)。没有整份 cherry-pick P3，也没有从来源工作树复制未提交文件。

仅新增以下文件组：

- `backend/investigation_creation/scope_search_plan.py`：纯计划及专用类型。
- `tests/test_geographic_execution_plan*.py`：状态/来源、复制、资源、设置/严格类型、风险不变、恢复、P3 与离线防误调用测试。
- 本实验目录：authority、冻结风险、storage、P3 bridge、离线 runner/replay/source audit、文档、原样依赖和 fixture 证据。

原 P1 1570 个已跟踪文件逐项 SHA256 保持不变，Git tracked diff 为空。规则/黑话库工具、service/store/contracts/adapters、triage/matcher/pipeline/crawler、API 和前端没有修改。产品启动路径没有 import 新计划或实验模块。核对命令和全量结果见 [修复后 source audit](artifacts/fix-source-audit-20261015-01.json)，可用 `verify_sources` 复跑；固定输入另有 [独立 Git 对象核验](artifacts/acceptance-input-audit-20261015-01.json)。

P3 原样快照为 8 个指定 relevance 模块、原行政名录、SOURCE_MANIFEST、location_catalog，共 11 个文件，路径/分类/字节数/SHA256 见 [import_manifest.json](dependencies/p3/import_manifest.json)。每个文件再次与固定 P3 Git 对象逐字节比较；桥接另固定 manifest SHA。未导入 `relevance_store.py`，未使用旧 `evaluate_and_record`。

行政名录 3218 条、版本 `cn-division-2026.0.1-reviewed-p1-v1`，原文件字节 SHA256 `716c35d358e030b8d499979884fce462d4a2d6293b76fb0bfaad8f59062f01a6`。字节摘要与 canonical JSON 摘要分别保留。内容名录与别名 overlay 独立冻结；原文件和 prompt/schema 没有修改。

## P1/P2/P3/P4 衔接

| 阶段 | 本轮实际行为与后续责任 |
|---|---|
| P1 | 原应用基线保存内部地域状态、真实聊天消息或 UI 操作、revision、采用资源；原地域启动阻断保留。 |
| P2 | 从隔离 P1 stores 捕获真实 fixture 权威来源，冻结当前身份/资源/设置/完整风险输入/原样搜索计划/P3 profile，独立保存和恢复。 |
| P3 | 在隔离实验命名空间，以恢复内容构造 names-v2 请求、消费明确人工响应、独立输出地域结果；没有真实模型调用。 |
| P4（后续） | 原预筛选输出风险结果；P4 消费风险与地域两份结果，完成跨词本地优先、分档补充、最终选择及必要精采时序。候选汇总、额度、执行检查点本轮未实现。 |

真正执行时先按已确认搜索词搜帖子。原风险评分和 P3 地域判断可独立进行；地域档位用于后续选择，不改原风险结果。模型只看原帖实际文字和有限地点候选，不注入目标、query、风险、评论文字或 IP。

不再预先把同帖分配给最早搜索词。各 query 保留自己的 rank/评论/搜索自命中上下文，不用首来源分数，也不跨词取最高分。后续 P4 按实际选中词预留额度，精采成功才结算，失败释放；A/B 均召回 X、A 选 Y 后 B 仍应能选 X。静态计划没有帖子 owner、已选帖子或预算余额字段。

## 计划与权威输入

schema 为 `geo-execution-plan-v1`，`validation_origin=fixture`。入口：

```python
build_scope_search_plan(authority, *, expected_identity)
validate_scope_search_plan(plan, authority, *, expected_identity)
serialize_scope_search_plan(plan)
parse_scope_search_plan(raw, *, expected_identity, expected_digest)
```

`expected_identity` 由调用方独立传入 owner/session/draft/revision。构造与校验从外部权威重新派生；对篡改 query/source/order/额度后重算自身 hash 的计划仍拒绝。恢复核对独立预期 identity/digest，仅解释原冻结输入；计划 hash 不代表确认授权。严格 JSON 拒绝重复键、未知 schema/策略、bool 冒充整数、非法 bool、非有限数、缺必需字段及不一致摘要。

`capture_fixture_authority` 只允许系统临时目录或本实验的真实 P1 数据库。原 resolver 在真实资源 fence 内解析四种资源组合；同 fence 读取统一设置 revision 和风险词条/分组。来源读取采用只读连接，没有真实确认、Run/Job 或采集动作。

- 聊天来源核真实 user message/turn、owner/session、sequence/quote、任务锚点、源 revision 与当前 revision。
- UI 来源核真实选择表 principal/session/operation、region/catalog、expected/committed 两个 revision 和精确来源配置；无需 quote/turn，不生成聊天消息。选择发生后改标题、规则或词库仍合法。
- 复制检查真实 copy tool receipt、来源与目标身份、source revision/configuration 和目标 revision 1；合法一跳/多跳均有真实工具测试。
- 临时词库验证真实 edit ref、owner/session、历史编辑版本内容与 source IDs；规则验证真实 approval、采用 revision、用户消息和经现有 `published_presentations` 核验的历史展示。编辑器升级不替换已采用旧快照；合法抽屉修改脱离旧 ref 后可冻结。
- 正式资源保留真实 ID/版本或 runtime hash；临时资源保留实际内容与来源，不编造正式版本。无权访问/错内容/未知引用/缺采用凭据明确失败。
- 任务参数采用统一设置与 revision，草案自带参数不作为权威。revision=0 对应冻结 defaults。每词上限固定 1，候选数 `max(triage_candidates_per_keyword, max_notes)`；评论关闭时上限 0，开启时取有效精采上限与预筛选上限的较小值。

克拉玛依市/深圳市/吉林省/吉林市/北京市/新疆/上海的行政和执行层级分别验证。地级市执行 city，普通省份执行 province，直辖市保留省级行政代码并执行 city。区县、不支持的地级类型、pending/不完整来源不猜测；仅本轮 `dy + search` 可构造地域计划，其他平台/creator 明确拒绝。历史无 scope 与 cleared 无 scope 返回无地域计划。

## 搜索、风险与恢复证据

确认词、平台 query 和原风险评分 search_keyword 使用同一原词。真实临时词库测试保留顺序和内部空格：

| 确认词 | 冻结 query |
|---|---|
| `克拉玛依市包过` | `克拉玛依市包过` |
| `包过` | `包过` |
| `北京路欠薪` | `北京路欠薪` |
| `克拉玛依市 包过` | `克拉玛依市 包过` |

同 query 的两个合法 entry 都进入 sources，但只执行一次、不增加额度。沿用原 resolver 的上限、优先级和精确去重，不更改 execution.keyword。仅改地域后，规则、词库、query、来源映射和 execution.keyword 保持一致。`search_terms_max` 的默认值仍是 10；1–200 是 P1 已支持的配置范围，并不表示本轮增加实际搜索词。新增定向测试用仅一个真实搜索词分别冻结 10/100/101/200，保存序列化后仍保持该词原样。修复前 [3 failed / 2 passed](artifacts/fix-contract-red-20261015-01/pytest.log)，修复后 [5 passed](artifacts/fix-contract-green-20261015-01/pytest.log)，其中另一项确保把结构化 `rule_snapshot` 填进风险规则并重算摘要也会被拒绝。

冻结风险适配器保留实际 TriageTerm 六字段、顺序、search_groups、原 triage 实际使用的 `prompt_profile_snapshot`、模型标识及提示/评分/身份参数。结构化 `rule_snapshot` 继续独立保存在 resolution 中。适配器通过原 `terms_for()` 建立词条列表与分组关系，没有写引擎私有 cache，也没有修改原评分引擎。

本次新增真实捕获恢复对照：正式/临时规则 × 正式/临时词库四组合，由真实 P1 resolver、真实词库存储和 settings 构造原评分引擎基准；另一端必须经过 `capture_fixture_authority → build → SQLite 保存 → load → frozen engine`。比较完整请求正文及参数、审核目标、原词、词条/分组、身份排除、命中/自命中、分数和排序，并用恢复输入切换 P3 local/other_region；只有实验入选资格 true/false 变化，风险请求和输出保持一致。每组合各端仅 3 次人工客户端调用，另 3 项身份排除在调用前完成；无真实模型调用。

有效修复前对照 [4 failed](artifacts/risk-capture-red-20261015-02/pytest.log)及 [完整四组合输入输出](artifacts/risk-capture-red-20261015-02/captured-risk-comparison.json)证明：原审核目标 85 字、恢复后 0 字，完整请求不等；固定人工回答下分数相同并不能证明请求正确。修复后 [4 passed](artifacts/risk-capture-green-20261015-01/pytest.log)及 [完整四组合对照](artifacts/risk-capture-green-20261015-01/captured-risk-comparison.json)中审核目标为 85→85，每组合全部 8 项断言成立。首次 `risk-capture-red-20261015` 是新增测试初稿误要求不存在的 `evidence_rules` 字段产生 KeyError，只保留调试历史，不作为业务缺陷证据。原独立验收的 91→0 属另一个样本，不与此处混用。

原有人工词库 fixture 对照保留作补充，验证同帖不同 query 分别保留 400/800 分、同词 rank 1/9 上下文及排序，不用它替代上述真实捕获路径的证明。完整输入、人工响应及原引擎输出见最终完整轮的 `risk-comparison.json`；四组合实际捕获证据见 `captured-risk-comparison.json`。人工回答验证输入适配的一致性，不证明真实模型语义质量或 P4 正式编排。

实验 SQLite 保存内容寻址 blob 和计划。实际修改 live 草案、规则/词库编辑器、统一设置后仍恢复旧计划；冻结名录内容内嵌，并阻断恢复时磁盘名录读取。独立 Python 进程恢复的完整计划逐项一致。缺副本、未知 storage/plan schema、内容损坏、错外部 identity/digest 均失败，不回读最新补齐。计划恢复不代表执行进度恢复。

P3 固定 true/true 及两个 bool 四组合均验证；模型配置来源、实际标识、调用配置、prompt/code/schema、三份名录/overlay 与 IP fixture profile 一起冻结。调用路径为 `context_from_scope_state → names_v2.prepare_request → names_v2.decision`。人工 located/no_location/不足/冲突/非法响应、作者 IP 无评论、评论稳定身份与 10人/50%/60% profile、跨帖身份与缺文/截断等边界有断言。IP 只在成功 no_location 时可能补充；评论门槛不限制作者 IP。local_only 排除 ip_only，普通省级只收本省 local，城市补充 local→province_unknown→ip_only 且 IP 须有效 strong。消费器明确 `production_p4=False`，没有实现选帖或额度。

## 命令与测试结果

已有解释器 Python 3.12.10：`/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python`。测试入口在 pytest 导入及 collection 前安装网络/真实模型/采集/调度 guard，Python 子进程通过 sitecustomize 继承。SQLite 仅限内存、系统临时目录、显式实验输出；业务源库未被打开写入。防误调用 guard 自测的拦截事件单独计数，不计为真实调用。

修复后的单次完整回归：[summary](artifacts/fix-full-20261015-01/summary.json)、[全部 argv/环境路径](artifacts/fix-full-20261015-01/commands.json)、[JUnit](artifacts/fix-full-20261015-01/junit.xml)、[日志](artifacts/fix-full-20261015-01/pytest.log)。**686 项，682 passed / 4 failed / 0 errors / 0 skipped，158.09 秒；新增 160 项全部通过，原回归 522 passed / 4 既有失败。** 四项失败保留 pytest exit code 1，不跳过、xfail 或把测试称为全绿。

定向绿测另外验证本次新增的 9 项：四组合风险 4 passed；范围与错误来源拒绝 5 passed。最终完整轮重新执行它们及全部新增/原针对性回归，未把分次结果合成完整轮。

| 测试组（`tests/test_*.py`） | 通过 | 失败 | 证据轮次 |
|---|---:|---:|---|
| geographic_execution_plan | 29 | 0 | 修复后完整轮 |
| geographic_execution_plan_guards | 17 | 0 | 修复后完整轮 |
| geographic_execution_plan_integration | 25 | 0 | 修复后完整轮 |
| geographic_execution_plan_p3 | 49 | 0 | 修复后完整轮 |
| geographic_execution_plan_provenance | 11 | 0 | 修复后完整轮 |
| geographic_execution_plan_resources | 13 | 0 | 修复后完整轮 |
| geographic_execution_plan_risk | 12 | 0 | 修复后完整轮 |
| geographic_execution_plan_storage | 4 | 0 | 修复后完整轮 |
| geographic_draft_configuration | 32 | 0 | 修复后完整轮 |
| geographic_scope_catalog | 36 | 0 | 修复后完整轮 |
| geographic_national_catalog | 34 | 0 | 修复后完整轮 |
| geographic_scope_context | 8 | 0 | 修复后完整轮 |
| geographic_scope_selection | 21 | 0 | 修复后完整轮 |
| geographic_scope_selection_history | 12 | 0 | 修复后完整轮 |
| geographic_scope_intent | 15 | 0 | 修复后完整轮 |
| unified_draft_configuration | 78 | 0 | 修复后完整轮 |
| frozen_workspace_configuration | 22 | 0 | 修复后完整轮 |
| temporary_ruleset_execution | 49 | 0 | 修复后完整轮 |
| search_terms_cap | 20 | 4 | 修复后完整轮 |
| investigation_draft_configuration_m3 | 117 | 0 | 修复后完整轮 |
| task_settings | 14 | 0 | 修复后完整轮 |
| triage_engine | 55 | 0 | 修复后完整轮 |
| triage_matcher | 9 | 0 | 修复后完整轮 |

实际复跑命令（在本工作树根目录；`python` 指上文已有解释器）：

```sh
python -m experiments.geographic_execution_plan.run_offline --output-dir experiments/geographic_execution_plan/artifacts/fix-full-rerun-01
python -m experiments.geographic_execution_plan.run_offline --output-dir experiments/geographic_execution_plan/artifacts/fix-new-tests-rerun-01 --new-tests-only
python -m experiments.geographic_execution_plan.run_offline --output-dir experiments/geographic_execution_plan/artifacts/fix-p1-baseline-rerun-01 --baseline-ref 031e0cd26379ee44c2372c9a28536c377ab3d4b9
python -m experiments.geographic_execution_plan.verify_sources --output experiments/geographic_execution_plan/artifacts/fix-source-audit-rerun.json
python -m experiments.geographic_execution_plan.replay --evidence-dir experiments/geographic_execution_plan/artifacts/fix-full-20261015-01
```

输出目录不覆盖；复跑时换一个全新名称。最终完整轮实际输出目录为 `fix-full-20261015-01`，准确解释器、pytest argv 和防护配置以其 commands.json 为准。`--new-tests-only` 会 collection 原 M3 的 117 项再 deselect，保留其修改 TMPDIR 的环境副作用；runner 在父子进程导入前固定 `GEO_OFFLINE_TEMP_ROOT`，不放宽 SQLite 边界。

当前可回放的 [example plan](artifacts/fix-full-20261015-01/example-fixture-plan.json)、[P3 人工响应](artifacts/fix-full-20261015-01/p3-manual-replay.json)、[独立 replay CLI 输出](artifacts/fix-full-20261015-01/replay-cli.json)、[四组合真实捕获风险对照](artifacts/fix-full-20261015-01/captured-risk-comparison.json)和[补充人工词库对照](artifacts/fix-full-20261015-01/risk-comparison.json)均来自修复后完整轮。CLI 恢复后 request/decision/consumer 三项均相同，真实模型调用为 0；全局 2 次 SQLite 拦截属于故意拒绝探针，外部网络/模型/采集/调度成功为 0。旧失败轮次计划仅作历史证据，不作为当前恢复入口；旧错误风险字段会被新校验拒绝，需要重新捕获 fixture，不回读 live 数据补齐。

四项既有失败已在精确 P1 `git archive` 临时副本中复现：[基线 summary](artifacts/acceptance-p1-baseline-20261015-01/summary.json)、[commands](artifacts/acceptance-p1-baseline-20261015-01/commands.json)、[JUnit](artifacts/acceptance-p1-baseline-20261015-01/junit.xml)、[日志](artifacts/acceptance-p1-baseline-20261015-01/pytest.log)。[本次逐节点及原因对照](artifacts/fix-test-comparison-20261015-01.json)解析两轮 JUnit，确认节点与原因完全一致：

| 失败节点（`test_search_terms_cap.py`） | 修复后完整轮与精确 P1 的共同原因 |
|---|---|
| `test_prompts_are_formatted_with_the_setting[10]` | 仍断言旧资源提示语“默认整组 1 至 10 个实际搜索词” |
| `test_prompts_are_formatted_with_the_setting[6]` | 同一旧提示语断言，参数为 6 |
| `test_task_creation_caps_search_terms_for_every_source[formal-temporary]` | 临时召回 fixture 缺当前会话有效资源引用，`RESOURCE_REF_INVALID` |
| `test_task_creation_caps_search_terms_for_every_source[temporary-temporary]` | 同上 |

这四项本身不阻断本轮离线交付；未修改原提示词、测试或资源引用保护。旧 `p1-baseline-search-cap-20261015` 的第四项曾先被 SQLite guard 阻断，不能证明其资源引用失败；本报告改用上面精确 P1 新复现，不沿用那份不足的证据。

历史验证仍保留：修复前独立验收 `acceptance-full-20261015-01` 为 673 passed / 4 failed（新增 151 项当时未覆盖本次真实边界）；更早 `offline-20261015-01/02/03` 以及 `offline-new-tests-temp-root-20261015` 的失败、guard/临时根修正过程见[修复前报告原件](artifacts/pre-fix-report-20261015.md)。这些轮次不替代最终完整轮，也未修改历史产物来消除失败。

两路只读独立复审已给出 **code-reviewer APPROVE / architect CLEAR**，仅批准这次修复，详见 [FIX-REVIEW-20261015.md](FIX-REVIEW-20261015.md)。原始不通过记录继续保留，本轮仍按既定离线范围交原会话验收。

没有适用的独立 Python lint/typecheck 配置。实际执行 Python 编译、Git diff 空白检查、所有 authored 文件尾空白检查、原文件 SHA256 比较、P3 Git 字节比对和产品导入扫描；不虚报 lint/typecheck/build。前端和已归档 P0 八份测试未运行，均未修改。

本轮未联网、未安装依赖、未调用模型、未启动采集/worker/运行服务、未读写正式业务 DB、未发消息到其他会话，也未 commit/push/merge/deploy。原无地域测试可在隔离库创建自己的 fixture Run/Job；地域预览/service/直接 store 拒绝路径仍断言无新地域 Run/Job/调度。

## 后续正式接入与共享文件冲突清单

| 接点 | 后续需要实施与验收 | 主要共享文件 |
|---|---|---|
| P2 正式冻结 | 在真实资源 fence/确认事务重读草案 revision、双来源、四资源和设置 revision，以权威输入重算；为正式规则分支提供同等防篡改校验。 | resources.py、service.py、store.py |
| Run/Job 契约 | 同步 execution/resolution/V4/hash/白名单、JobStore 写读恢复、Run→Job 对账；历史缺省省略/hash不变，丢地域字段拒绝降级。 | contracts.py、frozen.py、adapters.py、JobStore/API |
| P3 正式来源 | 实现验证真实确认快照的 frozen-origin adapter；不能将 fixture 字符串改成 frozen 充当授权。 | relevance_scope.py、正式冻结适配 |
| 调用/结果保存 | names-v2 调用封装、调用前去重与预算登记、模型/prompt/名录/证据身份、并发/超时未知结果/保存失败/重试预算；风险缓存另绑定完整原 query/rank/comments 输入。不能承诺外部调用严格 exactly-once。 | P3 调用器、结果存储、预算/检查点 |
| P4 编排 | 原搜索及评分引擎保持原样，汇总全计划候选，两份结果独立消费；跨词本地优先，全候选完成后再开放补充。保留各 query 风险上下文；实际选中词预留，精采成功结算、失败释放。验证 A/B/X/Y、失败、并发、重启不双占/多计。 | pipeline.py、执行适配/新选择器；triage 只读 |
| 精采与失败 | 精采文字变化/冲突重验，区分模型失败、地域未知、无风险、无符合内容与采集失败；提供执行进度恢复证据。 | 精采编排、检查点、审核交付 |
| P5 展示 | 已确认搜索词、冻结范围/策略、历史来源与计数；分别展示本地/省级/IP依据，保留选择历史。 | API、前端卡片/报告 |
| 最终启用 | P4/P5、P3语义质量与支持平台/模式验收后，以独立启用改动解除相应能力阻断；从正常入口以隔离DB/受控采集器/人工响应跑成功确认→Run→Job。能力未就绪继续阻断，不加 bypass。 | scope_intent.py、正式 guard 及端到端测试 |

P3 固定 tag 已有 12 条新编诊断文本的真实调用：完整预期 11/12、实体集合与档位 12/12。本轮未重跑这些样本，人工响应测试不计入语义准确率，不能据此宣布 P3 整阶段验收通过。真实平台召回、生产 IP 门槛、正式确认/Run/Job、跨词执行与执行进度恢复仍需后续独立验收。
