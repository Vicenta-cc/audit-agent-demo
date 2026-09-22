# 每词十选一（Triage select）

## 模式
- `TRIAGE_MODE=off`：默认，采集逻辑与现状一致（每词取搜索排第一）。
- `select`：每词采 10 条文本候选，规则加 flash 打分，选最可疑的 1 条按 ID 补采媒体与评论，再精审。
- `compare`：每个词随机选用"排第一"或"初筛选中"策略，记录在 candidates.json 的 strategy 字段，用于对照验证。

## 证据
- `outputs/{job}/crawler/candidates/{序号}-{词}/candidates.json`：每条候选的分数、命中、模型输出、排序、是否选中、策略。
- 若任务中途切换了采集账号，候选目录位于 `outputs/{job}/crawler/rotation-{账号}/candidates/...`，对照报告脚本会一并读取。

## 对照报告
`python -m scripts.triage_compare_report <job_id> [...]`，看 `lift`（初筛组 reject 率 ÷ 排第一组 reject 率，目标 ≥ 1.5）。

## 回滚
`TRIAGE_MODE=off` 并重启。无数据结构变更。

## 纠错参数 A/B（待执行）

抖音搜索默认开启查询纠错（`query_correct_type=1`），可能把黑话词纠成常规词。在配置好采集账号 Profile 的环境执行：

```bash
python -m scripts.douyin_query_correct_ab --category gambling --account-id <账号ID> --limit 10
```

脚本对每个词各跑一次"开启纠错"和"关闭纠错"，输出到 `ab-query-correct/report.json`，每个词含 `on_count`、`off_count`、`overlap`、`only_on`、`only_off`、`on_rule_hits`、`off_rule_hits`。

判定规则：若关闭纠错后词库命中总数（`off_rule_hits` 之和）高于开启时，且没有词变成 0 结果，则把应用侧 `run_search` 的 `query_correct_type` 默认传 0；否则维持 1。结论出来前默认值保持 1。
