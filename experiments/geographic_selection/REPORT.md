# P4-offline-v1 独立交付报告

日期：2026-10-15。范围为独立选择、实验执行状态和受控离线编排。首次交付及其 261 项回归记录保留在下文；风险证据绑定验收返修见文末。正式地域流水线尚未验收。

## 版本、工作树及只读边界

| 项目 | 本轮实际值 |
|---|---|
| 工作树 | `/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p4/xhs-audit-agent-demo` |
| 分支 | `codex/geographic-relevance-p4` |
| HEAD / 完整应用基线 | `031e0cd26379ee44c2372c9a28536c377ab3d4b9` |
| P1 tag | `checkpoint/geographic-relevance-p0p1-final-20261015` |
| P3 tag | `p3-post-only-names-v2-local-20261015` |
| P3 解引用 commit | `2120c53379d1b79c45e3cb8232b9c9b96657b183` |
| P2 | 仅 `p4-fixture-input-v1`；未取得并验收实际 P2 交付，不追随其分支 |

P4 从最终 P1 新建，未在 P3 工作树开发。P3 tag 是附注标签，按 `^{commit}` 核验，不把 tag 对象当提交。没有整分支合并、cherry-pick 或提交；新增文件仍留在 P4 工作区。

固定输入核验为 29 份 P1 + 14 份 P3 Git 文件、两个 tag/commit/tree、两份 P2 文档观察摘要。保存 6 份计划文档快照。实际只导出 8 个被 import 的 P3 模块和 3 份名录/manifest，共 11 份原字节文件；没有导出 RelevanceStore、provider 配置、原模型输出、媒体、密钥、环境或业务数据库。

证据：`preflight.json`、`pinned_docs/`、`dependencies/IMPORT_MANIFEST.json`，以及可重跑 `verify_inputs.py`。P1 固定文件逐字节一致；`git diff --name-only HEAD` 为空，所有既有跟踪文件未改。生产目录原有代码没有 import 新模块；pipeline/triage/crawler_adapter/JobStore、P1 创建确认/API/前端、P2 和 P3 原树保持只读。

## 本轮文件与接口

新增生产候选模块仅三份：

| 文件 | 职责 |
|---|---|
| `backend/audit_agent/scoped_selection.py` | `validate_plan`、`eligible_tier`、`select_next_batch`；无 I/O 的资格、屏障、档位、原顺序和额度选择 |
| `backend/audit_agent/scoped_selection_store.py` | `Store` 显式实验 SQLite；完整输入、查询进度、批次、预留、尝试、receipt、结算、复核、交付回执与追加事件 |
| `backend/audit_agent/scoped_execution.py` | `ScopedExecution` 只接受注入式捕获/风险/地域/精采/复核/交付/账号/停止；调用原 `rank_candidates`，不复制旧 pipeline |

四份新测试是 `test_scoped_selection.py`、`test_scoped_selection_store.py`、`test_scoped_execution.py`、`test_scoped_bridge.py`。其余新增均在本实验目录：原样依赖、fixture/桥接、拦截入口、核验脚本、示例、文档和验证证据。完整文件摘要见 `artifacts/DELIVERY_MANIFEST.json`。

`plan` 绑定 fixture 标识、execution、owner/session/draft/revision、scope/policy、P3 与名录/prompt/model/risk 摘要、有序 query、原搜索上下文/参数引用和双上限。只支持本轮 `dy/search/select`；未知策略、off/compare、坏地域和错误绑定明确拒绝，不转为普通任务。

一个 occurrence 对应一个 query/rank/source_keyword/证据上下文。同帖跨词保留不同 occurrence/risk input，不取最早词或跨词最高分。RiskRecord 保存完整原输入/config、原 CandidateScore 全字段、人工响应和有效性；GeoRecord 保存完整原 P3 request/decision、版本、原始人工响应和依据。摘要验证一致性，不提供正式授权。

实验桥接调用原 `context_from_scope_state → prepare_request → decision`，不调用 evaluate/旧 evaluate_and_record。评论文本不进入地域语义请求；IP 的身份、阈值和统计原样使用 P3。注入的 `validate_geo_record` 重新构造原请求并重放原 decision，精确比较结果，不能只修改 tier/IP 字段冒充有效结果。P4 核心通过显式注入使用此校验，产品启动路径不 import 实验依赖。

