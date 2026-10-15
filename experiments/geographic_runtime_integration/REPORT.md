# 地域正式运行路径接入实施报告

2026-10-15。状态：**本轮接口接入及离线返修完成，待原会话验收。真实前端全流程尚未验证，默认地域启动阻断保留。**

这不是 P4 整阶段通过或正式上线结论。P4 风险证据绑定返修原会话明确复验记录仍缺失，实施者自测不能替代。原计划 R0 写明：“可先写契约及离线测试；正式测试启动前关闭该项”。本轮完成可安全推进的本地代码、离线测试和报告，未绕过该前置条件启动 R6。

## 基线和修改范围

- worktree：`/Users/ext.wanghongtao6/.codex/worktrees/geographic-integration/xhs-audit-agent-demo`，分支 `codex/geographic-integration`，HEAD `9b36fbc2b5b3341bf7eb88bc8495ba08e8b4c363`。
- 应用 main 引用 `a4997d3969beab3d499a218e7a7aee025fea46d9`，离线检查点 `9b20f342dddbf6172596b2b440d3e19559a766a3`；不是云服务器部署版本证明。
- 最终 P1 `031e0cd26379ee44c2372c9a28536c377ab3d4b9`；P3 固定 tag `p3-post-only-names-v2-local-20261015` → `2120c53379d1b79c45e3cb8232b9c9b96657b183`。
- 用户后续授权必要的 P1/P2/P3 非语义接口适配；重点约束为旧精选风险筛选逻辑不变。固定 P3 原样导出没有修改；新增显式运行时来源契约，不将真实任务伪装为 fixture。
- 未提交、合并、推送、部署；没有改动运行中的 P1 worktree、服务或数据库。没有调用真实模型、采集、迁移账号或打开前端进行真实业务操作。

## 交付内容

| 范围 | 实施结果 |
|---|---|
| R1 冻结与身份 | 服务端权威确认、地域 v5 Run、Job 传递、可信摘要/运行身份绑定；旧无地域序列化保留 |
| R2 捕获与判断 | 正式桥接、全词候选屏障、原风险引擎与排序、固定 P3 语义、正式状态存储 |
| R3 精采与恢复 | 复用 P4 预算/选择；精采后只做地域复核；持久交付账本、本次原始证据专属文件、恢复校验、完成闸门 |
| R4 接口与展示 | 按 Run 授权的分页筛选状态；冻结报告地域依据/覆盖统计；前端状态与报告展示；原风险统计不变 |
| R5 离线验证 | 真实确认/Run/Job/Worker/pipeline/ingestion/审核/报告发布代码运行；模型与采集使用明确测试响应；导入前副作用守卫 |
| R5 真实测试准备 | 可选独立模型预算网关，默认30次，跨进程原子扣额、失败不退、重启不清零；没有接入真实环境 |
| R6 | 未执行；真实账号、专用网络出口、所有模型角色路由审计及前端克拉玛依正向流程均待验证 |

详细接口见 [INTERFACE_MAPPING.md](./INTERFACE_MAPPING.md)，逐文件摘要与受保护源码差分见 [SOURCE_MANIFEST.json](./SOURCE_MANIFEST.json)，25 场景逐例证据与不足见 [CASE_EVIDENCE.md](./CASE_EVIDENCE.md)。

没有新增依赖、另造风险评分器或重写精选算法。将冻结风险适配放入 backend，正式代码不依赖实验 harness；原 P4 状态转换共用，实验入口限制保留。

## 风险逻辑与证据边界

`triage.py`、`triage_matcher.py`、`crawler_adapter.py` 与最终 P1/整合 HEAD 的完整字节相同；`pipeline._run_triaged_search` AST 相同。这不表示整个 pipeline 未变：正式分派、运行身份、交付及终态属于本轮接入修改。

同 execution/query/content/rank 的旧 weak 记录不能用于原文、评论或认证信息已经变化的首次捕获。首次风险记录写入、query 完成屏障及缓存恢复校验不可变 raw_source 与规范化投影；相关反例保留在原测试文件中。精采证据单独存储，允许同档内容变化，保留首次 RiskRecord/排序，不重新风险评分。正式审核仍读取本次获准交付的精采原文。

本轮另发现并处理账号二次认证失败可能进入外层旧轮换的问题；红/绿记录保留。最后独立复核发现同 crawl epoch 更换 Run claim 后旧 worker 回写的问题，已补短事务执行权保护：Job 状态、审核落盘、结果文件与分析状态提交在持有 Run claim 写锁时执行。先复现6项失败，10项租约测试加 Worker/pipeline 共16项定向通过；日志保留于 `artifacts/runtime-store-delivery-worker/geographic-lease-red.log` 和 `geographic-lease-final.log`。这不宣称每条诊断日志或所有正式多进程竞争情形均已覆盖。

## 新鲜测试结果

最终统一回归：**218 passed / 13 skipped / 0 failed，231 collected**，pytest 172.11秒，进程退出码0。运行前后源码摘要一致，守卫异常为0，真实网络/模型/采集/派发成功为0。见 [最终 summary](./artifacts/runtime-final-20261015-01/summary.json)、[逐项 JUnit](./artifacts/runtime-final-20261015-01/junit.xml)、[完整命令](./artifacts/runtime-final-20261015-01/commands.json)、[源码前摘要](./artifacts/runtime-final-20261015-01/source-before.json)与[后摘要](./artifacts/runtime-final-20261015-01/source-after.json)。包含新地域冻结/桥接/存储/交付/Worker/租约/报告/预算，以及旧精选搜索、账号轮换、冻结配置和报告回归。

13项跳过全部来自 `R31PresentationProjectionRealReportTest`，缺少 `R31_REAL_REPORT_A_DB` 和 `R31_REAL_REPORT_B_DB`；不是新增地域用例跳过。2项为依赖弃用警告。以下分轮结果不相加冒充统一验收：

