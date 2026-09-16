# 抖音 M3 公共执行能力候选基线交接（2026-09-16）

## 1. 本文用途与当前结论

本文用于接续本次长窗口中的设计决策、代码现状、真实验证证据和后续验收任务。

> 状态更新：本文主体保留铆钉前现场。代码审核、临时项排除和候选门禁随后已完成；候选提交由标签 `douyin-m3-candidate-20260916` 标识，详细结果见 `docs/douyin-m3-candidate-gate-20260916.md`。该标签仍不代表主界面验收或正式晋升完成。

当前结论：

- 已找到此前大量账号触发验证的主要原因：只迁移 Cookie、重新创建 profile/seed，会改变账号的设备身份；完整持久 profile 或在稳定新 profile 中重新扫码登录可以正常采集。
- 实验性的“页面 DOM 搜索”方向已全部撤回，当前继续使用原有 MediaCrawler 搜索链路。
- M3 与非 M3 的底层执行差异已按“小范围收敛”方向修复：公共账号轮换、失败分类、恢复、采集/分析控制均不再由 M3 开关决定。
- 新建的 `zc不常用账号` 持久 profile 已完成“扫码登录 → 重开 profile → MediaCrawler 直测 → 8199 API Job”四阶段验证，真实抓取并入库 1 条。
- 当前代码已完成候选审核和铆钉；修复后的主界面全链路验收仍未完成，不能直接晋升为正式基线。

## 2. 基线与环境边界

### 2.1 正式冻结环境（不得修改）

- 代码提交：`2134c7b53c572b9981a0cbd9a2a281ed7ca6eb3a`
- 分支：`codex/unified-frozen-20260913`
- 标签：`unified-baseline-20260913`
- 代码目录：`/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-unified-frozen-20260913`
- 正式运行目录：`/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-formal-20260911`
- 正式数据库：`/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-formal-20260911/data/audit_index.sqlite3`
- 正式前端：`127.0.0.1:3198`，当前正在监听。
- 正式后端：`127.0.0.1:8198`，当前正在监听。
- 原正式冻结提交 `f3ea3b0` 没有被覆盖。

本轮没有把开发代码、配置、账号 profile 或数据库写入正式环境。

### 2.2 当前候选开发环境

- 代码目录：`/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-douyin-ops-controls-20260916`
- 分支：`codex/douyin-ops-controls-20260916`
- 当前 HEAD：`a925969d78e570bbd99413dfa2e2b0d5283811da`
- 隔离运行目录：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/douyin-ops-controls-20260916`
- 隔离数据库：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/douyin-ops-controls-20260916/data/audit_index.sqlite3`
- 隔离后端：`127.0.0.1:8199`，当前正在监听。
- 隔离前端：`127.0.0.1:3199`，当前**没有监听**；浏览器中保留旧页面不代表服务仍在运行。

当前工作树有约 51 个已跟踪文件发生修改，并有多份新增文件。所有改动仍未提交，因此 `a925969d...` 不是本轮成果的固定提交。

## 3. 已确认的设计边界

### 3.1 保留 M3，不重做架构

M3 继续负责：

- 对话理解和调查草稿；
- 用户确认后创建任务；
- 幂等创建和配置一致性；
- Investigation / Run / Job / 报告关联；
- 恢复后维持原调查语义和结果归属。

公共执行层统一负责：

- 账号选择和可用账号池遍历；
- 登录失效、平台验证、限流的分类；
- cooldown、换号和账号状态更新；
- checkpoint、采集、入库和边采边分析；
- 采集与分析的暂停、继续、停止和恢复。

核心原则：**M3/非 M3 不得成为底层能力开关。**

### 3.2 统一设置与任务执行快照

用户在业务入口下设置统一采集/分析参数，后续新任务默认读取这组参数。

任务在用户确认启动时冻结一份 execution snapshot。运行中的任务继续使用自己的快照，不因全局设置后续变化而改变行为。账号切换记录、checkpoint 和进度属于运行状态，不改写原始参数快照。

当前隔离环境统一设置为：

```yaml
revision: 7
start_page: 1
max_notes: 2
max_comments: 1
collect_comments: true
get_sub_comment: false
collect_media: true
max_items_per_minute: 1
max_concurrency: 1
auto_analyze: true
analyze_limit: 2
analysis_batch_size: 1
```

### 3.3 FAILED 恢复规则

“继续采集”不是新建任务，也不应另写一套账号重试逻辑。正确流程是：

