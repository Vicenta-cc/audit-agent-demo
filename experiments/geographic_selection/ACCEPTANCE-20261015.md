# P4 独立交付验收 — 2026-10-15

**结论：REQUEST CHANGES。code-reviewer：REQUEST CHANGES；architect：BLOCK。一项 HIGH 阻塞，一项非阻塞 WATCH。**

范围是 P4-offline-v1 的独立选择、实验状态存储、注入式编排及离线验证；不将真实 P2 适配或正式业务接入当作本轮完成条件。本轮没有修改交付源码、原测试、原 REPORT 或原验证产物，只新增验收报告和 `artifacts/acceptance-20261015-01/` 证据。

## 版本与边界

- 工作树：`/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p4/xhs-audit-agent-demo`。
- 分支：`codex/geographic-relevance-p4`；HEAD：`031e0cd26379ee44c2372c9a28536c377ab3d4b9`。P4 新增文件尚未提交，本报告不将 HEAD 当成包含 P4 源码的提交。
- P3 tag：`p3-post-only-names-v2-local-20261015`，commit：`2120c53379d1b79c45e3cb8232b9c9b96657b183`。
- 与精确 P1 Git 归档逐字节比较的 1570 个原有文件全部不变；43 个固定输入、11 个 P3 导出与固定 Git 对象一致；交付清单中的 23 份作者文件摘要一致。见 [input-audit.json](artifacts/acceptance-20261015-01/input-audit.json)。
- 原搜索词、原风险引擎及评分输入配置、原 query 顺序和各词原排序未重写；地域档位作为外层选择顺序。无新增产品入口或开关，地域启动阻断保留。
- P4 消费固定 P3 结果，不纠正其语义。既有“新疆 + 乌鲁木齐可能为 province_unknown”不列为 P4 阻塞。

## HIGH：首次风险结果未绑定当前捕获证据

定位：`backend/audit_agent/scoped_selection.py:133–154`，尤其 135–142 行；编排入口 `backend/audit_agent/scoped_execution.py:94` 消费该结果。

当前检查了 RiskRecord 的自摘要以及 occurrence、帖子 ID、query、rank、config，却未证明其中评分所用的原文、评论及认证信息确实对应当前初始候选。摘要自洽只能证明记录没变，不能证明它属于这份候选证据。

已用原引擎和现有夹具复现：同一 execution/query/content/rank，普通候选得到 `weak`；带 `enterprise_verify_reason='公安官方'` 的当前候选正确得到 `discard`。把前者风险记录装入后者，保持当前完整 P3 结果不变，地域和资格校验仍通过，完整编排输出 `collected=1 / handed_off=1`。也就是原引擎已正确丢弃，P4 却允许旧结果绕过该决定。

最小修复边界：

1. 在首次评估落盘及 query 完成屏障前，将评分输入与不可变的原始捕获证据、所属 query/occurrence 绑定并校验。不应只增加一份由记录自身生成、未与捕获证据对照的摘要。
2. 绑定覆盖影响原评分的原帖字段、评论及认证/身份字段；适配时明确定义原始与规范化表示的对应关系，不能粗暴比较两个本来字段形状不同的对象。
3. 精采后的新文字/评论是另一份证据。保留首次风险绑定及原风险结果，继续按原方案只进行地域复核；不能为满足新校验而修改原评分引擎、重新评分或拒绝所有正常精采变化。
4. 增补同帖同词同 rank 但原文、评论、认证信息不同的旧 RiskRecord 混用拒绝测试，并保留同档精采变化可交付、完整缓存恢复不重评、原排序一致的回归。

无需修改 P3、MediaCrawler、对话端或正式 pipeline。不得用地名/措辞规则替代内容模型判断。

## WATCH：交付回执的可选状态语义尚未约定

定位：`backend/audit_agent/scoped_selection_store.py:370–382`、`backend/audit_agent/scoped_execution.py:166–176`。

完整绑定回执即使额外带 `status='rejected'`，当前也会记为 handed_off。复现输出已存证，但**本项不作为本轮阻塞**：既有 store 测试明确接受无 status 回执，交付失败测试使用抛异常；尚未约定 status 字段和拒绝状态协议，不能据此追溯要求所有合法回执新增成功字段。架构审查复核后已撤回此项独立阻塞结论。

建议说明当前“正常返回代表成功、失败抛异常”的契约。若保留可选 status，则明确其存在时必须为 accepted；无 status 的现有成功回执保持兼容。拒绝/待定不得冒充成功，已采预算和稳定幂等键保持原语义。该建议不要求新增正式交付器或外部严格一次保证。

## 新鲜验证证据

主审新鲜重跑原范围九份测试：**261 passed、2 warnings，407.05 秒，exit code 0**。五项 OfflineGuard 计数均为 0；原有两条依赖弃用警告保留。见 [checks.json](artifacts/acceptance-20261015-01/checks.json) 和 [完整日志](artifacts/acceptance-20261015-01/pytest.log)。这套既有测试未覆盖上述风险证据混用反例，因此通过不撤销 HIGH 结论。

- 独立反例：[脚本](artifacts/acceptance-20261015-01/reproduce_review_findings.py)、[实际输出](artifacts/acceptance-20261015-01/reproduce_review_findings.json)。在临时实验数据库、受控人工模型响应下，主审独立复现上述两个行为；网络、外部进程、真实模型、采集及正式数据库五项拦截计数均为 0。
- 两路独立审查：[汇总与架构复核说明](artifacts/acceptance-20261015-01/independent-review.md)。两路均确认风险证据绑定 HIGH；架构通道最终总体 BLOCK，回执条目仅 WATCH。
- 全套回归使用原 `run_checks.py`、同九份测试及同 OfflineGuard。为保留原交付证据，验收 [rerun.py](artifacts/acceptance-20261015-01/rerun.py) 只在内存替换输出目录；没有改动原 runner。实际命令如下，后续复跑应使用新的输出目录，保留本次证据：

```sh
PYTHONPATH=backend:. /Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python experiments/geographic_selection/artifacts/acceptance-20261015-01/rerun.py
```

Runner 的 checks.json 中 `actual_guarded_command` 仍沿用其原模块调用说明；准确的本次入口以上述已保存 harness 为准，pytest argv、临时库路径及拦截计数见本次 checks.json。反例不属于原 261 项测试，不能把其输出冒称为新增通过测试。

## 停止点与下一步

先在原 P4 独立工作树小范围修复 HIGH、补反例回归并复验；原引擎/P3/正式流程不动。通过后固定已验收 P2 和 P4 的精确源码快照及接口，再另做 P2→P4 实际冻结计划的离线适配、重启恢复和评分一致性联调。

P2 会话已交付两项返修结果，但其复审不自动等于 P2→P4 实际接口验证。当前 P4 仍使用 fixture 计划，尚未接正式 Run/Job、正式数据库或真实采集；不宣称 P4 整阶段通过，也不解除地域启动阻断、不进入正式启用。
