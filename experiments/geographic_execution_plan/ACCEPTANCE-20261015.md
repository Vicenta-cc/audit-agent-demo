# P2 离线交付独立验收（2026-10-15）

历史记录：本文件结论对应修复前交付，原 REPORT 已[按原字节归档](artifacts/pre-fix-report-20261015.md)。用户随后授权修复；修复内容、独立复审与当前证据见 [FIX-REVIEW-20261015.md](FIX-REVIEW-20261015.md) 和 [REPORT.md](REPORT.md)。下文保留当时发现与不通过结论。

**结论：REQUEST CHANGES，当前不通过。** code-reviewer 为 REQUEST CHANGES；独立 architect 为 BLOCK。发现 HIGH 1 项、MEDIUM 1 项，CRITICAL/LOW 0 项。四项 P1 既有失败本身不阻断本轮，实际阻断来自下面两项 P2 缺陷。未修改交付源码、原测试或原 REPORT，未进入修复或正式接入。

## 验收对象与固定输入

- 工作树：`/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p2/xhs-audit-agent-demo`，分支 `codex/geographic-relevance-p2`。
- 新版方案 SHA256：`c604bb4d4d01eb2f7ff9ce617f54619e0abb3acd4cd0c54d933923e6f3860868`。
- 被审 [REPORT.md](REPORT.md) SHA256：`2807ad0e42c36d6821a4fe4d8ec1fc41664427b7eb97108b76c9cfd52e29a529`。本验收结论覆盖其“待验收”状态；不将交付声明当成验收证据。
- 两路独立审查各读取 19 个交付源码、测试及文档文件，并核对相关 P1 调用与契约。代码审查另核对 11 个 P3 导出依赖及 manifest；主审负责重新执行完整离线测试、精确 P1 基线复现和独立 Git 对象核验。

| 重点 | 验收结果与证据 |
|---|---|
| P1 基线 | HEAD 与 `checkpoint/geographic-relevance-p0p1-final-20261015` 均为 `031e0cd26379ee44c2372c9a28536c377ab3d4b9`，tree 与输入清单一致。独立从 Git 对象读取全部 1570 个原文件，逐字节比较工作树和原摘要清单，均无变化。 |
| P3 基线 | `p3-post-only-names-v2-local-20261015` 解引用为 `2120c53379d1b79c45e3cb8232b9c9b96657b183`。11 个实际导出文件逐字节等于固定 Git 对象；37 项固定输入摘要全部核实。 |
| 修改边界 | 仅新增允许的纯计划文件、测试前缀及实验目录。原前端、词库/规则工具、资源/service/store/contracts/adapters、triage/matcher/pipeline/crawler 未改；产品无新计划或实验 import；地域启动阻断仍保留。 |
| 搜索词原样 | 真实 P1 临时词库测试覆盖 `克拉玛依市包过`、`包过`、`北京路欠薪`、`克拉玛依市 包过` 的原词/空格/顺序，切换 scope 后不变；重复 query 保留全部来源并只执行一次；未预设帖子归最早搜索词。不过新参数范围有下面 MEDIUM 缺陷。 |
| 风险结果不变 | **不满足当前验收要求。** 手写风险 fixture 的请求、命中、排除、分数和排序测试通过，但真实捕获计划存在规则来源错配；实际模型请求已经不相等。 |
| 阶段边界 | fixture-only、独立保存恢复、指定 names-v2 人工请求/响应消费符合本轮方向；正式确认、Run/Job、P4 选择/额度、执行进度恢复与语义质量明确延期，不作为本轮缺陷，也不宣称完成。 |

独立输入与禁区证据：[acceptance-input-audit](artifacts/acceptance-input-audit-20261015-01.json)、[source audit](artifacts/acceptance-source-audit-20261015-01.json)。

## HIGH：冻结了错误的风险提示输入