## 选择轨迹和风险不变

选择顺序为地域档外层、原 query 顺序、每 query 的原 `rank_candidates` 顺序。一个批次只有同 query/同档，容量同时扣除成功和有效预留。原始 rank、score、band、model、model_risk、hits、engagement 不变，不强制 strong 在 weak 前。

实际可重跑三词示例见 `demo.py` 与 `artifacts/example.json`：

| 原词序 | 原风险 | P3 档位 | 每词1/总额2时 |
|---|---|---|---|
| A | strong | province_unknown | 本地档未耗尽前不预留 |
| B | weak | local | 先精采并交付 B |
| C | strong | local | 再精采并交付 C |

实际轨迹先捕获 A/B/C，再 detail B、deliver B、detail C、deliver C；最终成功2、交付2、预留0、未完成 query 0。总额1的回归只选 B，不因 C 更高风险替换原词序。省级目标仅消费本省 local；local_only 不消费省级/IP；IP 还必须是原引擎有效 strong，允许有效 strong 缺 risk_score，不设置额外分数门槛。

桥接回归对同一原文、评论、rank、source、词库分组、规则和参数，直接比较原引擎实际 prompt/calls、命中、自命中排除、身份丢弃、完整 CandidateScore 和原排序。切换目标地域只改变 GeoRecord/最终资格，风险结果逐字段一致；失败回退 weak 不变、不当有效 strong。

固定 P3 已知混合省市行为原样保留：目标克拉玛依，单独乌鲁木齐为 other_region；新疆+乌鲁木齐的有效 province_unknown 按省级档资格消费，不另加纠错或阻断。此项证明消费契约，不证明语义准确率。

## 状态、失败与恢复

数据库只在显式临时目录或实验路径。Store 拒绝非实验路径、已有非 `scoped_selection_` 表及常用正式库名，不连接 JobStore/RelevanceStore。本轮使用一张 `scoped_selection_executions` 表保存带摘要的完整 snapshot；事务中同时更新状态、operation 回放结果及只追加事件。此结构便于实验核验，不是正式数据库迁移方案。

所有预留、额度/实体检查、当前批次和状态变化均在 `BEGIN IMMEDIATE` 内重读，两个独立连接竞争不会超额或跳到后词/低档。只允许一个活动批次。开始捕获和开始精采使用独立抢占 operation ID，避免把幂等重放返回值误当作再次执行外部调用的授权。实体仅临时预留，成功才按实际词结算；失败释放后不保留首次召回 owner。

| 事件 | 实际行为及测试 |
|---|---|
| 全词完成屏障 | pending/capturing/captured/failed/paused 不当零候选；成功零候选才 evaluated；未完成不选省级/IP |
| 空 detail | 释放该预留、不计成功；query 持久 no_refill；仅继续此前同批已确定 picks，不再为该词跨档补挑 |
| typed 媒体失败 | 跨档关闭该词，释放失败和同批未发起预留；不回滚成功或释放未知结果；其他词可再选失败帖 |
| 认证/验证失败 | 排除失败 occurrence、取消未发起预留；最多一次换号重选；auth_pending、替换账号、额度跨档/重启保存；无替换/再次失败传播并持久阻断 |
| 限流/不完整/基础设施异常 | 原类型传播，保留阶段；结果未知保留预留，不当零候选或开放补充 |
| 精采前/已返回但未记 receipt | 未发起的预留可继续；已进入 collecting 而无完整 receipt 则待对账，不自动重采 |
| receipt 已记未结算 | 完整 execution/plan/query/platform/key/post/occurrence/attempt/content 摘要通过才继续结算一次 |
| 结算后未交付 | 从已保存完整内容继续，不重采、不释放成功名额 |
| 已交付但回执未记 | 稳定 idempotency key 重放受控交付器；取得可信完整回执才记 handed_off |
| 精采文字/证据变化 | 原 P3 人工响应适配复核；仅同档且仍合格交付；任何变档/冲突/不足/协议失败保留 collected_not_delivered，成功额度不释放、不补采 |
| 暂停 | 不新捕获/预留/精采，保留批次/进度和逐 occurrence 已完成 risk/geo；恢复不读取当前资源或重评完整缓存 |

