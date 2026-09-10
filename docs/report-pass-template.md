# 全 pass 报告分支

该分支供单条帖子审核演示使用，复用现有报告发布、展示接口和页面版式。报告生成阶段不调用 Qwen，审核结果仍来自真实审核数据库。

## 生成与展示

- `R31ReportRuntime.generate(task_id)` 在有效审核结果非空且全部为 `pass / none` 时选择 `PassReportGraph`。
- 生成前要求任务状态为 `completed`。冻结资料仍须覆盖每个报告样本的审核结果，不允许存在风险评论或直接风险依据；不完整的评论审核结果明确列为未审核。
- 章节为调查概况、数据概览（含三个子节）、审核结果与样本展示、综合研判、调查结论与建议及附录。不生成风险聚类或独立风险事项章节。
- 第三节单帖完整展示；多帖时展示冻结顺序中的前 3 条，统计仍覆盖全部审核帖子，附录可分页查看全部。
- 正文与附录详情共用原文、原帖链接、已有转写及译文组件。缺失字段不补造，转写对象的模型信息和本地文件路径不进入展示字段。
- 账号活动仅统计本次样本中的发布记录，按来源平台及稳定账号标识合并；不依赖历史账号资料库，也不提供历史账号活动推断。
- 发布沿用结构校验、内容哈希、持久化和原有读取权限。恢复执行沿用已冻结的模板与资料，不重新根据当前审核库选择分支。

## 真实历史样本预览

`scripts/preview_pass_report.py` 从指定历史报告中读取真实的通过帖审核结果，在独立数据目录生成演示任务和报告。输入归档使用只读连接；不会重新抓取或重新审核，报告标题明确标记“历史审核样本”。

```bash
python scripts/preview_pass_report.py \
  --archive /absolute/path/to/report-generation.sqlite3 \
  --report-version report-version:SOURCE_VERSION \
  --data-dir /absolute/path/to/separate-preview-data
```

使用输出的任务 ID、报告版本 ID 打开 `/investigation/{task_id}/report?report={report_version_id}`。预览后端的 `XHS_AUDIT_DATA_DIR`、`XHS_AUDIT_OUTPUTS_DIR` 应指向该独立目录；前端须显式配置 `VITE_API_PROXY_TARGET`。

## 集成边界

原模板提交 `f7d6a40` 打通“已有审核结果 → 全 pass 报告生成/发布 → 前端正文和附录”，不含调查 worker 自动触发。当前分支在 `47b83b6` 后补全了自动触发、会话绑定、状态回传及详情跳转，见 [报告闭环补全记录](M3_REPORT_COMPLETION_20260909.md)。历史样本预览仍不能当作新抓取的完整演示。

原模板分支为 `codex/report-pass-template`，基于共同冻结基线 `7c32921`。当前 `codex/m3-prompt-300-frozen-baseline` 已迁入 `1dbde3f` 的 reject 聚类修复和 `3854674` 的报告整合差异；没有修改原模板、Deep-link、T6-A 或 authoritative 工作树。

## 验证

- `tests/test_pass_report.py`：单帖、多帖全量统计与展示限额，报告/详情/附录 API，结构化转写映射，未审核评论，风险评论拒绝发布，未完成任务拒绝发布，故障恢复及风险报告分支保留。
- `tests/test_r31_presentation_projection.py`：通过 `R31_REAL_REPORT_A_DB`、`R31_REAL_REPORT_B_DB` 指定真实 Report A/B 归档，测试在临时副本中执行。
- 前端 TypeScript 检查、Vite 生产构建；真实历史通过帖预览中核对目录跳转、正文与附录详情、原帖链接及窄屏布局。