```text
原任务发生可恢复的采集失败
→ 用户点击继续采集
→ 读取原 Run / Job / execution snapshot / checkpoint
→ 重新进入公共采集流程
→ 遍历本轮当前可用账号池，每个可用账号最多尝试一次
→ 成功则继续原任务，全部不可用则本轮再次 FAILED
```

当前采集阶段的结构化可恢复失败包括：

- `crawler_account_verification_required`
- `crawler_account_login_required`
- `crawler_rate_limited`

逻辑语义为：

```ini
stage = crawl
recoverable = true
recovery_action = continue_crawl
```

并非所有 `FAILED` 都能“继续采集”；是否可恢复取决于失败阶段和恢复动作，而不是只判断一个错误字符串。

## 4. 本轮已经完成的代码能力

- 新增业务级统一任务设置，并在确认启动时记录执行快照。
- M3 和非 M3 接入相同的账号选择、失败分类、冷却和完整可用账号池轮换。
- 可恢复的 M3 `FAILED` 调查能够衔接回原 Run / Job，而不是另建任务。
- 采集阶段和分析阶段分别记录状态，避免“停止采集”必须等待分析结束后才更新。
- 补充暂停/继续采集、暂停/继续分析、重启恢复和 checkpoint 相关逻辑。
- 修正恢复采集后的唯一入库计数，避免重复经过批次导致页面多算。
- 修正任务详情旧快照、状态标签和日志中的本地路径泄露问题。
- 抖音页码完成边界换算：界面和快照保留用户可读的第 1 页，传给爬虫时转换为第 0 页。
- 登录流程支持在隔离环境中通过 `CRAWLER_LOGIN_HEADED=true` 打开有头 CloakBrowser；正式默认仍为 `false`。
- 实验性的 CloakBrowser 页面 DOM 搜索改动已经撤回，未进入当前候选代码。

## 5. 爬虫与 profile 的关键结论

### 5.1 当前使用的爬虫代码是干净版本

以下三处 `media_platform/douyin/client.py` 的 SHA-256 一致：

```text
8e857005dded68d3fffbce497a931e013d20f8099d2b9371eb7532355f040ccb
```

- 当前 MediaCrawler 源码：`/Users/ext.wanghongtao6/Documents/Codex/projects/MediaCrawler-douyin-v2-pagination-dedupe-20260914`
- 旧 8027 成功验收爬虫：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/collection-reliability-live-20260915/crawler`
- 当前 8199 隔离爬虫：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/douyin-ops-controls-20260916/crawler`

因此当前候选环境没有残留“页面 DOM 搜索”生产实现。

### 5.2 profile 不是只含 Cookie

已通过对照确认：

```text
旧成功代码 + 旧持久 profile + 同一账号 → 成功
新建 profile + 注入旧 Cookie              → 容易触发 verify
稳定的新 profile 中重新扫码登录           → 成功
```

profile/seed 应视为账号的持久设备身份。正常维护方式是：

- 一个账号长期绑定一个稳定 profile 和 seed；
- 登录或重新登录时，在这个 profile 内用有头浏览器扫码；
- 登录后关闭并重开同一 profile；
- 后续正常任务可继续无头运行；
- 不通过“新建指纹后仅注入旧 Cookie”更新账号；
- 不随意删除、重建或换 seed。

## 6. 账号与真实验证证据

### 6.1 `zc常用账号`

- 账号 ID：`e0b04e4d2faa`
- 使用从 8027 完整迁移的已验证 profile 副本。
- seed：`10000`
- CloakBrowser 版本：`145.0.7632.109.2`
- 旧代码＋旧 profile＋有头模式曾成功抓取“维汉夫妻”1 条。
- 当前仍为 active；历史测试留下了已过期 cooldown / verify 字段，可后续只做展示数据清理，不影响本轮架构结论。

### 6.2 `zc不常用账号`

- 账号 ID：`ad4cdc1b4f04`
- 使用全新、稳定的独立 profile。
- seed：`10003`
- CloakBrowser 版本：`145.0.7632.109.2`
- 没有注入旧 Cookie；通过隔离 8199 的有头登录流程重新扫码。

该账号已完成以下连续验证：

1. 有头扫码登录成功；
2. 关闭并重开同一 profile，网页搜索“维汉夫妻”返回 9 条唯一内容且没有 verify；
3. 使用干净 MediaCrawler、无头、最多 1 条、关闭评论/媒体/分析，成功抓取 1 条；
4. 重启 8199 后通过实际 API 创建 Job `b40070e3e290`；
5. Job 使用 `zc不常用账号` 完成采集并入库 1 条，状态为 crawl completed，无错误、无 cooldown。

本次内容：

```text
aweme_id: 7685934289930433747
标题: #刚睡醒的状态 #小日常记录 #维汉夫妻 #重庆老公
```

