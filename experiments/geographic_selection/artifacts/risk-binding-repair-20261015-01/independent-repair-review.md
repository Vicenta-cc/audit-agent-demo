# 风险证据绑定返修：独立只读复核

2026-10-15，Codex native explore 子代理，未修改文件。先完整读取原验收报告、反例及原始/规范化字段，再对最终三个核心模块作只读复核。

结论：未发现本轮风险证据绑定 HIGH 的必要遗漏。最终完整测试结果仍由主代理读取确认，本记录不是原会话复验结论或 P4 整阶段验收。

- 首次风险落盘从不可变 capture 唯一定位 occurrence；完整原帖/评论与 risk.input 对照，覆盖认证、身份及影响排序字段。
- canonical digest 对照两份实际对象，保留 JSON 标量类型差异；不只是 RiskRecord 自摘要。
- candidate.raw_source 提供原始评分证据；原 P3 normalize/request/decision 校验仍负责其规范化对应关系，未改动原评分或 P3。
- query 完成屏障、部分缓存及已完成缓存加载均重验绑定。已见重算快照摘要后旧风险仍拒绝的测试。
- 精采复核仅用已持久化 reservation 中的首次 candidate 校验风险，新 evidence 只走地域复核；不重评首次风险。
- WATCH 未扩大；真实 P2、正式流水线和外部回执协议不属于本轮返修。