同 operation/receipt ID 同 payload 幂等，不同 payload 冲突。完整 receipt 也检查帖子身份，不接受旧/跨任务/部分结果；不使用旧 load_collected_selections/candidates.json。snapshot 摘要能检测损坏，不能替代来源授权；计划变化需要新 execution，禁止覆盖旧绑定。

交付前不向正式审核透传 detail callback，候选回调被隔离；没有伪造报告。结束输出分别保存候选、排除、不合格、地域不足/冲突、成功采集、已交付、已采未交付、预留及未完成 query；多个原因可以并存，不把失败改写为 triage_found_nothing。

## 验证证据

最终整体回归：**261 passed，2 warnings，402.14 秒，退出码 0**。原始命令、退出码及计数见 `artifacts/checks.json`，完整日志见 `artifacts/pytest.log`。两项 warning 来自既有 Starlette 依赖的 python_multipart / AnyIO 弃用提示。

五项拦截计数全部为 0：network_attempts、process_attempts、real_model_attempts、crawler_launch_attempts、forbidden_database_attempts。独立示例也记录相同五项零计数。固定输入核验 43 份 Git 文件与 11 份导出均通过，既有跟踪文件改动为零。

指定原回归为 `test_triage_engine.py`、`test_triage_matcher.py`、`test_triaged_search.py`、`test_triage_account_rotation.py`、`test_geographic_draft_configuration.py`，加全部四份新测试。既有地域测试经真实预览/service/直接 store 拒绝路径验证阻断，普通 triaged_search/rotation 使用原有回归，没有 mock guard。

| 必验组 | 对应本轮证据 |
|---|---|
| 原顺序/档位/策略/屏障/IP/已审与身份 | selection 测试；桥接真实 P1/P3；三词示例 |
| 风险输入/原 prompt/分数/rank 不变 | bridge 测试与五份原回归 |
| 双预算/实体归属/并发/事件和提交回滚 | store 两独立连接、配额/同帖竞争；execution 并发 capture/detail 与 begin 提交失败 |
| 空结果/媒体跨档关闭/认证一次及重启 | store 与 execution 分类测试 |
| 捕获和分项评估缓存/精采各中断窗口 | execution after_capture/risk/geo/reserve/detail/receipt/settle/delivery 参数化测试 |
| 回调隔离/变档不交付/交付失败恢复 | execution 测试；完整身份 receipt 负向测试 |
| 结束原因/暂停/成功零候选/捕获失败 | execution 与 selection 测试 |
| 禁区、无新增入口、阻断保留 | verify_inputs、static-checks、地域既有拒绝测试和原普通路径回归 |

测试入口的审计 hook 阻断网络/进程，显式替换两个包命名空间的真实 Qwen 与爬虫启动方法，SQLite 只允许本次临时测试根目录。即使被业务异常处理捕获，任何拦截计数非零仍使整次测试失败。应用 data/outputs/认证库/调度库只重定向到测试临时路径，没有启用地域能力或改 guard。具体临时库路径、实际命令、退出码和计数存入 checks.json。

静态检查：新增 Python 编译、已有/新增文件空白检查、既有产品 import 检查、tracked diff 检查；实际结果见 `artifacts/static-checks.json`。项目无现成全仓 Python lint/typecheck 配置，不虚报此类检查；没有前端修改，不新增前端测试；没有联网安装依赖。

开发期第一轮编排测试曾有5失败/8通过：夹具省级文本错误配了本地人工响应、测试预期未按原 rank 降序，以及断言忽略并列结束原因。修改夹具/预期并保持原评分代码只读后，首批13编排测试通过。早期还出现一次未设置 PYTHONPATH 的 collection error；已使用下列统一入口消除。最终完整回归已全部通过，上述开发期失败保留为诊断记录。

## 重跑

```sh
cd /Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p4/xhs-audit-agent-demo
PYTHONPATH=backend:. /Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_selection.verify_inputs
PYTHONPATH=backend:. /Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_selection.run_checks
PYTHONPATH=backend:. /Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_selection.demo
git diff --check
```

完整实际 `python -m pytest -q --basetemp=... <九份测试>` 命令及相同拦截配置的启动命令见 checks.json。Python 为已有3.12环境，未复制虚拟环境。测试/demo 数据库均为临时生成的夹具库，结束后删除；保留命令、日志、计数和紧凑示例，不保留正式数据。