证据文件：

- API Job 输出：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/douyin-ops-controls-20260916/data/outputs/b40070e3e290/crawler/douyin/jsonl/search_contents_2026-09-16.jsonl`
- MediaCrawler 直测输出：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/douyin-ops-controls-20260916/data/outputs/zc-uncommon-fresh-profile-probe-20260916/douyin/jsonl/search_contents_2026-09-16.jsonl`
- 登录前备份：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/douyin-ops-controls-20260916/profile-onboarding-backup-20260916-1750`

### 6.3 关于 active 的语义

`active` 表示账号的登录/profile 状态可以参与任务选择，不表示该账号对任意关键词都一定不会触发平台验证。

不要在登录完成后强制用某个固定关键词抓取 1 条才标记 active。关键词结果和平台验证具有场景性，不能用一次关键词探测定义账号的永久可用性。

### 6.4 暂不使用的账号

`dk账号`、`wht抖音`此前只迁移了数据库认证信息并创建了新 profile，实测多次触发 verify。当前用户手边只有 `zc常用账号` 和 `zc不常用账号`，后续主界面验收只使用这两个账号，除非用户另行指定。

## 7. 已有测试与验收覆盖

### 7.1 自动化测试

最近一次针对登录与恢复相关的定向测试结果：

```text
25 passed, 3 skipped
```

覆盖：

- `tests/test_crawler_login_manager.py`
- `tests/test_crawler_browser_integration.py`
- `tests/test_crawl_resume_control.py`
- `tests/test_pipeline_verify_rotation.py`

较早的 M3 执行一致性门禁记录为：

```text
353 passed
23 frontend subtests passed
TypeScript / Vite build passed
```

全量测试曾出现：

```text
1266 passed, 28 skipped, 8 failed, 11 errors
```

失败/错误已记录为外部环境、路径或归档夹具相关，但这意味着不能写成“全量回归全部通过”。正式晋升前应再次运行并分类。

### 7.2 旧诊断页面验收

`/tasks` 只作为内部诊断页面，不是最终产品验收入口。

旧 Job `daa0dc99f769` 曾真实验证：

- 暂停分析后采集继续；
- 暂停采集后分析继续；
- 重启后任务和已入库内容恢复；
- 最终数据库保留 2 条唯一内容、2 条评论。

但当时没有产出完整审核和最终报告，且“采集停止状态受分析拖住”的缺陷是在该轮之后修复，因此这份记录只能作为历史证据，不能替代修复后的主界面验收。

### 7.3 先前 M3 主界面尝试

- Investigation Run：`investigation-run:87efa74ae4684f9ba35c024e25046fe0`
- Job：`m3-843f98377f5d847571b2`

该轮证明了 M3 对话创建/确认链路和四账号池遍历可以执行，但当时 profile 迁移方式错误，平台 verify 导致 0 条内容、没有报告。现在 profile 问题已得到新的成功证据，需要重新进行主界面验收。

## 8. 目前还不能宣称完成的事项

- 修复 profile 后，尚未从 3199 主界面完成一次完整 M3 调查。
- 尚未在主界面真实复验采集与分析的独立暂停、继续和停止。
- 尚未在修复后任务中间重启 8199，并验证同一 Investigation / Run / Job / checkpoint 延续。
- 尚未获得至少一条完整审核结果和最终调查报告。
- 尚未验证“已完成审核结果在恢复/重启后不会重复审核”。
- 当前采集与分析是同一后端进程内的逻辑解耦，不是两个独立服务；本轮目标不包含服务拆分。

## 9. 下一窗口应按顺序完成的任务

### 任务 1：审核并铆钉候选代码（已完成）

1. 审核当前全部已跟踪和新增文件，排除临时探测代码、输出、Cookie、二维码、绝对 profile 数据等不应入库内容。
2. 再次确认三份爬虫 `client.py` 哈希一致，不含 DOM 搜索实验。
3. 运行后端定向门禁、执行一致性测试、前端测试和构建。
4. 对全量测试失败项逐项分类并记录；若出现本轮新增回归，先修复。
5. 更新实现和验收文档。
6. 将确认后的改动提交为一个可复现候选提交，并以 `douyin-m3-candidate-20260916` 标记；提交号由标签和候选控制器 receipt 共同记录。

### 任务 2：恢复隔离前端

使用 Codex 已固定的 Node/pnpm 绝对路径构建和启动 3199，不使用正式 `formal-runtime.sh`，不改 3198/8198。

启动后先确认：

- 3199 指向 8199；
- 页面显示的统一设置是 revision 7；
- 账号页能看到 `zc常用账号` 和 `zc不常用账号`；
- `zc不常用账号` 的实际 profile/seed 未被重建。

### 任务 3：从主界面完成小量真实验收

只使用主界面 `/investigation`，关键词固定为“维汉夫妻”，禁止使用“盘口”。

建议：

- 最多抓取 2 条；
- 每分钟 1 条；
- 并发 1；
- 每帖最多 1 条评论；
- 分析批次 1；
- 首选 `zc不常用账号`，`zc常用账号`作为备用。

完整操作：

1. 通过对话新建调查；
2. 检查确认卡显示统一参数；
3. 用户确认并启动；
4. 核对任务使用确认时的 execution snapshot；
5. 分析运行时暂停采集，确认分析仍继续；
6. 恢复采集；
7. 采集运行时暂停分析，确认采集仍继续；
8. 恢复分析；
9. 避免为了制造换号而密集请求；若自然发生 verify，核对 cooldown 和完整可用账号池轮换。

### 任务 4：重启恢复验收

在任务仍有未完成内容时重启**隔离后端 8199**，验证：

- 原 Investigation、Run 和 Job ID 不变；
- 原 execution snapshot 不变；
- checkpoint 和已入库内容保留；
- 继续采集重新进入公共账号池流程；
- 不重复入库同一内容；
- 已完成审核不重复执行；
- 页面状态、接口状态和数据库状态一致。

### 任务 5：完成审核与报告闭环

至少让 1 条内容完整完成审核，并确认：

- 分析结果归属于原 Job；
- 调查状态正确推进；
- 最终报告挂到原 Investigation；
- 刷新或重启后报告仍能打开；
- 再次点击继续/恢复不会重复生成已完成审核结果。

### 任务 6：形成最终验收结论

交叉核对主界面、8199 API、SQLite、运行日志和 JSONL 输出，记录：

- Investigation / Run / Job ID；
- 账号选择及切换记录；
- 抓取、唯一入库、评论和分析数量；
- 暂停/继续/重启时间点；
- 最终报告状态；
- 是否有残留爬虫或 CloakBrowser 进程。

只有上述主界面闭环通过，才讨论把候选提交晋升为正式基线。

## 10. 主界面验收通过标准

- [ ] 对话草稿不会在未确认时误启动任务。
- [ ] 确认后只创建一个 Investigation / Run / Job。
- [ ] 任务使用确认时冻结的统一参数快照。
- [ ] “维汉夫妻”真实抓取 1～2 条并唯一入库。
- [ ] 暂停采集不阻塞分析状态推进。
- [ ] 暂停分析不阻塞采集状态推进。
- [ ] 继续采集从原 checkpoint 进入公共流程。
- [ ] 可恢复 FAILED 使用原 Run / Job，不新建任务。
- [ ] 当前可用账号池按策略遍历，不只尝试一个备用账号。
- [ ] verify、登录失效和限流没有互相误标。
- [ ] 重启 8199 后原调查链路完整恢复。
- [ ] 已入库内容和已完成审核不重复。
- [ ] 至少 1 条审核完成并生成原调查的最终报告。
- [ ] 页面、API、数据库、日志和输出数量一致。
- [ ] 日志不泄露本地敏感路径、Cookie 或登录信息。
- [ ] 正式 3198/8198 全程未改变。

## 11. 明确禁止事项

- 不在正式 3198/8198 上试验或替换代码。
- 不把 `/tasks` 的结果当作主界面产品验收结论。
- 不使用“盘口”作为验收关键词。
- 不重新引入页面 DOM 搜索实验。
- 不通过“新 profile + 注入旧 Cookie”伪造账号迁移。
- 不删除或重建已验证的 `zc常用账号`、`zc不常用账号` profile/seed。
- 不在未获得用户新指示时使用 `dk账号`、`wht抖音`继续实网请求。
- 不为了测试轮换而进行高频、无限或无上限重试。
- 不把本轮扩大为服务拆分或整体架构重写。

## 12. 相关记录

- `docs/douyin-ops-controls-implementation-20260916.md`：早期实现记录，部分结论已被后续修复覆盖。
- `docs/douyin-ops-controls-live-acceptance-20260916.md`：旧 `/tasks` 诊断页面真实验收记录。
- `docs/m3-execution-parity-20260916.md`：M3/非 M3 执行一致性改动和先前主界面尝试。
- `docs/report-multipost-qa-account-penetration-handoff-20260916.md`：1～5 帖报告、混合风险问询和跨报告账号穿透的专项审计与实施步骤。该专项通过前，不应把当前检查点提升为最终候选标签。
