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