## 限制、待接入与停止状态

详细接口清单见 `INTERFACE_GAPS.md`。P2 实际冻结接口/真实确认来源尚未联调；正式 pipeline、Run/Job、账号租约、真实完整 receipt/callback、正式 DB 迁移、筛选鉴权/P5 和启用验收均未实施。预计冲突的 pipeline/JobStore/创建确认/正式回调文件未提前编辑。

跨词地域优先要求全计划候选先完成，首结果等待和候选请求成本可能增加；最终成功预算不增加，无地域旧路径未变。人工响应只验证程序契约，不证明 P3 语义质量或真实平台召回。受控交付幂等不等于真实网络/审核 exactly-once，未知窗口仍需可信对账。

**首次交付历史状态（后续验收 REQUEST CHANGES）：P4 并行独立选择模块、实验执行状态及离线验证完成，待原会话验收；P2实际接线/正式pipeline/Run/Job/真实采集未接入，地域启动阻断保留，P4整阶段未通过。**


## 风险证据绑定验收返修（2026-10-15）

完整读取 `ACCEPTANCE-20261015.md`、原反例脚本/JSON 及独立审查记录后，仅返修 HIGH：同 execution/query/content/rank 的旧 RiskRecord 不能用于当前原文、评论或身份不同的首次候选。WATCH 回执状态协议本轮未修改；现有受控交付契约仍为正常返回表示成功、失败抛异常，不在本轮新增状态协议或正式交付器。

原验收报告、`artifacts/acceptance-20261015-01/`、首次回归 checks/log 及首次交付清单保持原字节。返修前 REPORT 副本与这些文件的初始摘要存于 `artifacts/risk-binding-repair-20261015-01/`。原开发失败和验收失败记录均保留。

### 修改清单与字段契约

| 文件 | 本轮修改 |
|---|---|
| `backend/audit_agent/scoped_selection.py` | 抽出 `validate_risk_record`，沿用既有身份/config/score检查，并将完整风险 item/comments 对照首次捕获原始字段；选择消费也执行此检查 |
| `backend/audit_agent/scoped_selection_store.py` | risk 首次落盘前从不可变 `query.capture.occurrences` 唯一定位原 occurrence；query 完成前重新对照完整捕获及风险；部分/完成缓存加载时重验，拒绝只重算自摘要的旧记录 |
| `backend/audit_agent/scoped_execution.py` | 精采后复核显式传入 reservation 保存的初始 candidate 作为 `risk_candidate`；新 candidate 仍仅作地域复核，不重跑风险 |
| `tests/test_scoped_selection.py` | 原文/评论/认证旧记录混用拒绝，以及标题、签名、昵称、认证、粉丝、互动字段回归；纯函数夹具补齐真实 item/comments 对应关系 |
| `tests/test_scoped_selection_store.py` | 首次写入、完成屏障、缺捕获、重算快照摘要后旧缓存拒绝；原事务测试夹具先保存捕获再完成，保留配额/回滚/恢复断言 |
| `tests/test_scoped_execution.py` | 编排早拒绝三类旧风险，正常原始/规范化字段差异、精采后文字/评论/身份同档变化仍交付且不重评分 |
| `REPORT.md` 与本次独立证据目录 | 保留历史，追加返修命令、失败/通过证据、源码及验收文件摘要 |

字段关系不是两个形状不同对象的盲目比较：P3 的 `candidate.post` / `candidate.comments` 是规范化地域投影，`candidate.raw_source.post` / `.comments` 保存传入规范化前的完整原对象。风险 `input.item` 必须精确等于原始 post，`input.comments` 必须精确等于完整有序原始 comments（缺省空列表），不与规范化 post/comments 比较。没有 normalization_version 的原始测试对象直接使用自身 post/comments；声明已规范化却缺 raw_source 的输入拒绝。

规范化前后映射沿用已有桥接校验，风险对象按两份绑定对象的 canonical JSON 比较，并区分 True 与 1 等标量类型。完整原帖比较覆盖 title/desc/nickname/user_signature、enterprise_verify_reason/custom_verify/follower_count、互动计数及 source_keyword 等影响评分/排序字段，不复制或修改评分规则。完整原评论比较保留 ID、顺序和 content/source_text/text 等全部原字段；P3 与风险引擎的评论别名优先级不同，不能仅比较某个规范化 text。已有 `validate_geo_record` 继续用原 P3 normalize 和 request/decision 重放校验规范化关系，P3 本身未改。

