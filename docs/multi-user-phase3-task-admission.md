# 多用户阶段三：任务准入、名额和恢复

> 额度与整任务结束规则已由 [接受任务计次修订](multi-user-task-lifecycle-v2.md) 替代。本文及其中的探针收据保留当时规则，不代表新任务仍按报告成功计次。

本阶段基于阶段二提交 `9cc0aebe8a77e2a86c751303865083d99525e4ec`。仅为候选代码；不代表已部署或完成多用户开放验收。

后续阶段四已将多用户执行改为按任务及采集账号协调并发；本文串行锁说明仅描述阶段三历史状态，当前运行及升级规则见 [阶段四说明](multi-user-phase4-concurrency.md)。

## 规则和账本

`APP_AUTH_MODE=required` 时，新建 Job、HTTP/Agent 草案确认、继续采集、继续/补充分析、重试报告都经过同一准入账本。

- 每用户每天最多三个成功报告。接受任务时暂占一个名额；正式 ReportVersion 发布才结算成功。
- 每用户最多一个未完成任务。排队、执行、暂停、取消中、结果未明都占用这一位置。换对话、浏览器或请求入口不能绕过。
- 无成功报告的最终失败或取消，在确认执行停止后释放。暂停不释放。删除 Job 或报告不删除账本、不恢复已用额度。
- 已释放任务的手动恢复重新准入；同一次暂停恢复沿用预留。报告生成失败可以只重试报告，复用已有采集和报告生成标识。
- 默认使用北京时间零点，跨日任务结算到接受时的日期；旧任务仍占用户唯一位置。这是计划中的推荐默认规则。
- 到期/禁用后不再派发，在执行安全检查点停止。此前已经发布的报告仍计成功，后台可以补齐其会话及状态，不再发起采集或报告生成。

`investigation_creation.sqlite3` 新增 `task_admissions` 和 `task_admission_commands`。名额预留、用户唯一位置、冻结输入和待执行记录在 `BEGIN IMMEDIATE` 同一事务中落库；M3 Run 创建也在该事务中。两个部分唯一索引约束用户和任务的 RESERVED 行。账本不外键关联可删除业务数据。

状态：`RESERVED → SUCCEEDED / RELEASED`；执行投影：`QUEUED / RUNNING / HELD / DONE`。`decision` 持久记录取消与发布谁先获得执行权。发布先写入报告版本的 PUBLISHING 意图，再提交报告数据库。恢复核对已发布版本，避免报告已生成却返还名额。

## API 和客户端兼容

保留原有登录、CSRF、资源授权要求。普通用户恢复的任务必须归自己所有。

| 接口 | 变化 |
| --- | --- |
| `POST /api/jobs` | required 模式须 `Idempotency-Key`；先持久准入，再返回稳定 Job；API 不再后台执行采集。 |
| 草案确认 HTTP/Agent 工具 | 沿用已有幂等键，确认事务同时预留和入队。 |
| `POST /api/jobs/{id}/control` | 恢复采集/分析/补充分析须 `Idempotency-Key`，交给 worker；stop_all 记录取消并通知安全停止。 |
| `GET /api/me/task-quota` | 返回 day、timezone、limit、completed、reserved、remaining、reset_at、active_task。跨日旧任务可能占 active_task，但不占新日 reserved。 |
| `POST /api/tasks/{task_id}/cancel` | task_id 可为准入任务 ID 或其 Job ID；取消中保持 RESERVED，停止后才释放。发布已经获得执行权时不强行改判取消。 |
| `POST /api/tasks/{task_id}/retry-report` | 须 `Idempotency-Key`；仅报告生成失败允许；重新准入并复用已有结果。 |

准入冲突返回 409，包含 `USER_TASK_LIMIT`、`DAILY_REPORT_LIMIT`、`IDEMPOTENCY_CONFLICT` 等 code。旧创建/恢复入口缺少请求键返回 400；报告重试缺少必填 Header 返回 422。同键同内容重放；不同内容拒绝。前端统一客户端合并同一时刻的重复点击，并用 sessionStorage 保留网络/服务器错误下的请求键，供刷新后重试。完整额度和排队页面属于阶段五。

## Worker 与停止证明

required 模式必须运行 `python -m backend.investigation_creation.worker`，API 和 worker 必须指向同一套隔离数据目录及认证库。没有 worker 时任务仍然持久排队，取消中名额也不会自动释放。旧的直接 Pipeline/报告发布调用不再允许绕过执行身份。

阶段三使用 `<control-db>.execution.lock` 的 POSIX flock 暂时串行执行。MediaCrawler 子进程继承锁 FD；父 worker 退出不会提前解锁。只有通过新 FD 重新取得锁，才能判断旧执行已停止并释放失败/取消任务。不能仅凭心跳过期返还，也不能删除运行锁文件强制解锁。API 启动不再批量改写全局 Job 和分析状态。

恢复顺序：核对正式报告 → 补齐成功状态；否则核对取消/到期/明确失败 → 安全释放；未知或暂停 → 保留名额并展示可恢复状态。恢复与新请求用条件更新/同库事务防止互相覆盖。只重置已停止任务自己的分析记录。

该机制面向同机 macOS/Linux 和本地文件系统，不是跨主机租约。不同用户可以同时提交，但真实采集目前仍串行。阶段四必须用账号/Profile 独占、资源租约及容量限制替换全局执行锁，再验收跨用户实际执行时间重叠。浏览器维护、登录冲突和真实容量不在本阶段宣称完成。

## 上线与回退边界

本阶段没有执行正式迁移。后续演练需保留原业务库、outputs、账号和 Profile，并备份新增控制账本。先停止旧 worker 并妥善结束未纳入账本的旧任务，再切换 API、worker 和前端；不能把旧活跃任务直接当成已准入任务。新 schema 在候选库初始化时增量建表，不改写历史额度，也不清空历史业务数据。

禁止混用旧 worker、新 API 或多个不同控制库驱动同一业务目录。禁止用本地旧库覆盖云端。回退必须暂停普通用户入口并保留新账本；不能退到旧模式后继续对普通用户开放，绕过鉴权或名额。

## 验证范围

`tests/test_task_admission.py` 覆盖多窗口竞争、幂等、每日额度、跨日、取消/发布裁决、过期、事务回滚、孤儿爬虫、恢复竞争、报告单独重试及普通 Job 报告归属。故障测试使用临时库、测试适配器与无网络子进程。它们不替代真实 MediaCrawler、模型供应商、两个独立账号/Profile 或云端容量验收。

最终测试收据与提交号见工作区外阶段三交接文档。
