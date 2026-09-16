# 1～5 帖报告、报告问询与跨报告账号穿透专项交接（2026-09-16）

## 1. 文档用途

本文供一个新的 Codex 窗口从**只读审计**开始，继续完成以下三项工作：

1. 验证并补齐 1～5 条帖子、不同风险组合的报告生成；
2. 让同一报告中的安全帖和风险帖都能被 Qwen 正常问询；
3. 让 Report A/B/C、后续新增报告以及新增报告之间，在授权边界内通过稳定账号身份完成跨报告账号活动穿透。

本专项建立在以下总交接文档之上：

- `docs/douyin-m3-candidate-baseline-handoff-20260916.md`

新窗口必须先读总交接，再读本文。本文不授权修改正式 3198/8198，也不授权重新设计采集器、profile 或 M3 调查编排。

## 2. 当前代码基线与运行边界

### 2.1 正式冻结环境（不得修改）

- 正式提交：`2134c7b53c572b9981a0cbd9a2a281ed7ca6eb3a`
- 正式分支：`codex/unified-frozen-20260913`
- 正式标签：`unified-baseline-20260913`
- 正式代码：`/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-unified-frozen-20260913`
- 正式 runtime：`/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-formal-20260911`
- 正式前端/后端：3198/8198

正式环境只允许只读核对，不允许改文件、数据库、配置、进程或账号 profile。

### 2.2 当前候选工作树

- 工作树：`/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-douyin-ops-controls-20260916`
- 分支：`codex/douyin-ops-controls-20260916`
- 当前旧 HEAD：`a925969d78e570bbd99413dfa2e2b0d5283811da`
- 该 HEAD 不包含当前全部工作树改动。
- 当前仍有大量已跟踪修改和未跟踪文件，没有候选标签。
- 隔离 runtime：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/douyin-ops-controls-20260916`
- 隔离后端：8199，当前可能由手工 `serve.py` 启动；操作前必须重新核对。
- 隔离前端：3199，交接时未启动；浏览器旧页面不能当作服务存活证据。

本专项开始前，应先把现有“采集/分析控制与 profile 修复”改动审核并形成可复现的检查点提交。可以在检查点提交之上继续本专项，但在本专项全部通过前不要打最终候选标签。

## 3. 用户要求与术语定义

### 3.1 用户要求

需要回答并验证：

- 1～5 条审核结果能否稳定生成报告；
- 3～5 条全部安全、全部风险或安全/风险混合时能否生成正确报告；
- 报告问询能否同时查看安全帖和风险帖；
- Qwen 能否回答某条安全帖、某条风险帖及二者差异；
- Report A/B/C 与新增报告能否按同一稳定账号穿透；
- 新增报告 D 与新增报告 E 之间能否穿透；
- 同昵称但没有相同稳定身份的账号是否保持分离；
- 未授权报告是否严格不可见。

### 3.2 “所有报告”的准确含义

“所有报告”不能实现为扫描数据库中的全体报告并全部交给 Qwen。

正确含义是：

> 当前用户明确有权访问的、状态为 published 的不可变报告版本。

当前产品是本地单用户模式，principal 为 `local-user`，但仍要保留 Principal 边界，防止以后多用户化时发生跨用户泄露。

授权集合至少包括：

- 明确允许的历史 Report A/B/C；
- `owner_principal` 为当前 principal 的已发布 M3 报告；
- 当前正在问询的报告本身。

已删除、未发布、其他 principal 所有、身份校验失败或快照哈希不一致的报告必须排除。

### 3.3 “用户穿透”的准确含义

这里的“用户”是抖音发布者或评论作者账号，不是登录本系统的 Principal。

跨报告账号合并只能基于稳定平台身份，例如：

```text
platform = douyin
source_namespace = douyin.sec_uid
source_account_key = <稳定 sec_uid>
```

禁止仅凭昵称合并。相同昵称、缺少稳定 ID 或身份不一致的记录必须保持分离或标记为不可解析。

## 4. 只读审计已经确认的事实

以下结论来自当前候选工作树的只读代码核对。新窗口必须复核，若现场不一致，应先报告差异，不能直接照抄修改。

### 4.1 报告模板分流并不只是“一个安全模板＋一个危险模板”

入口：`backend/reporting/runtime.py`

当前 `R31ReportRuntime.generation_graph()` 的实际分流为：

```text
全部帖子 decision=pass 且 risk_level=none
→ PassReportGraph（all_pass）

