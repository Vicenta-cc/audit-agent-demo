# M3 报告闭环补全 · 2026-09-09

工作区：`xhs-audit-agent-m3-frozen-baseline`，分支 `codex/m3-prompt-300-frozen-baseline`，本次修改直接基于 `47b83b6`。

## 问题和修复

`47b83b6` 继承了 `f7d6a40` 的 pass 模板，但调查 worker 对 v3/v4 Run 在审核完成后提前返回 `AUDIT_COMPLETED`，没有调用报告生成器。模板测试通过不能证明调查可自动产出报告。

本次从 `f7d6a40..3854674` 提取报告代码和相关测试差异：

- 移除 worker 的提前返回，通过审核完成门禁后进入 `REPORT_GENERATING`，沿用已有生成预约、发布、会话绑定和恢复机制。
- 将调查名称传入冻结执行配置及 Job，报告标题沿用调查名称。报告源读取对应 ReportStore 的数据库，避免默认资源库与注入资源库不一致。
- 全 pass 使用已有确定性模板；单条风险帖保留真实审核决定和依据，作为独立样本生成报告；多条风险仍走原 R3.1 路径。
- 帖子详情按 `reportVersionId + postRef` 跳转；pass 没有风险证据或聚类引用时，仍可打开其审核说明和来源资料。前端补齐复审统计以及失败/中断状态恢复。
- 包含 `1dbde3f` 的风险输入修复：`review/reject` 且 `low/medium/high` 均可进入风险聚类。

保留 `47b83b6` 的提示词、原生工具发现及新任务默认 300 条评论设置。采集/审核数量仍由原 M3 单条冻结合同控制。本次不引入号池、v5 配置、账号租约或配套 MediaCrawler 改动；不将旧整合环境的采集验收记录当作当前分支的真实验收。

## 验证

- 后端 211 项测试及 23 个子测试通过：pass 报告、风险筛选、M3 创建与恢复、配置、对话、发现引导。
- 集成测试使用模拟采集/审核输入，真实执行 Worker、SQLite 持久化、报告生成与发布、会话绑定；覆盖 pass/review/reject，检查报告标题、样本决定、依据数量和重启后不重复执行。
- 前端调查配置、恢复、发布映射和帖子详情路径 35 项测试通过；TypeScript 检查及 Vite 构建通过（保留已有包体积提示）。
- `git diff --check` 通过。未在本轮重新请求抖音、调用真实审核模型或执行历史 Report A/B 归档测试。

后端验证命令：

```sh
python -m pytest -q tests/test_pass_report.py tests/test_report_risk_inputs.py tests/test_investigation_creation_m3.py tests/test_investigation_draft_configuration_m3.py tests/test_investigation_creation_conversation.py tests/test_m3_discovery_guidance.py
```

## 运行边界

本次修复适用于后续执行且通过完成门禁的调查；历史已经终止在 `AUDIT_COMPLETED` 的 Run 不会自动重排队或补报告。采集失败、模型审核失败、资料不完整时仍停止，不能自动伪造通过结果。

本次只更新代码和提交，不重启现有服务。运行环境需要使用本分支的新提交并重新启动 worker/API、加载对应前端，才会执行补全后的流程。原 `codex/m3-resource-management` 等工作区不会随本分支提交自动更新。
