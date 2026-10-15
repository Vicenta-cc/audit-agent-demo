# P2/P4 接口缺项与正式接入前置

日期：2026-10-15；对应 P4-offline-v1。这里记录待验收接点，不读取移动中的 P2 分支。

## 本轮实际输入

`fixtures.fixture_plan()` 只产生 `p4-fixture-input-v1`，固定 `validation_origin=fixture`，只支持 `dy + search + select`。owner/session/draft/revision 是测试绑定，不是正式确认凭证。P4 核心不 import `scope_search_plan.py`，不解析真实资源，不访问当前草案、设置、词库或名录补值。

本轮没有取得并验收 P2 的实际交付版本，因此 **P2→P4 实际兼容尚未验证**。P2 接口适配不能以本轮夹具测试代替。后续首先登记其精确 commit、接口文件 SHA256、schema 和验收结论，再新增实验适配与契约测试；不自动使用工作树 HEAD。

| 接口 | 当前测试投影 | 正式联调尚需提供/验证 |
|---|---|---|
| 确认与授权 | fixture owner/session/draft/revision、预期 execution_id/plan_digest | 已验收 P2 的真实来源验证、确认快照及权限边界；摘要自洽不等于授权 |
| 搜索计划 | 有序 query_id/query、每词上限、任务上限、capture_parameters | 权威资源解析后的原词与顺序、所有来源、0/default 参数语义、评论参数及冻结设置 revision |
| 风险输入 | 完整 config、词库 terms/search_groups、规则和原引擎参数；每 occurrence 原 rank/source/comments | 已验收冻结适配的确切数据形状和版本；保留原 `terms_for` 关联；不得直接写私有 cache |
| 地域上下文 | 原样 P3 fixture context；scope/policy/model/prompt/admin 摘要 | 真实确认来源到 P3 的正式适配；不能把 fixture 字符串改为 frozen 冒充授权 |
| 候选身份 | 测试明确给出的 platform/content_key/post_id、query、rank 和证据 | 正式平台规范 content_key、采集账号、稳定评论用户 ID、缺失/截断标记及原来源绑定 |
| 已审排除 | occurrence.already_audited 测试快照 | 正式已审查询时点、恢复一致性和注入边界；地域不能复活已审结果 |
| 原排序 | 编排调用固定 P1 `rank_candidates`；纯选择稳定过滤该列表 | 已验收输入适配与实际 CandidateScore 一致性；不新增跨词分数榜 |
| 账号处理 | 显式注入账号身份和一次换号结果；auth_pending 持久化 | 与既有真实账号退出/轮换/租约的接线；候选账号与精采账号映射；无账号/第二次失败按原类型传播 |
| P3 运行 | prepare_request + 人工响应 decision；精确请求重建和结果重放 | names-v2 正式调用/去重/成本/超时未知窗口及语义质量验收；未接旧 evaluate_and_record |

## 本轮未实施的接点

- 正式 resources/service/store 确认事务、Run/Job/contracts/adapters/frozen/JobStore 穿透与 schema 迁移。
- 正式 pipeline 搜索/停止/进度/账号/地域选择接线，无地域仍需保持原路径。
- 爬虫完整 receipt 与原 callback 的真实幂等边界；本轮稳定 key 只验证受控幂等交付器，不证明外部严格一次。
- 正式恢复与对账入口；实验库不得直接转为生产库。捕获未知、精采未知和认证决策中断需可信人工/外部证据恢复，不能超时释放。
- 正式筛选查询/鉴权、P5 分层统计和报告、全链路质量验收与独立启用改动。地域启动阻断继续保留。

预计冲突文件是 pipeline.py、JobStore、创建确认 contracts/resources/service/store/adapters/frozen 及正式 callback 适配。本轮未编辑这些文件。

## 固定 P3 已知限制

目标克拉玛依时，仅乌鲁木齐可判 other_region；同时选中新疆和乌鲁木齐可判 province_unknown。本轮真实调用原决策并按 province_unknown 正常消费，不加城市共现、措辞纠错或额外阻断。此消费测试不构成语义质量结论，也不列为 P4 独立消费验收阻塞。

完整全词候选屏障会增加首结果等待和候选请求成本；最终精采每词/任务成功上限未增加。P3 真实语义质量、真实平台召回和正式外部回调幂等均未由本轮证明。
