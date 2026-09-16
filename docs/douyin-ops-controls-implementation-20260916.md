# 抖音任务参数、采集/分析解耦与实时控制实施记录

最新设计与状态见 [M3 底层行为收敛记录](m3-execution-parity-20260916.md)：参数现为业务入口下统一设置，不再以本文件早期的逐任务表单为准。

后续更新：本文件记录离线实施阶段。2026-09-16 下午已开展前端真实验收，结果为部分通过及待修复项，见 [前端真实验收记录](douyin-ops-controls-live-acceptance-20260916.md)。下文“未执行真实验收”仅描述当时的实施阶段。

## 范围与基线

- 实施日期：2026-09-16（Asia/Shanghai）。
- 应用起点：`a925969d78e570bbd99413dfa2e2b0d5283811da`。
- 开发分支：`codex/douyin-ops-controls-20260916`。
- 开发工作树：`xhs-audit-agent-douyin-ops-controls-20260916`。
- MediaCrawler：`6d2c85b`，本次未修改。
- 正式 3198/8198、冻结 8027 以及原有脏工作树均未启动、覆盖或修改。
- 本轮没有创建小红书、快手或真实抖音采集任务。

本次完成的是同一 API 进程内的逻辑解耦：采集子进程与分析线程使用独立持久化状态和控制字段。它不是采集、分析两个独立服务；API 进程退出仍会同时影响二者。

## 参数暴露分级

### A. 任务级安全参数

业务入口现已提交并冻结以下字段：平台、采集账号、关键词来源、搜索/博主模式、`max_notes`、`start_page`、评论开关、`max_comments`、`get_sub_comment`、媒体开关、`max_items_per_minute`、`max_concurrency`、任务级自动分析开关、`analyze_limit`、`analysis_batch_size`。

- `max_notes` 前后端均严格接受整数 1–5；关键词模式表示每个关键词上限，页面显示关键词数和预计最大总量。
- 博主模式明确表示本任务总量。
- `max_comments=0` 或关闭评论均表示不采集评论；关闭评论时实际配置会同步关闭二级评论并将评论上限归零。
- 媒体开关只控制 MediaCrawler 的 `--get_media`，不暴露文件路径或浏览器配置。
- `analyze_limit=0` 明确表示不自动分析。
- `requested_config` 保留用户请求；`effective_config` 记录关键词数、预计总量、服务端并发上限以及评论/媒体/自动分析的实际值。

### B. 管理员或部署级参数

继续由部署配置管理，不进入业务表单：crawler sleep、请求最小间隔、每分钟请求上限、请求并发上限、媒体请求间隔、cooldown、持久化 request gate、streaming/batch ingestion、flush size/interval、全局并发和平台共享限速。

### C. 禁止前台暴露

Cookie、登录凭据、API key、加密密钥、本地文件路径、浏览器 profile 路径、浏览器指纹细节、CloakBrowser 安全参数及任何可能破坏隐藏浏览器约束的开关均未暴露。公开流水线日志会隐藏账号名称、执行命令和本地路径。

## 状态机与恢复语义

- 持久化字段：`crawl_status`、`analysis_status`；`status` 仅承担兼容和聚合展示。
- 采集：`queued -> running -> pausing -> stopped -> queued/running -> completed`。
- 分析：`queued/pending -> running -> pausing -> paused -> running -> completed/partial`，或 `running -> stopping -> stopped -> running`。
- 暂停分析会在单条内容安全边界等待，采集线程继续。
- 停止分析会保留 queued 内容，采集继续到配置上限；单帖失败记录后继续下一帖。
- 暂停采集不会清空已入库内容；分析可继续消费队列。
- 恢复仅消费 queued/可恢复内容，已有 completed 结果会纳入去重集合，不重复审核。
- 应用启动时将遗留 `analyzing` 回退为 queued，并把活跃任务标记为可恢复的 interrupted/pending 组合状态。
- 所有恢复动作沿用原 Job ID。调查 Run 在同一 Job 恢复完成后重新进入 `RUNNING + recovery_required`，继续完成态校验和报告生成。

服务端 `available_actions` 是唯一按钮权限源。实时卡片分别提供暂停/继续采集、暂停/停止/继续分析，并展示数据库真实日志；已完成历史卡片只读。

## 自动验证记录

本轮没有真实任务 ID。自动化固定身份包括 `phase-state`、`same-task` 和 `stable-resume-job`，仅存在于 pytest 临时数据库。

| 验证项 | 结果 |
| --- | --- |
| 异常隔离 `test_collection_continues_while_analysis_skips_or_stops` | 5/5 场景通过；每场模拟采集目标 5 条 |
| 参数、适配器、阶段状态、续抓与异常隔离组合 | 52 passed |
| M3 冻结配置、历史 v3、分析补跑与供应商边界 | 219 passed |
| 冻结 MediaCrawler 的限速、共享 gate、浏览器模拟、分页续抓 | 27 passed, 1 skipped |
| 后端广泛回归（排除缺失的可选依赖与外部归档场景） | 1169 passed, 25 skipped, 2 deselected；另有 55 subtests passed |
| 前端 TypeScript | `tsc --noEmit` 通过 |
| 前端生产构建 | 通过；仅有既存的大 chunk 提示 |

模拟状态和计数由临时数据库断言：5 条采集目标在单帖失败、供应商失败和最后一帖失败时仍完成采集；分析失败计入 failed/partial，不覆盖采集完成态。日志断言覆盖配置、控制、恢复、采集、入库和分析阶段，且公开投影不包含账号、命令或本地路径。

## 未执行的真实验收与剩余风险

- 未确认采集账号及环境空闲，因此没有按计划执行 1–5 条真实边爬边分析，也没有真实任务的起止时间、采集数、分析数和失败数。不得把自动化结果描述成真实平台验收。
- 真实验收仍需在新的隔离运行环境完成：暂停分析后采集继续、暂停采集后分析清空 queued、重启恢复、页面/数据库/日志计数一致，以及无残留 Chromium/CloakBrowser 进程。
- 当前仍是同进程逻辑隔离。若要求一个执行器进程崩溃而另一个继续，需要后续拆成独立 worker/process，并补 lease、heartbeat 和故障注入验收。
- 仓库全量测试包含可选 Hermes Agent、冻结历史报告归档和 OpenCV 等外部依赖；缺失这些依赖的用例需在原门禁环境复核。