否则，帖子总数恰好为 1
→ SinglePostReportGraph（single_risk_post）

否则
→ AccountOverviewReportGraph（通用多帖风险报告）
```

因此：

- 全安全报告本来就支持多帖；
- 单条非安全结果走单帖风险模板；
- 2～5 条且至少有一条风险时走通用 R3.1 风险报告图。

### 4.2 全安全 5 条已有自动化证据

文件：`tests/test_pass_report.py`

现有测试参数覆盖 `count=1` 和 `count=5`，验证：

- 不调用 Qwen；
- 报告成功 published；
- 决策统计正确；
- 报告正文最多展示前 3 条；
- 全部帖子仍可从附录和详情读取；
- 本地路径/provider 信息不泄露。

`backend/reporting/pass_graph.py` 中 `SAMPLE_LIMIT = 3` 只限制正文样本展示，不应限制报告包含的帖子总数。

### 4.3 单帖风险模板被明确限制为恰好 1 条

文件：`backend/reporting/single_post_graph.py`

`SinglePostReportGraph._validate_snapshot()` 明确要求：

```text
len(snapshot.posts) == 1
len(snapshot.findings) == 1
```

这是合理的模板边界，不应为了支持 3～5 条而删除。多帖风险应走通用报告图。

### 4.4 多帖混合风险只有局部测试，没有完整闭环证据

文件：`tests/test_report_risk_inputs.py`

现有测试覆盖：

- 1 条 reject；
- review + reject；
- pass + review + reject。

它证明安全帖不会被错误当成风险输入，风险帖能够进入聚类规划且不能被漏掉；但它只测试风险输入和规划节点，没有证明 3～5 条混合结果可以完成以下闭环：

```text
冻结快照
→ Qwen 规划/写作
→ 校验
→ published
→ 前端投影
→ 帖子详情/附录
→ 真实报告问询
```

这正是本专项第一个待补缺口。

### 4.5 当前报告问询确实按风险类型分裂

相关文件：

- `backend/hermes_runtime/service.py`
- `hermes_m0/pass_support.py`
- `hermes_m0/report_task_service.py`

当前 `_product_mode()`：

```text
template_kind == all_pass → pass-report
其他报告                 → account-activity / 风险报告工具
```

全安全报告的 `PassReportToolService`：

- `read_report` 返回所有冻结帖子的预览（当前上限 20）；
- `search_posts` 可在全安全快照中工作；
- `read_posts` 可读取选中安全帖。

风险/混合报告的常规 `ReportTaskInvestigationToolService`：

- `read_report` 主要返回 InvestigationFinding 和 standalone risk post 预览；
- `search_posts` 明确只返回确定性的风险候选；
- 工具响应明确声明 `deterministically safe pass/none posts` 未加载。

因此对一个“4 条安全＋1 条风险”的报告，Qwen 当前可能只能主动发现风险帖，无法正常围绕安全帖回答。这不是用户误解，而是真实能力缺口。

### 4.6 当前跨报告授权主要写死为 A/B/C

相关文件：

- `backend/historical_reports/catalog.py`
- `backend/main.py`
- `backend/hermes_runtime/service.py`

`backend/main.py` 构造 `HermesInvestigationAgentService` 时，将 `HISTORICAL_REPORT_SPECS` 中的 A/B/C 版本 ID 作为 `authorized_report_version_ids`。

`_authorized_report_contexts()` 当前行为：

- 历史报告 anchor 可以使用明确授权的 A/B/C；
- 未知历史导入不会自动获得跨报告上下文；
- M3 的 all-pass 报告通过 `pass_report_anchor` 特例可复用 A/B/C；
- M3 风险/混合报告不是 `pass-report`，通常不会获得 A/B/C 额外上下文；
- 新发布报告不会自动加入静态 A/B/C 集合；
- 新报告 D 与新报告 E 不会自然互相授权。

因此当前确实只有 A/B/C 的历史路径较完整，新增报告与 A/B/C、新增报告之间的穿透没有完整打通。

### 4.7 底层账号语料已经具备合并多个授权报告的能力

相关文件：

- `hermes_m0/runtime.py`
- `hermes_m0/pass_support.py`
- `hermes_m0/account_activity_repository.py`

`configure_real_report_runtime()` 已支持：

```text
当前报告 repository
+ additional_account_report_sources
→ SnapshotAccountRepository / AccountActivityRepository
→ 合并成当前授权账号活动语料
```

现有测试 `test_pass_account_scope_merges_only_explicitly_authorized_snapshots` 证明：

- 同一稳定账号可聚合两个明确授权任务中的 occurrence；
- 未授权任务不会进入语料；
- 当前报告自己的统计不会被跨报告总量替换。

所以本专项不需要重写账号穿透引擎。主要缺口是：

1. 如何动态、正确地构建当前 principal 的授权报告集合；
2. 如何让所有报告类型都使用该集合；
3. 如何在报告问询中暴露当前报告的安全帖。

### 4.8 已有 Principal 和报告所有权依据，但报告会话尚未完整使用

相关文件：

- `backend/investigation_creation/principal.py`
- `backend/investigation_creation/store.py`
- `backend/investigation/store.py`
- `backend/api/reporting.py`

当前已有：

- 本地 principal：`local-user`；
- `investigation_runs.owner_principal`；
- `owner_principals_for_report(report_version_id, task_id)`；
- API 中的 `can_read_m3_report()` 所有权判断。

但普通 report 类型的 `investigation_sessions.owner_principal` 当前写入空字符串，`create_session()` 也没有 principal 参数。新窗口必须审计这一点，不要直接“列出全部 published 报告”。

最低安全要求是：通过当前 M3 Run/报告所有权得到 principal 授权集合；历史 A/B/C继续使用显式授权目录。是否把 principal 写入 report session，应在审计后选择最小方案，并补迁移/兼容测试。

## 5. 本专项的最小设计目标

### 5.1 保留三种报告路径

不要把三个报告图合并成一个大模板：

- 全安全：继续使用确定性 `PassReportGraph`；
- 单条风险：继续使用 `SinglePostReportGraph`；
- 多帖且含风险：继续使用 `AccountOverviewReportGraph`。

本专项只补测试与问询/授权缺口，不重做报告架构。

### 5.2 当前报告的全部冻结帖子都可问询

对于 1～5 条的小量报告，最小可行方式应优先考虑：

- 风险/混合报告的 `read_report` 同时返回一组**全部冻结帖子预览**；
- 每个预览明确包含 decision、risk level 和安全/风险属性；
- 这些帖子引用可继续交给已有 `read_posts` 读取完整详情；
- 现有风险 Finding、standalone risk post、风险评论工具继续保留；
- 现有 `search_posts` 若继续定义为“风险候选搜索”，就不要悄悄改变其语义。

这样可以在不重写工具体系的情况下，让 Qwen 从报告概览中看到 1～5 条全部帖子，并进一步读取安全帖或风险帖。

如果审计证明概览预览不足，再考虑新增通用分页工具；不要一开始就扩展成新的检索系统。

### 5.3 动态授权目录只包含当前 principal 可访问报告

建议的授权来源规则：

```text
显式历史报告 A/B/C
+ 当前 principal 的 investigation_runs 中 status=PUBLISHED 且 report_version_id 非空的版本
+ 当前会话绑定报告
```

每个候选版本还必须通过：

- `report_versions.status == published`；
- ReportVersion、Report、Snapshot、content hash、snapshot hash 可验证；
- 没有被删除；
- 当前任务唯一，不与另一个版本伪装成同一来源任务；
- 当前 principal 有读取权限。

不能按昵称、任务标题、前端列表或文件路径推断授权。

### 5.4 授权集合更新语义必须明确

不能在一个正在执行的 Qwen turn 中途改变授权语料。

最小安全策略可以是：

- 每次创建或重新打开报告问询 Session 时冻结授权目录；
- 新报告发布后，用户刷新/重新打开问询，使 Session 重新绑定最新目录；
- 或者只在 turn 之间检测授权目录 revision，安全释放并重绑 runtime；
- 已签发的引用不能悄悄指向不同报告或不同对象。

新窗口必须在实现前明确选择一种，并写测试。不要让进程缓存导致 D 报告发布后 E 永远不可见，也不要在 turn 中热替换破坏引用安全。

## 6. 非目标与禁止事项

- 不修改正式 3198/8198、正式数据库或正式 runtime。
- 不修改抖音采集器、账号轮换、CloakBrowser profile/seed 或登录流程。
- 不恢复页面 DOM 搜索实验。
- 不把三套报告图推倒重写或强行合并。
- 不让 Qwen自行判断账号是否是同一个人；身份合并必须由确定性稳定 ID 完成。
- 不用昵称进行跨报告账号合并。
- 不向 Qwen暴露未授权、未发布或已删除报告。
- 不修改已发布报告内容；所有 published ReportVersion 保持不可变。
- 不把报告自己的统计替换成跨报告账号总量。
- 不因为本专项去拆分后端服务。
- 不使用 `/tasks` 页面作为产品验收入口。
- 不在没有合成/本地回归通过前进行真实 Qwen或真实采集试验。

## 7. 新窗口必须采用的分阶段工作方法

### 阶段 A：只读审计

第一阶段禁止修改文件、提交、打标签、重启服务或调用真实 Qwen。

#### A1. 核对现场

执行只读检查：

```text
git status --short
git branch --show-current
git rev-parse HEAD
git diff --stat
3198/8198/3199/8199 监听和进程工作目录
正式与隔离 runtime 身份接口
```

确认工作树是否已经形成采集控制阶段检查点提交。如果没有，不要把旧 HEAD 当作当前成果。

#### A2. 阅读必需文档

至少完整阅读：

- `docs/douyin-m3-candidate-baseline-handoff-20260916.md`
- 本文
- `docs/m3-execution-parity-20260916.md`
- `docs/M3_REPORT_COMPLETION_20260909.md`
- `docs/m1-finding-comment-provenance-adaptation.md`
- `docs/runtime/formal-entry-integration-progress.md`

#### A3. 追踪四条代码链路

1. 报告模板选择：
   - `backend/reporting/runtime.py`
   - `backend/reporting/pass_graph.py`
   - `backend/reporting/single_post_graph.py`
   - `backend/reporting/r31_graph.py`

2. 报告问询工具：
   - `backend/hermes_runtime/service.py`
   - `hermes_m0/runtime.py`
   - `hermes_m0/pass_support.py`
   - `hermes_m0/report_task_service.py`
   - `hermes_m0/schemas.py`

3. 授权报告目录：
   - `backend/historical_reports/catalog.py`
   - `backend/main.py`
   - `backend/investigation_creation/store.py`
   - `backend/investigation/store.py`
   - `backend/api/reporting.py`
   - `backend/api/investigation.py`

4. 账号身份与 occurrence：
   - `hermes_m0/pass_support.py`
   - `hermes_m0/account_activity_repository.py`
   - `hermes_m0/account_activity_service.py`
   - `backend/reporting/account_overview.py`
   - `backend/reporting/account_entries.py`

#### A4. 阅读现有测试

至少核对：

- `tests/test_pass_report.py`
- `tests/test_pass_investigation.py`
- `tests/test_report_risk_inputs.py`
- `tests/test_r31_presentation_projection.py`
- `tests/test_r02_runtime_closure.py`
- `tests/test_investigation_creation_m3.py`
- `tests/test_reference_state.py`

#### A5. 审计输出

在修改前先向用户报告：

- 当前三种报告分流是否与本文一致；
- 3～5 条完整生成缺少哪些测试；
- 安全帖在哪个工具边界被排除；
- A/B/C 与新增报告在哪个授权判断中断开；
- 当前 principal 可以从哪里可靠取得；
- 最小拟改文件列表；
- 不修改哪些模块；
- 预期风险和回滚方式。

如果审计与本文不一致，停在审计阶段说明差异，不要直接实施。

### 阶段 B：先建立失败测试与验收夹具

不得先改生产逻辑再补测试。

#### B1. 报告生成矩阵

用隔离 SQLite 和确定性夹具构造：

| 数量 | 组合 | 预期模板/路径 |
| ---: | --- | --- |
| 1 | 全安全 | `all_pass` |
| 1 | 单条风险 | `single_risk_post` |
| 3 | 全安全 | `all_pass` |
| 5 | 全安全 | `all_pass` |
| 3 | 2 安全＋1 风险 | 通用 R3.1 |
| 5 | 4 安全＋1 风险 | 通用 R3.1 |
| 5 | 2 安全＋3 风险 | 通用 R3.1 |
| 3/5 | 全风险 | 通用 R3.1 |

每个用例至少断言：

- 快照帖子数和审核结果数一致；
- 报告成功 published；
- 决策/风险统计正确；
- 每个风险帖进入 Finding 或 standalone risk post；
- 每个安全帖保留在报告快照、附录和帖子详情中；
- 不重复、不漏帖；
- 已发布版本不可变；
- 重开数据库后仍可读取。

#### B2. 当前报告问询夹具

构造“4 安全＋1 风险”报告，先证明现状：

- `read_report` 无法给风险模式 Qwen提供所有安全帖引用；
- 风险搜索只返回风险候选；
- 无法通过已有引用读取某条安全帖。

然后把预期测试写成：

- `read_report` 返回全部 5 条冻结帖子预览；
- 风险属性明确且统计一致；
- 安全帖引用可被 `read_posts` 读取；
- 风险帖仍可进入 Finding/风险评论工具；
- Qwen不能把安全帖说成风险帖，也不能把未审核帖说成安全帖。

#### B3. 跨报告账号夹具

至少构造：

- Report A：账号 X 发布 1 条；
- Report D：同一 `sec_uid=X` 再发布 1 条；
- Report E：同一 X 评论另一账号帖子；
- Report F：昵称与 X 相同，但 `sec_uid=Y`；
- Report U：属于另一个 principal 或未授权；
- Report P：尚未 published；
- Report Z：已删除。

断言：

- A/D/E 中 X 的 occurrence 可以聚合并分别回到原报告/帖子；
- F 不与 X 合并；
- U/P/Z 不进入授权语料；
- 当前报告统计保持本地范围；
- 跨报告账号 overview 明确标示授权来源范围；
- 删除或撤销授权后重新打开 Session 不再暴露对应来源。

### 阶段 C：最小实现报告内全帖问询

优先小改现有风险报告 `read_report`：

1. 从当前 immutable snapshot 构造全部冻结帖子预览；
2. 给每条预览签发当前 Session 下的 post ref；
3. 展示标题、作者、decision、risk level 和必要边界；
4. 允许既有 `read_posts` 读取这些 ref；
5. 保留风险 Finding、standalone risk post 和风险评论能力；
6. 保持 `search_posts` 的风险候选契约不变，除非审计证明必须扩展；
7. 更新 prompt，明确安全结论仅适用于已完成审核的当前冻结帖子。

先让 B2 测试通过，再运行所有报告工具和引用状态回归。

### 阶段 D：最小实现动态授权报告目录

实现前先选择 principal 的权威来源。优先复用：

- `investigation_runs.owner_principal`；
- `owner_principals_for_report()`；
- 当前 `m3-run:<run_id>` anchor；
- API 已有 `can_read_m3_report()` 规则；
- 历史 A/B/C 的显式授权目录。

不要新建一套彼此不一致的权限判断。

实现目标：

1. 为当前报告会话得到明确 principal；
2. 列出当前 principal 的 published M3 ReportVersion；
3. 合并显式历史 A/B/C；
4. 排除当前版本重复项、已删除项、未发布项和其他 principal 报告；
5. 将验证后的不可变上下文交给已有 `additional_report_contexts`；
6. 让 all-pass、single-risk 和 multi-risk 三种报告使用相同授权目录；
7. 定义目录 revision 和 Session 重绑策略；
8. 保证 task ID 唯一和报告 hash 校验仍然有效。

先让 B3 测试通过，再运行历史 A/B/C、删除、Session 恢复和引用状态回归。

### 阶段 E：3～5 条完整报告生成回归

在不访问外部平台的隔离数据上运行 B1 全矩阵。

对于需要 Qwen的多帖风险报告：

1. 先用受控 fake provider 验证完整图的所有节点和严格结构校验；
2. 再进行一次真实 Qwen 3 条混合报告；
3. 通过后再进行一次真实 Qwen 5 条混合报告；
4. 不用真实 Qwen反复轰炸同一失败输入；
5. 保留脱敏后的 provider receipt、报告版本、快照哈希和结果摘要。

全安全报告必须继续保持确定性生成，不应调用 Qwen。

### 阶段 F：真实报告问询验收

至少准备：

- 一份 3～5 条混合报告 D；
- 一份后续新增报告 E；
- 历史 A/B/C 可用归档。

对 D 的真实问询：

1. “这份报告一共有几条，哪些安全、哪些有风险？”
2. “打开第 2 条安全帖，说明为什么判定安全。”
3. “打开风险帖，列出审核结论与直接依据。”
4. “比较一条安全帖和一条风险帖，不要扩大结论范围。”
5. “哪些评论尚未完成审核？”

跨报告问询：

1. “这个发布账号还出现在哪些已授权报告？”
2. “它在 A/B/C 和新增报告 D 中分别发布或评论了什么？”
3. “新增报告 D 与 E 是否存在共同发布者或评论者？”
4. “同昵称但稳定 ID 不同的两个账号是否被错误合并？”
5. “未授权报告 U 中是否存在该账号？”——正确结果必须是不访问、不推断，而不是泄露 U 的存在或内容。

每个回答都要核对实际 tool trace，不能只凭自然语言看起来合理。

### 阶段 G：主界面验收

在 3199/8199 隔离环境中完成：

- 3～5 条报告正常展示；
- 安全帖、风险帖、附录和帖子详情均可打开；
- 报告底部问询可以回答上述问题；
- A/B/C 与新增报告、新增报告之间的账号入口可以跳转或返回可验证 occurrence；
- 刷新/重开后授权目录和引用仍正确；
- 报告删除后重新打开不再暴露已删除报告；
- 正式 3198/8198 不受影响。

本专项不需要为了生成 3～5 条数据高频抓取。优先使用已完成审核的隔离夹具；确需实采时仍只使用“维汉夫妻”、低频、小量、已验证 profile。

### 阶段 H：最终门禁与铆钉

至少运行：

- 新增的多帖报告矩阵测试；
- 报告问询工具与 schema 测试；
- 跨报告账号语料与授权测试；
- A/B/C 历史报告回归；
- report Session、reference state、删除与恢复测试；
- M3 报告生成/绑定/恢复测试；
- 前端 TypeScript 测试与 Vite build；
- 全量后端测试并对非本轮失败逐项分类。

最终提交前检查：

- 没有真实 Cookie、二维码、账号 profile、密钥、provider 原始敏感响应；
- 没有隔离数据库、JSONL、日志或生成媒体误入 Git；
- 没有硬编码新增报告 ID；
- 没有把 `local-user` 当作绕过授权的万能开关；
- 没有修改 published 报告；
- 没有影响采集/profile 代码；
- `git diff --check` 通过。

全部通过后再形成最终候选提交和标签，并更新总交接文档。

## 8. 完成定义

只有同时满足以下条件，才能宣称本专项完成：

- [ ] 1、3、5 条全安全报告通过。
- [ ] 1 条风险报告通过。
- [ ] 3、5 条安全/风险混合报告完整 published。
- [ ] 3、5 条全风险报告完整 published。
- [ ] 所有完成审核的帖子都进入快照、附录和详情，无遗漏、无重复。
- [ ] 风险帖全部进入 Finding 或 standalone risk post。
- [ ] 混合报告问询能访问安全帖和风险帖。
- [ ] Qwen能回答安全/风险差异并保持审核边界。
- [ ] A/B/C 与新增报告之间可按稳定账号 ID 穿透。
- [ ] 新增报告之间可按稳定账号 ID 穿透。
- [ ] 同昵称不同 ID 不合并。
- [ ] 未授权、未发布、已删除报告不可见。
- [ ] 当前报告统计不被跨报告总量污染。
- [ ] 重开 Session 后授权目录与引用状态正确。
- [ ] 主界面 3199 完成真实报告展示和问询验收。
- [ ] 正式 3198/8198 全程未修改。

## 9. 建议新窗口首次回复的格式

新窗口完成只读审计后，应先用下面结构回复用户，而不是立即宣布开始改代码：

```text
1. 当前基线与工作树状态
2. 报告模板真实分流
3. 3～5 条报告已有与缺失证据
4. 安全帖问询被阻断的具体代码位置
5. A/B/C、新报告之间授权断点
6. principal 与账号稳定身份边界
7. 最小修改文件清单
8. 测试先行计划
9. 不会触碰的正式环境与非目标
10. 是否发现与交接文档不一致的事实
```

## 10. 可直接粘贴给新窗口的启动指令

```text
请在以下候选工作树继续工作：
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-douyin-ops-controls-20260916

