# P2 离线计划与固定 P3 回放

应用基线为 P1 `031e0cd26379ee44c2372c9a28536c377ab3d4b9`。P3 仅原样导入固定 tag `p3-post-only-names-v2-local-20261015` 的最小依赖到隔离命名空间。本目录没有正式确认、Run/Job、采集或 P4 选择器接线。

在本工作树根目录使用已有解释器，输出目录必须全新且位于本实验内：

```sh
cd /Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p2/xhs-audit-agent-demo
/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_execution_plan.run_offline --output-dir experiments/geographic_execution_plan/artifacts/offline-rerun-01
```

入口先阻断网络、真实模型、采集与调度，再导入 pytest；SQLite 限内存、系统临时目录和显式实验输出。runner 创建独立临时根，父子进程在导入前由 `GEO_OFFLINE_TEMP_ROOT` 固定 tempfile 目录，避免旧测试 collection 改 TMPDIR 导致路径边界不同。Python 子进程继承同一 guard。guard 是防误调用边界，不是恶意代码沙箱。测试中的人工客户端及确定性响应不代表模型语义质量。

输出包括 `summary.json`、`commands.json`、`pytest.log`、`junit.xml`、阻断记录、计划 JSON/实验 SQLite、`p3-manual-replay.json`、`replay-cli.json`、`risk-comparison.json` 和 `captured-risk-comparison.json`。后者是四种真实 P1 资源组合经 capture→plan→SQLite 保存恢复的完整风险请求及结果对照；原 synthetic fixture 对照仅作补充。保存的计划、证据均标记 fixture，不能用作正式确认凭证。完整测试集包含原 P1 已存在的四项搜索词测试失败，详情及精确基线复现见 REPORT。

只跑全部新增测试，并保留原 M3 collection 的环境副作用（117 项仅 deselect）：

```sh
/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_execution_plan.run_offline --output-dir experiments/geographic_execution_plan/artifacts/new-tests-rerun-01 --new-tests-only
```

修复后[单次完整回归](artifacts/fix-full-20261015-01/summary.json)：686 项，682 passed / 4 P1 既有失败，新增 160 项全部通过。四项失败与精确 P1 同节点、同原因；全部轮次及修复说明见 [REPORT.md](REPORT.md)。当前可回放证据目录为 `artifacts/fix-full-20261015-01`。

单独复放已保存的计划和人工 P3 响应：

```sh
/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_execution_plan.replay --evidence-dir experiments/geographic_execution_plan/artifacts/offline-rerun-01
```

该命令安装离线 guard，从独立证据读取预期 identity/digest，恢复计划后重建请求、消费人工响应并逐项比较；不会调用模型。原 fixtures 中的随机草案 ID 每次运行可能变化，同一冻结输入的计划与请求保持确定性。

精确 P1 四项既有失败的独立复现，以及只读源文件边界检查：

```sh
/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_execution_plan.run_offline --output-dir experiments/geographic_execution_plan/artifacts/p1-baseline-rerun-01 --baseline-ref 031e0cd26379ee44c2372c9a28536c377ab3d4b9
/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_execution_plan.verify_sources --output experiments/geographic_execution_plan/artifacts/source-audit-rerun.json
```

基线复现只用 `git archive` 的 P1 应用与测试临时副本；P2 仅提供防误调用 guard。它不会重置工作树。没有新增依赖，也没有独立 lint/typecheck 脚本可报告；实际静态检查为 Python 编译、原文件摘要、导入扫描及空白检查。

主要入口：

- `scope_search_plan.py`：纯计划构造、外部权威重算校验、严格序列化/恢复。
- `authority.py`：仅从隔离真实 P1 stores 捕获 fixture；验证消息/UI 操作、revision/复制链、历史采用凭据、资源与统一设置。
- `frozen_risk.py`：只读冻结输入适配，通过原 `terms_for()` 维持词条与分组关联；原评分引擎保持原样。
- `p3_bridge.py`：固定 names-v2 `prepare_request` / `decision`，独立实验资格断言；没有选帖、额度或精采实现。
- `storage.py`：实验内容寻址 blob 与计划保存/恢复；缺内容或摘要/身份不符失败，不补读最新资源。

地域与搜索词独立。`query == source_term == 已确认搜索词`，同帖各搜索词的风险上下文保持独立。搜索词默认上限仍为 10，计划校验兼容 P1 原有 1–200 的合法配置范围，没有增加实际选词。风险冻结使用原引擎实际消费的 `prompt_profile_snapshot`，结构化规则另保留；两项缺陷修复见 [FIX-REVIEW](FIX-REVIEW-20261015.md)。旧失败轮次的计划仅留作历史证据，当前可回放计划使用 REPORT 中的修复后完整轮。

后续 P4 消费两份结果，按实际选中词预留额度、精采成功结算，失败释放；本轮不提前指定帖子 owner。