位置：[authority.py:187](authority.py#L187)；相关不变量 [scope_search_plan.py:382](../../backend/investigation_creation/scope_search_plan.py#L382)。

原 P1 的 `pipeline.py:2127–2128` 从 `request.prompt_profile_snapshot` 生成风险 `rules`，在 `2232–2233` 传给原 `TriageEngine.score()`。`triage.py:135–144` 读取 `audit_goal/evidence_rules/fusion_rules` 形成提示文字。P2 却将 `execution.rule_snapshot` 保存为 `risk_inputs.rules`，并要求它等于这个错误来源。

使用实际保存的 `example-fixture-plan.json`，保持帖子、搜索词、rank、评论、词库与参数一致，调用原引擎及固定人工客户端：

| 项目 | 原流程的规则来源 | 当前 P2 冻结来源 |
|---|---:|---:|
| 审核规则文字 | 91 字 | 0 字 |
| 完整风险请求 | 862 字 | 771 字 |
| 固定人工响应下分数 | 200 | 200 |

完整请求摘要不同，`requests_equal=false`。分数相同来自固定人工回答，不能抵消请求内容已经变化这一事实；没有调用真实模型，未证明真实分数一定改变。

现有 `test_geographic_execution_plan_risk.py:42–46` 的 `snapshot()` 手写正确形状的规则，75–112 行两端都使用这份数据，未覆盖真实 `capture_fixture_authority → plan → storage → restore` 边界。因此 REPORT 第 79–81 行不能据此宣布真实冻结路径风险不变。

修复要求：将风险 `rules` 冻结和校验绑定到原 triage 实际消费的 `prompt_profile_snapshot`；结构化 `rule_snapshot` 继续独立保留。使用真实 P1 resolver 的正式/临时规则×正式/临时词库四组合，保存恢复后，对照原请求、身份排除、命中/自命中、实际分数及排序。无需修改生产评分引擎或进入 P4。

## MEDIUM：缩小了既有搜索词上限参数范围

位置：[scope_search_plan.py:128](../../backend/investigation_creation/scope_search_plan.py#L128)。P1 `config.py:251` 接受 `SEARCH_TERMS_MAX` 为 1–200，新 `RuntimeLimits` 却限定不超过 100。实际 P1 配置为 100/101/200、且仅有一个确认搜索词时，P2 分别成功/失败/失败。

这会拒绝原流程合法参数，即使并没有超过 100 个实际词，也无法构造计划。修复要求：保持 P1 已有合法范围，并用一词有效计划覆盖 101 和 200；不调整原搜索词上限规则。

两项缺陷的离线复现见 [acceptance-risk-request-mismatch](artifacts/acceptance-risk-request-mismatch-20261015-01.json)，包含原计划及输入摘要、请求长度/摘要/参数、完整固定输入、人工输出和参数范围复现。仅两次人工客户端调用，真实模型/SQLite 调用均为 0，guard attempts 为 0。

## 新鲜测试与四项基线失败

本次使用已有 Python 3.12.10 及最终 runner，在 pytest collection 前安装防误调用 guard。完整命令和路径位于每轮 commands.json：

```sh
/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_execution_plan.run_offline --output-dir experiments/geographic_execution_plan/artifacts/acceptance-full-20261015-01
/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_execution_plan.run_offline --output-dir experiments/geographic_execution_plan/artifacts/acceptance-p1-baseline-20261015-01 --baseline-ref 031e0cd26379ee44c2372c9a28536c377ab3d4b9
```

- 完整 677 项：**673 passed / 4 failed / 0 errors / 0 skipped**，143.62 秒；新增 151 项全部通过。证据：[summary](artifacts/acceptance-full-20261015-01/summary.json)、[commands](artifacts/acceptance-full-20261015-01/commands.json)、[JUnit](artifacts/acceptance-full-20261015-01/junit.xml)、[日志](artifacts/acceptance-full-20261015-01/pytest.log)。
- 精确 P1 `git archive` 临时副本：4 项全部同节点、同原因失败，3.35 秒；没有 P2 应用代码路径。证据：[summary](artifacts/acceptance-p1-baseline-20261015-01/summary.json)、[commands](artifacts/acceptance-p1-baseline-20261015-01/commands.json)、[JUnit](artifacts/acceptance-p1-baseline-20261015-01/junit.xml)、[日志](artifacts/acceptance-p1-baseline-20261015-01/pytest.log)。
- [逐组结果及失败分类对照](artifacts/acceptance-test-comparison-20261015-01.json)逐项验证两轮失败节点和原因相同，没有只比较失败总数。
- 17 个 authored Python 文件编译、11 个固定依赖摘要、空白检查通过。完整轮的两条 SQLite 拦截属于故意拒绝探针，真实网络/模型/采集/调度成功为 0；基线轮拦截为 0。

| 失败节点（`test_search_terms_cap.py`） | 本次完整轮与精确 P1 的共同原因 | 对本轮验收的影响 |
|---|---|---|
| `test_prompts_are_formatted_with_the_setting[10]` | 仍断言旧资源提示语“默认整组 1 至 10 个实际搜索词” | 既有断言不匹配，不由 P2 引入。 |
| `test_prompts_are_formatted_with_the_setting[6]` | 同上，参数为 6 | 同上。 |
| `test_task_creation_caps_search_terms_for_every_source[formal-temporary]` | 临时召回 fixture 缺当前会话有效资源引用，`RESOURCE_REF_INVALID` | 不能据此放宽资源保护；本轮真实四资源组合另有通过证据。 |
| `test_task_creation_caps_search_terms_for_every_source[temporary-temporary]` | 同上，错误回执后断言失败 | 同上。 |

四项均未改、未 xfail、未跳过。**可作为已确认的基线失败单列，不要求在当前禁止修改原工具/测试的 P2 范围内修复；不能宣称测试全绿。** 它们也不能抵消本次 HIGH/MEDIUM。

REPORT 第 136 行链接的旧基线日志，第四项当时实际先被 SQLite guard 阻断，未到达资源引用检查。其“这份日志证明两项资源引用失败”的表述证据不足。本次新基线已完成补证，修订 REPORT 时应改引新日志，并更新完整轮结果；此文档证据问题不额外计为代码缺陷。

## 交回修订范围与再次验收条件

仅修正允许的新纯计划/实验模块及新增回归，保留全部原生产文件与地域启动阻断。修复上述两项、补真实冻结恢复风险对照并更新 REPORT 后，再做当前范围的复验。不得因本次验收进入正式确认、Run/Job、P4、真实模型质量或启动启用工作。

本次只新增验收文档和离线证据；未修改原交付文件、未联网/安装/调用真实模型/启动采集或服务/写正式 DB，也未 commit/push/merge/deploy 或发消息到其他会话。