首次风险写入只接受已经持久化的 capture 内的 occurrence，排序完成列表的全部非 risk/geo 字段必须等于该捕获；不能换成另一份自洽的旧 candidate+risk+geo。query 屏障再验风险与捕获及已存缓存。加载也对部分/已完成风险重新对照原捕获，不调用风险模型；缺失绑定不能用当前资源补齐。snapshot hash 仍只证明一致性，不提升来源权限。

精采 receipt 是另一份证据。reservation 中初始 candidate、已存 RiskRecord 和原分数保持不变；新内容只与新 GeoRecord 校验，并要求原选中档位不变。测试明确覆盖精采新增认证字段后原引擎若重评会 discard 的情况：本轮按约定不重新评分，只沿用首次合法风险和同档地域复核，保留原成功预算。

### 先复现、后修复的证据

修复前先新增 9 个参数化反例（原文、评论、认证各覆盖编排、store、纯选择）。未修改源码时实际运行：**9 failed，78 deselected，51.62 秒，exit 1**，五项拦截计数均 0。见 `artifacts/risk-binding-repair-20261015-01/red-before-fix/checks.json` 和 `pytest.log`。这些预期失败证明旧实现未拒绝混用，不用旧 261 通过记录覆盖验收 HIGH。

修复后独立反例 `reproduce_fixed.py` / `.json` 实际显示三类混用均在首次风险落盘前以 `risk_capture_evidence_mismatch` 拒绝；完整 P3 结果仍为当前证据且通过原校验。认证案例原引擎仍为普通 weak / 当前 discard，未修改引擎。每例已存评估为空、query failed、reserved/collected/handed_off 全为 0，detail/deliver 调用为空，五项拦截计数全为 0。原始验收反例不重写、不伪装为 pytest 新增通过项。

中途四份 P4 回归为 105 passed（461.77 秒），随后 22 项返修针对性为 22 passed / 78 deselected（113.77 秒），均 exit 0、五项拦截计数全 0。两者均保留原命令与日志，且是开发中途证据；最终源码另增加 JSON 标量类型回归，最终结果以下方完整九文件验证为准。独立只读复核记录在同目录 `independent-repair-review.md`，未发现本轮 HIGH 遗漏，不代替原会话复验。

### 新鲜验证

最终完整原验收九份测试：**284 passed，2 warnings，519.10 秒，exit 0**（原 261 项加 23 项新增回归）。两项为既有依赖弃用警告。完整结果与实际命令见 `artifacts/risk-binding-repair-20261015-01/full-regression/checks.json`，日志见同目录 `pytest.log`。network/process/real_model/crawler_launch/forbidden_database 五项 attempts 全部为 0。首次评估、缓存恢复、精采同档变化、原排序及预算恢复均在本次通过范围内。

测试前锁定的 13 份源码/测试 SHA256 在测试后逐份相符；18 份 Python 文件编译及空白检查通过。1570 份原跟踪文件与 Git index 字节一致，固定输入验证 43 份 Git 文件及 11 份 P3 导出通过，原验收证据/原交付清单未变。具体结果见 `static-and-boundary-checks.json`。原 `DELIVERY_MANIFEST.json` 保留为历史基线，本次代码、REPORT 和返修证据摘要另存 `REPAIR_MANIFEST.json`，不覆盖原失败记录。

重跑命令（仍使用原九份测试与相同 OfflineGuard；仅本次 harness 将输出写到新目录并记录准确入口）：

```sh
cd /Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p4/xhs-audit-agent-demo
PYTHONPATH=backend:. /Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python experiments/geographic_selection/artifacts/risk-binding-repair-20261015-01/full-regression/rerun.py
PYTHONPATH=backend:. /Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python experiments/geographic_selection/artifacts/risk-binding-repair-20261015-01/reproduce_fixed.py
```

**P4 风险证据绑定返修完成、待原会话复验。**

P2 仍为明确标记的测试冻结输入；正式 pipeline、Run/Job、真实采集未接入，地域启动阻断继续保留。未提交、合并或部署；此状态不代表 P4 整阶段通过。
