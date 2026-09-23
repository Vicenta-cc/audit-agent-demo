# 每词十选一（Triage select）

## 模式
- `TRIAGE_MODE=off`：默认，采集逻辑与现状一致（每词取搜索排第一）。
- `select`：每词采 10 条文本候选，规则加 flash 打分，选最可疑的 1 条按 ID 补采媒体与评论，再精审。初筛只依据平台的两份基础数据——黑话库与判定规则。规则层只使用任务所属词库的条目（精确、模糊、正则），命中一处 +300，本次搜索词自身的命中不计（搜出来的帖子必然含搜索词），只剩自命中的候选走模型判断；词库按任务全部 library_ids 加载，与搜索词来源无关（关键词任务配了 library_ids 时同样生效）。需要「加微信、看主页」这类引流用语参与初筛时，在词库里以正则或模糊条目维护，不在代码里写死。模型层的提示词由任务分类的判定规则（审核目标与证据规则）构成，按其中的高危/中危/低危/放行定义答 strong/weak/none。初筛先做身份丢弃：官方与机构类蓝V（媒体、政务、公安、事业单位等，按 `TRIAGE_OFFICIAL_VERIFY_PATTERNS` 识别）一律不进精审；商家/企业认证按普通账号打分，只受粉丝阈值约束；个人黄V粉丝超过 `TRIAGE_MAX_FOLLOWERS_PERSONAL_VERIFIED`、无认证账号（含商家/企业蓝V）粉丝超过 `TRIAGE_MAX_FOLLOWERS_UNVERIFIED` 的不进精审；丢弃的候选记录在 candidates.json 的 band=discard。候选采集时对每个作者补一次资料请求（粉丝数、签名、认证），同一作者只请求一次；粉丝阈值与签名命中依赖它。任务日志的「初筛判定规则：<prompt_version>」一行记录本次用的是哪一版判定规则，任务没带判定规则时写「未提供」，提示词里就没有规则段。
- `compare`：每个词随机选用"排第一"或"初筛选中"策略，记录在 candidates.json 的 strategy 字段，用于对照验证。

## 证据
- `outputs/{job}/crawler/candidates/{序号}-{词}/candidates.json`：每条候选的分数、命中、模型输出、排序、是否选中、策略。
- 若任务中途切换了采集账号，候选目录位于 `outputs/{job}/crawler/rotation-{账号}/candidates/...`，对照报告脚本会一并读取。

## 对照报告
`python -m scripts.triage_compare_report <job_id> [...]`，看 `lift`（初筛组 reject 率 ÷ 排第一组 reject 率，目标 ≥ 1.5）。

## 已知限制

- **爬虫表结构**：爬虫 `douyin_aweme` 行新增 `custom_verify`、`enterprise_verify_reason`、`follower_count`、`verification_type`、`max_follower_count` 五个字段。本平台用 jsonl 存储不受影响；若某个环境用爬虫的 db/sqlite/postgres 存储且已有旧表，需手动 `ALTER TABLE` 加这五列，`create_all` 不会给旧表加列。

- **任务采集上限**：一次任务最多采多少条由请求参数 `max_total_notes` 决定（当前契约上限 5），它同时决定 `analyze_limit`。词数超过上限时，靠后的词不会被搜索，任务日志里有「已达本任务采集上限 N 条，以下词未搜索：…」一行列出这些词。
- **精采不完整**：补采媒体或评论的子步骤失败（`CrawlerCollectionIncompleteError`）与 `TRIAGE_MODE=off` 时一致，整个任务失败并提示查看 `douyin/collection_status`；不会把已经流式入库的半条内容当成「本词无产出」。
- **恢复采集与切换账号**：按 `candidates.json` 的 `collected` 标记跳过已经精采成功的词，所以续采或换账号重跑不会让同一个词出两条。非流式入库配置下（`STREAM_CRAWL_ANALYSIS=false`），切换账号前采到的内容不会被再次读取，也不会再次采集，因此会从本次任务产出中丢失；默认的流式入库配置不受影响。
- **作者资料请求失败**：该候选按搜索结果里的作者信息打分（粉丝数为 0、没有签名），不会让整个词失败。

## 回滚
`TRIAGE_MODE=off` 并重启。无数据结构变更。

## 纠错参数 A/B（待执行）

抖音搜索默认开启查询纠错（`query_correct_type=1`），可能把黑话词纠成常规词。在配置好采集账号 Profile 的环境执行：

```bash
python -m scripts.douyin_query_correct_ab --category gambling --account-id <账号ID> --limit 10
```

脚本对每个词各跑一次"开启纠错"和"关闭纠错"，输出到 `ab-query-correct/report.json`，每个词含 `on_count`、`off_count`、`overlap`、`only_on`、`only_off`、`on_rule_hits`、`off_rule_hits`。

判定规则：若关闭纠错后词库命中总数（`off_rule_hits` 之和）高于开启时，且没有词变成 0 结果，则把应用侧 `run_search` 的 `query_correct_type` 默认传 0；否则维持 1。结论出来前默认值保持 1。
