# P2 验收缺陷修复复核（2026-10-15）

本次修复对象是 [原独立验收](ACCEPTANCE-20261015.md) 的 HIGH 与 MEDIUM 两项；不进入正式接入、P4 或解除地域启动阻断。当前交付范围和最终完整回归见 [REPORT](REPORT.md)。

## 修改与有效证据

| 原发现 | 本次修复 | 回归证据 |
|---|---|---|
| HIGH：风险冻结选错规则字段，审核目标漏传 | `authority.py:187` 冻结真实 `execution.prompt_profile_snapshot`；`scope_search_plan.py:382` 验证同一来源；结构化 `rule_snapshot` 保留在 resolution | 真实 P1 正式/临时规则 × 正式/临时词库四组合，从真实 resolver/store/settings 构造基准，对照 capture→plan→SQLite→restore 后的原评分引擎；完整请求、审核目标、原词、词组、身份排除、命中、自命中、分数、排序及两地域目标对照 |
| MEDIUM：新校验拒绝 P1 合法的 101–200 参数 | `scope_search_plan.py:128` 改为 1–200；生产配置默认 10 保持原样 | 一个真实搜索词分别冻结并序列化恢复 10/100/101/200；另验证错误结构化规则即使重算摘要也拒绝 |

四组合有效红测为 [risk-capture-red-20261015-02](artifacts/risk-capture-red-20261015-02/pytest.log)：4 failed，审核目标 85→0，完整请求不等，固定人工回答下分数仍相同。修复后 [risk-capture-green-20261015-01](artifacts/risk-capture-green-20261015-01/pytest.log)：4 passed、8 deselected，9.37 秒；[完整对照](artifacts/risk-capture-green-20261015-01/captured-risk-comparison.json)四组合每项断言均为 true，审核目标 85→85，地域 local/other_region 仅改变 fixture 入选资格。

契约红测 [fix-contract-red-20261015-01](artifacts/fix-contract-red-20261015-01/pytest.log)：3 failed、2 passed；修复后 [fix-contract-green-20261015-01](artifacts/fix-contract-green-20261015-01/pytest.log)：5 passed、20 deselected，3.21 秒。两轮均在 pytest collection 前启用离线 guard。

首次 `risk-capture-red-20261015` 的 KeyError 是测试初稿误要求不存在的字段，不能用作本缺陷红测。旧报告及旧错误计划保留作历史证据；本轮当前计划与回放入口以 REPORT 的修复后完整轮为准。旧计划不满足修复后的风险字段绑定，应重新从权威 fixture 捕获；没有正式数据迁移或生产接入。

## 独立复审

- `/root/p2_acceptance_code_review`：只读复审 4 个修改的源码/测试文件，**APPROVE — this repair**，CRITICAL/HIGH/MEDIUM/LOW 均为 0。确认基准为真实 P1 来源，红测可发现原问题；17 个 authored Python 文件编译、P1 tracked 不变、11 个固定 P3 依赖一致。
- `/root/p2_acceptance_architecture`：**Architectural Status CLEAR**。确认冻结输入与原 pipeline 同源、从冻结内容重新编译核对 prompt、真实保存恢复对照覆盖旧缺口；错误来源不能重新冒充；无最早词归属、生产 P4 或启动入口扩展。

两路复审均限定于本次修复，最终完整回归及文档由主审收口。四项精确 P1 既有失败继续单列，未修改、跳过或 xfail；它们本身不阻断此次离线修复，不据此宣称测试全绿。人工风险/P3 响应不证明真实模型语义质量，P2/P3 整阶段仍待原会话验收与后续接入。

最终单次完整回归为 **682 passed / 4 failed / 0 errors / 0 skipped（686 项，158.09 秒）**，新增 160 项全部通过。四项失败与精确 P1 同节点、同原因，见 [完整轮](artifacts/fix-full-20261015-01/summary.json)及[逐项比较](artifacts/fix-test-comparison-20261015-01.json)。P1 原文件和固定 P3 依赖的最终边界核验见 [source audit](artifacts/fix-source-audit-20261015-01.json)。
