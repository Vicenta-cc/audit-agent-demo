# P4 独立离线选择实验

固定应用基线 `031e0cd26379ee44c2372c9a28536c377ab3d4b9`；固定 P3 tag `p3-post-only-names-v2-local-20261015`，解引用提交 `2120c53379d1b79c45e3cb8232b9c9b96657b183`。P2 仅使用明确 fixture 输入。本目录不接正式任务入口。

在 P4 工作树根目录运行已有解释器，无需安装依赖：

```sh
cd /Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p4/xhs-audit-agent-demo
PYTHONPATH=backend:. /Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_selection.verify_inputs
PYTHONPATH=backend:. /Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_selection.run_checks
PYTHONPATH=backend:. /Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_selection.demo
```

第一条只读核验 tag/commit/tree、43 份 Git 固定输入、11 份 P3 原样导出、文档观察及 P1 禁区。第二条运行五份指定原回归与全部 `tests/test_scoped_*.py`，实际 pytest 参数、退出码、日志和拦截计数保存到 `artifacts/checks.json` 与 `artifacts/pytest.log`。第三条运行受控三词示例，保存选择、结算、交付轨迹及拦截计数到 `artifacts/example.json`。

测试入口把应用数据、输出和 pytest basetemp 都指向本次独立临时目录；网络、真实模型、爬虫进程、子进程和目录外 SQLite 连接均立即失败。没有更改或 mock 地域启动 guard。临时测试数据库运行后删除，原始业务数据库不复制。

`bridge.py` 用 P1 原引擎与固定词库适配产生真实 CandidateScore，用 P3 原 context/prepare_request/decision 消费人工响应。`validate_geo_record` 重新构造原请求并重放原 decision，避免只信任调用者声明的 tier/IP 资格。`fixtures.py` 的测试字段不能当作 P2 正式计划。

核心接口：

- `scoped_selection.validate_plan / eligible_tier / select_next_batch`：校验与纯选择。
- `scoped_selection_store.Store(explicit_path)`：事务状态、实体预留、完整 receipt、结算、复核、交付回执与追加事件。
- `scoped_execution.ScopedExecution`：只接收注入接口的离线捕获、原风险排序、精采、复核、稳定 key 交付和恢复。

Store 只接受临时目录或名称含 `geographic_selection` 的显式实验目录，拒绝已有业务表及常用正式库名。实例不连接 JobStore/RelevanceStore。未知尝试保持预留，通过 `ScopedExecution.reconcile(attempt_id, trusted_receipt, receipt_id=...)` 提交完整可信结果，不读取旧 candidates.json。

逐组证据见 [REPORT.md](REPORT.md)，正式接口待办见 [INTERFACE_GAPS.md](INTERFACE_GAPS.md)。
