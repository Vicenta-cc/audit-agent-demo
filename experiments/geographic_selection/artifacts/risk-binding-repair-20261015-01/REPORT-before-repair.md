# P4-offline-v1 独立交付报告

日期：2026-10-15。范围为独立选择、实验执行状态和受控离线编排。整体回归 261 项通过；本报告交付独立离线范围，正式地域流水线尚未验收。

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

**P4 并行独立选择模块、实验执行状态及离线验证完成，待原会话验收；P2实际接线/正式pipeline/Run/Job/真实采集未接入，地域启动阻断保留，P4整阶段未通过。**
