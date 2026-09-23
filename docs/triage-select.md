# 每词十选一（Triage select）

## 模式
- `TRIAGE_MODE=off`：默认，采集逻辑与现状一致（每词取搜索排第一）。
- `select`：每词采 10 条文本候选，规则加 flash 打分，选最可疑的 1 条按 ID 补采媒体与评论，再精审。规则层不把本次搜索词自身的命中计分（搜出来的帖子必然含搜索词），只计其他词库词、变体与导流模板；搜索词自身命中的候选走模型判断。
- `compare`：每个词随机选用"排第一"或"初筛选中"策略，记录在 candidates.json 的 strategy 字段，用于对照验证。

## 证据
- `outputs/{job}/crawler/candidates/{序号}-{词}/candidates.json`：每条候选的分数、命中、模型输出、排序、是否选中、策略。
- 若任务中途切换了采集账号，候选目录位于 `outputs/{job}/crawler/rotation-{账号}/candidates/...`，对照报告脚本会一并读取。

## 对照报告
`python -m scripts.triage_compare_report <job_id> [...]`，看 `lift`（初筛组 reject 率 ÷ 排第一组 reject 率，目标 ≥ 1.5）。

## 已知限制

- **任务采集上限**：一次任务最多采多少条由请求参数 `max_total_notes` 决定（当前契约上限 5），它同时决定 `analyze_limit`。词数超过上限时，靠后的词不会被搜索，任务日志里有「已达本任务采集上限 N 条，以下词未搜索：…」一行列出这些词。
- **精采不完整**：补采媒体或评论的子步骤失败（`CrawlerCollectionIncompleteError`）与 `TRIAGE_MODE=off` 时一致，整个任务失败并提示查看 `douyin/collection_status`；不会把已经流式入库的半条内容当成「本词无产出」。
- **恢复采集与切换账号**：按 `candidates.json` 的 `collected` 标记跳过已经精采成功的词，所以续采或换账号重跑不会让同一个词出两条。非流式入库配置下（`STREAM_CRAWL_ANALYSIS=false`），切换账号前采到的内容不会被再次读取，也不会再次采集，因此会从本次任务产出中丢失；默认的流式入库配置不受影响。

## 回滚
`TRIAGE_MODE=off` 并重启。无数据结构变更。

## 纠错参数 A/B（待执行）

抖音搜索默认开启查询纠错（`query_correct_type=1`），可能把黑话词纠成常规词。在配置好采集账号 Profile 的环境执行：

```bash
python -m scripts.douyin_query_correct_ab --category gambling --account-id <账号ID> --limit 10
```

脚本对每个词各跑一次"开启纠错"和"关闭纠错"，输出到 `ab-query-correct/report.json`，每个词含 `on_count`、`off_count`、`overlap`、`only_on`、`only_off`、`on_rule_hits`、`off_rule_hits`。

判定规则：若关闭纠错后词库命中总数（`off_rule_hits` 之和）高于开启时，且没有词变成 0 结果，则把应用侧 `run_search` 的 `query_correct_type` 默认传 0；否则维持 1。结论出来前默认值保持 1。