- 原风险/P4 原验收范围（最终租约修复前单独执行，相关旧精选/账号轮换已纳入最终统一重跑）：**284 passed**，417.00秒；[summary](../geographic_joint_integration/artifacts/runtime-original-p4-01/summary.json)，无跳过/副作用守卫异常。
- 网关：**18 passed**；[summary](./artifacts/runtime-gateway-green-01/summary.json)，跨独立子进程共享额度；无真实网络请求，运行前后源码相同。
- 独立进程交付恢复：**1 passed**；[summary](./artifacts/runtime-replay-green-01/summary.json)，继承离线守卫，恢复仅读取本次证据。
- 前端逻辑：**31 passed**（含8项新增展示测试）；typecheck 无诊断，build 成功，保留大 chunk 警告。日志见 [frontend-validation](./artifacts/frontend-validation/logic.log)。这不是浏览器真实业务流程测试。

## 失败与历史记录保留

1. 旧冻结 offline worktree preflight：344 passed / 1 failed，1067.77秒；失败节点 `test_persisted_plan_to_pinned_p3_request_and_manual_result`，子进程 replay 非零退出。日志没有保留完整子进程 stderr，不擅自断言原因或算通过。产物路径：`/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p234-offline/xhs-audit-agent-demo/experiments/geographic_joint_integration/artifacts/runtime-preflight-20261015-01`。
2. 当前源码跑旧联合固定清单：70 collected，1 passed / 56 failed / 13 errors；原因包括 `imported_source_drift:backend/investigation_creation/scope_search_plan.py`。旧清单固定旧输入源码，新增正式契约后拒绝。没有改写旧清单以制造通过；[原日志](../geographic_joint_integration/artifacts/runtime-joint-final-01/pytest.log)。因此不能宣称原联合范围在当前源码全部通过。
3. 初次统一启动文件名错误，随后旧 runner 运行得到112 passed / 1 failed / 13 skipped；独立交付恢复的 `python -c` 被子进程守卫拒绝。[summary](../geographic_joint_integration/artifacts/runtime-formal-final-02/summary.json)。改为固定 replay 模块并让子进程继承相同守卫，新 runner 仅允许两个明确测试模块，不允许任意进程。
4. 新 runner 首次正式路径测试126 collected：113 passed / 13 skipped / 0 failed，但运行时三个测试工具文件改变，`source_unchanged=false`、wrapper exit=1；不作为最终通过。变化为网关测试、网关固定回放模块及新 runner，见 `artifacts/runtime-formal-green-01`。
5. 所有先红后绿、测试响应修正及历史 P2 四项基线失败不删除、不重写、不通过 skip 隐藏。旧报告保持原状。

## 未验证项与运行限制

- P4 原会话返修复验与源码绑定未补齐；作者离线通过不构成该批准。
- 没有运行真实前端→模型→账号采集→地域精选→审核→报告，不能答复用户“已经全流程可用”。已有 Worker 测试只证明受控外部响应下业务路径。
- 全部正式 Worker 故障点的独立进程恢复、真实并发 worker 抢占、多个真实 source_job 聚合及多轮 scope 发布隔离，仍不能从局部测试外推全覆盖。
- 真实账号状态、MediaCrawler 外部工作区版本、登录、代理、专用香港出口未验证。保持原 P1 环境不动。
- 模型网关仅支持 chat/completions、SSE 缓冲返回，默认 transport 不读取代理；所有模型角色/子进程统一路由配置尚未审计。预算工具通过不代表真实调用全面受控。
- R3.1 真实历史报告 DB 测试因没有提供隔离副本环境变量而跳过；没有读取正式数据库补足它们。

## 回退与交接

改动未提交，默认能力关闭。需回退时按源码清单审阅并逐项撤销本轮差异，不能 reset 或删除他人工作。隔离测试临时库由 runner 清理，日志与失败证据保留。没有启动新服务，无需停止或重启现有 P1 服务。

原会话验收后才继续 R6：固定验收源码，补真实环境配置/路由审计，建立独立数据库与端口，验证账号，再有限额地从前端执行“调查克拉玛依大火”。不根据本报告自行宣布可上线。

## 最终静态检查与交接状态

50份本轮变更/新增 Python 文件 AST 解析通过，`git diff --check` 无错误；前端 typecheck/build 如上。仓库没有独立 lint 脚本，不虚报 lint。证据见 [static-checks.json](./artifacts/final-verification/static-checks.json)。

源码清单、接口映射和逐例证据已交付，原失败记录保留。本报告不替代 P4 原会话返修复验，不宣布 P4 整阶段通过、真实链路打通或可以上线。

## 本地交接版本保存

本报告、接入源码、测试和交接资料一并保存为本地Git提交，未推送、部署或解除地域启动阻断。上文“未提交”描述的是实施完成时的历史状态。准确提交可通过本报告的Git历史取得；外部交接目录另记录完整commit ID。提交不改变测试源码或验收结论。可复制的接续提示见 [NEW_WINDOW_PROMPT.txt](./handoff/NEW_WINDOW_PROMPT.txt)，环境与验证步骤见 [ENVIRONMENT_AND_VALIDATION.md](./handoff/ENVIRONMENT_AND_VALIDATION.md)。

提交前全量暂存检查补充：63份变更源码在Git index中逐字节匹配已测试清单。暂存diff空白检查提示新文件geographic_runtime.py末尾空行和原始测试日志/XML的尾随空格；这些字节保持原样，未为清除格式提示重写验收源码或失败证据。上文较早git diff --check只覆盖当时已跟踪差异，不能视为完整暂存集合无警告。未将私有配置值、账号库或运行数据库纳入提交。
