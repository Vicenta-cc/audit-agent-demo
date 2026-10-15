# 地域正式路径接口映射

2026-10-15；实施说明，待原会话验收。业务源码根为本 worktree，不是运行中的 P1。

| 接点 | 本轮接口/职责 | 不变量与验证入口 |
|---|---|---|
| 草案 → 确认 | `geographic_runtime.capture_confirmed_authority` 从会话、资源、权限和设置捕获权威输入；确认事务内再次校验 | 前端自填摘要不能授权；`test_geographic_runtime_freeze.py` |
| 确认 → Run | `execution.geographic_plan` 使用 `geo-confirmed-execution-plan-v1` / `confirmed_run`；地域 Run 使用 `investigation-run-config-v5` | 无地域省略新字段；旧版本不接受地域载荷 |
| Run → Job → pipeline | adapter 校验可信 Run 后产生 `_verified_geographic_binding`；`_m3_snapshot_validator` 与 `_geographic_lease_fence` 每次重验 | owner/session/draft/revision/run/job/config_hash/p2_digest；Job 与请求不自证可信 |
| P2 → P4 | `GeographicBridge` 派生 `p4-confirmed-input-v1`；计划摘要与运行身份分层 | 查询词、顺序、有效参数、风险规则冻结；不从实时设置补缺 |
| 原始捕获 → 风险 | `bridge.occurrence/risk` + 原 `TriageEngine`；raw_source 保存原帖、评论、身份字段，规范化字段另行校验 | 首次落盘、query 屏障、恢复都绑定不可变捕获；原评分和排序不变 |
| P3 运行时契约 | `audit_agent/geographic_p3_runtime` 显式衍生；固定 tag 的原样导出保留 | 仅来源/错误码封装差异；判定、提示、名录保持，差分测试覆盖 |
| pipeline → capture/detail | `GeographicPipeline` 注入原 MediaCrawlerAdapter；同一 P4 执行器协调全词屏障、账号、预算和停止 | 候选不提前交付；detail callback 不直接入审核；原 crawler_adapter 未修改 |
| P4 → 正式存储 | `GeographicExecutionStore` 复用状态转换；绑定独立 expected digest/identity；事务检查 lease | 原实验 Store 的 fixture/路径限制保留；未知结果保留待恢复状态 |
| 精采 → 复核 | 保留第一次 RiskRecord，仅对精采证据重新地域判断 | 同档正常变化允许交付；变档/失败不进入 membership，已成功采集额度不退 |
| 复核 → ingestion | `GeographicDelivery.deliver` 返回持久 receipt；稳定幂等键、ledger 与 membership 同库事务 | 本次专属不可变 raw 文件；恢复校验摘要，拒绝旧 shared_raw 或无账本 membership |
| ingestion → 原审核 | pipeline 只投递已提交 refs；沿原审核队列/保存路径 | 初筛 weak/strong 不替代正式审核结果；审核读取本次获准精采原文 |
| worker → 终态 | `geographic_completion_failure` 区分 ready/empty/incomplete | 未完成、未知、待交付不能发布成功报告；零入选不制造帖子 |
| Run → 前端状态 | `GET /api/investigation-runs/{run_id}/geographic-selection`，offset/limit/tier | 按 Run principal 授权；不返回账号、凭据、任意路径；历史 Run 不借用当前状态 |
| 审核 → 报告 | source snapshot 保存 `selection_basis`、独立 `geographic_coverage`；绑定 source_job_id/content_key 并冻结发布 | 不改变原风险统计分母；旧无地域序列化兼容；前端读取发布快照 |
| 隔离启动能力 | `IsolatedGeographicCapability` 仅显式服务端安装在临时隔离根 | 默认地域启动阻断；前端请求不能打开；重建 store 不继承能力 |
| 真实模型预算工具 | `experiments/.../model_gateway.py`，SQLite 原子转发前扣额，默认最多30 | 错误/超时/内部重试不退额、重启不清零；尚未给所有模型角色统一配置与审计，不能声称 R6 已就绪 |

跨库不宣称单一原子事务：ingestion 提交后、P4 记回执前中断，依稳定交付键查询/补认。外部 detail 未知结果不宣称严格一次。

模型预算网关当前仅支持 `/v1/chat/completions`，SSE 缓冲，默认 transport 不读取环境代理。真实测试前须处理模型角色/API 和专用出口配置并验证，不改旧风险评分代码以迁就环境。