先完整阅读：
1. docs/douyin-m3-candidate-baseline-handoff-20260916.md
2. docs/report-multipost-qa-account-penetration-handoff-20260916.md

本任务目标是：在不过度重构的前提下，验证并补齐 1～5 条报告生成、混合安全/风险帖问询，以及 Report A/B/C、新增报告和新增报告之间基于稳定账号身份的授权穿透。

第一阶段只做审计：不得修改文件、提交、打标签、重启服务、调用真实 Qwen或发起真实采集。请核对 Git 现场、报告模板分流、报告工具范围、授权目录、Principal 来源、账号稳定身份和现有测试，并先向我给出审计结论、最小修改面及测试计划。

审计必须特别确认：
- 全安全多帖是否已有 1/5 条证据；
- 多帖混合风险是否只有局部规划测试；
- 风险报告的 search/read 工具是否排除了安全帖；
- A/B/C 是否由静态 HISTORICAL_REPORT_SPECS 授权；
- 新 M3 风险报告和新增报告之间为何不能穿透；
- 如何只纳入当前 principal 有权读取的 published 报告；
- 为什么不能按昵称合并账号。

实施时必须测试先行，并按文档阶段 B 到 H 推进。保留 all-pass、single-risk、multi-risk 三种报告路径；优先通过风险报告 read_report 暴露当前冻结快照的全部 1～5 条帖子，复用现有 read_posts；不要一开始新建检索系统。动态授权必须复用 investigation_runs.owner_principal、owner_principals_for_report、已有 API 读取规则和 A/B/C 显式授权，不得扫描并暴露全部数据库报告。

正式 3198/8198、正式 runtime、抖音采集器、CloakBrowser profile/seed、账号轮换和 DOM 搜索均不在本任务修改范围。所有改动和测试只在候选工作树及 3199/8199 隔离环境进行。遇到与交接事实不一致、需要扩大架构或可能影响正式数据的情况，先停下来报告。
```
