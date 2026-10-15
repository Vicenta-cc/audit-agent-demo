# P0 减少重复确认版本验收交接

更新日期：2026-10-15。状态：**保留减少重复确认版本，地域识别与草案落地结果已整理、待原会话验收；P0 未整体通过。**

用户本次选择的是已完成真实评测的 `geo-intent-p0-bridge-minimal-v1` 的 **`confirmation`** 候选。保留原版循环、完整工具/schema及该候选提示的冻结字节，本次仅整理文档与选择清单，新增真实模型请求 **0 次**。未删除其他候选及失败证据，未更改提示、评分、样本或默认参数。

## 用户目标与结果

用户核心目标是：明确要求调查某地区舆情时，模型正确理解地域，并在具备配置条件时把地域写入草案。以下均为隔离模拟草案，未验证帖子筛选、真实采集、正式卡片展示或生产稳定性。

| 验收问题 | 分子 / 分母 | 结果及口径 |
|---|---:|---|
| 地域设置要求是否识别正确 | 精确率 7/7；召回率 7/7 | 均 100%；七条 set 的目标、原话来源、策略通过原自动判分与人工复核 |
| 具备配置条件时是否写入正确地域 | 5/5 | 100%；含创建和修改；以实际 configure 参数及成功模拟回执核实 |
| 缺资源时是否保留地域意图并暂缓创建 | 2/2 | 两条已记录 set，不生成占位草案；这两条仍在七条识别及十八条总样本分母内 |
| 用户要求取消地域时是否清除 | 1/1 | 成功写入 scope=null，保留资源引用 |
| 所有具备配置条件案例是否实际调用配置并成功 | 13/14 | 92.86%；包括没有地域要求的案例；不能称为地域识别成功率 |
| 严格实验记录是否完整且正确 | 原自动 15/18；人工 15/18 | 83.33%；三条漏记录继续记失败，不以行为正确补分 |
| 原完整案例评分 | 自动 15/18；人工离线复核 17/18 | 两条模拟声明误伤仅离线说明；原判及第四轮终止失败保留，不等于草案完成率 |

七条地域设置正例逐项如下，可落地五条没有漏写地域：

| 案例 | 结果 |
|---|---|
| city-kelamayi | 已写入正确地域 |
| only-kelamayi | 已写入正确地域 |
| change-daqing | 已写入正确地域 |
| adoption-temp-later | 已写入正确地域 |
| province-xinjiang | 已写入正确地域 |
| defer-missing-all | 地域已记录；缺资源，按要求暂缓草案 |
| adoption-temp-first | 地域已记录；缺资源，按要求暂缓草案 |

`keep-rules` 是 13/14 中唯一未配置案例：用户指定的规则引用已与草案绑定相同，模型读取后保持原状，为合理 no-op；但不能把 no-op 算作“实际配置成功”，13/14 原分母与分子不变。

## 没有设置地域、修改与歧义

八条 keep 案例中，既有地域正确保留（keep-denial-clear、keep-rules、keep-title）；新一轮全国调查、含地名的资源名、账号背景、被否定举例、助手推荐均未被擅自设为地域（new-round-national、named-resource、creator-kelamayi、denied-city、assistant-city）。八条中的七条实际配置成功，一条合理 no-op，不另造总成功率。

`ambiguous-chaoyang` 先澄清，不猜测朝阳对应地区，不配置草案；`pasted-post` 仅转发且无任务，不配置。此候选未出现错误 set/clear、实际错误地域写入、越界工具尝试或虚假真实执行声明；这些结论不替其他候选背书。

## 保留的缺口

- `keep-rules`、`keep-title`、`pasted-post` 缺少实验观察记录：是模型未遵守记录工具契约，严格评分仍失败；其实际地域处理经人工核实正确。记录工具用于区分理解与承接，不是用户草案目标本身。本次不移除工具、不降低原验收标准，交原会话决定后续是否将它列为可选诊断。
- `named-resource` 第四次响应已配置全国模拟草案，scope=None，但没有最终文本回复，原 termination=loop_limit 及完整案例失败保持。草案成功与回复结束分开报告。正式卡片展示未测试，不据此宣称卡片已验证。
- 两处原完整判分误伤（adoption-temp-later、denied-city）只列人工离线解释，不改原自动分数，不重跑模型。

## 全部十八例复核索引

| 案例 | 标注决定 | 实际配置 | 严格地域记录人工判分 | 对话结束 |
|---|---|---|---|---|
| city-kelamayi | set | 是 | 通过 | 已结束 |
| only-kelamayi | set | 是 | 通过 | 已结束 |
| keep-denial-clear | keep | 是 | 通过 | 已结束 |
| change-daqing | set | 是 | 通过 | 已结束 |
| adoption-temp-later | set | 是 | 通过 | 已结束 |
| province-xinjiang | set | 是 | 通过 | 已结束 |
| keep-rules | keep | 否 | 失败（缺记录） | 已结束 |
| defer-missing-all | set | 否 | 通过 | 已结束 |
| adoption-temp-first | set | 否 | 通过 | 已结束 |
| ambiguous-chaoyang | clarify | 否 | 通过 | 已结束 |
| new-round-national | keep | 是 | 通过 | 已结束 |
| named-resource | keep | 是 | 通过 | 未结束：第四轮配置成功 |
| pasted-post | no_task | 否 | 失败（缺记录） | 已结束 |
| creator-kelamayi | keep | 是 | 通过 | 已结束 |
| denied-city | keep | 是 | 通过 | 已结束 |
| clear-unlimited | clear | 是 | 通过 | 已结束 |
| keep-title | keep | 是 | 失败（缺记录） | 已结束 |
| assistant-city | keep | 是 | 通过 | 已结束 |

每例原话、来源、策略、工具参数和模拟回执的详细理由保留在原逐例复核及 REPORT 的 confirmation 表中；本交接不对其他候选优点作拼接。

## 与历史原桥接对照

| 指标 | 原桥接首次历史结果 | 原桥接最近复测 | 本次保留 confirmation |
|---|---:|---:|---:|
| set 识别精确率 / 召回率 | 7/7 / 7/7 | 5/5 / 5/7 | 7/7 / 7/7 |
| 可落地 set 正确写入 | 4/5 | 5/5 | 5/5 |
| clear 实际清除 | 1/1 | 0/1 | 1/1 |
| 实际配置成功 | 11/14 | 8/14 | 13/14 |
| 严格记录正确 | 16/18 | 11/18 | 15/18 |

地域写入 5/5 与最近原桥接复测相同；本轮观察到的改善主要是清除承接及整体草案配置。历史轮次不是同时随机对照，存在超时与模型波动，不能将全部差异归因为提示变更，不能宣称稳定提升。

## 版本、运行与证据

- HEAD：`a4997d3969beab3d499a218e7a7aee025fea46d9`；分支 `codex/geographic-relevance-p0`。
- 选择方式：调用 `build_request(..., variant='confirmation')` / `run_candidate(..., variant='confirmation')`，明确指定候选。冻结 API 的默认值仍为 combined，**不要把省略 variant 的调用当作已选版本**。保留冻结代码，未为选择候选而改默认值。
- 冻结清单 SHA256：`13687cc375f360323b7bc725eca8445eb53d492b35f62c4cfd65d74eb4bc88db`。原 31 文件、新候选 4 文件及运行包装脚本已重新核对，无漂移；未刷新旧快照。
- 模型：`deepseek-v4-pro-ga-260813`；provider：`https://api.qhose.net/v1`。配置来源为既有私有 JSON，仅使用 CONVERSATION 所需配置；不在本文复制凭据。
- 选中组：18 条既有合成 dev，50 请求 / 50 响应，运行失败 0，无自动重试；每例最多 4 次模型响应、12 次工具，单次超时 45 秒。全部 50 响应及 41 工具请求/模拟回执已逐例复核。
- 本组运行状态耗时 836.71 秒，均摊约 46.48 秒/例；这是整例流程耗时，非草案生成或卡片出现的直接耗时。当时四组并行，不当作正式性能基准。
- 已返回 usage：prompt 1,259,848 / completion 7,803 / total 1,267,651 tokens；费用未知。
- 本地框架：既有 435 项针对性测试（含45项新增）及25项回归通过，证据不变。本次仅改文档与保存选择清单，未改代码，不重复无关测试；重新做冻结哈希、原始结果及指标一致性检查。
- 四候选历史总计 194 请求 / 190 响应 / 4 超时；项目累计 502 请求 / 495 响应不变。本次新增调用 0；没有为选中版本再跑 holdout。

证据绝对路径：

- [freeze](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/freeze.json)
- [results](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/confirmation/results.json)
- [review](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-minimal-v1-confirmation-review.json)
- [ledger](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/confirmation/http-ledger.jsonl)
- [status](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/confirmation/status.json)
- [summary](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-minimal-v1-summary.json)
- [previous_audit](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-minimal-v1-audit.json)
- [targeted_tests](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-minimal-v1-targeted-final.txt)
- [regression_tests](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-minimal-v1-regression-final.txt)
- [本次选择与保护清单](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/confirmation-selection-20261015.json)

历史可复现入口为 `outputs/geographic-scope-intent/bridge-minimal-v1/run.py --variant confirmation`，包装脚本的真实模式仍需显式开关及授权标签；原输出禁止覆盖，本次不执行。原评测完整命令保留在 REPORT 四候选章节。

## 验收停止点

34 条原样本全部为合成对话，本次重复使用其中 18 条 dev，可落地 set 只有 5 条；即使 5/5 也不能证明生产识别稳定。严格记录 15/18 未达到 95% 建议门槛，选中候选没有新增独立留出验证；不能把选择候选当作 P0 整体通过。

未改正式业务、卡片、数据库、worker 或 MediaCrawler，未提交、推送、部署或进入 P1。保留其他候选、历史失败和他人修改。此文件供用户转交原验收会话，不自动向其他会话发送消息。

可转交摘要：按用户选择保留冻结版本 geo-intent-p0-bridge-minimal-v1/confirmation（减少重复确认），本次新增真实请求0。该组既有18dev、50请求50响应、无重试或运行失败；set精确率7/7、召回率7/7，其中具备配置条件的5/5正确写入模拟草案，另2例因资源缺失正确暂缓；clear落地1/1，整体实际配置13/14。严格记录仍15/18，三条缺记录未豁免；一条第四轮配置成功但缺最终回复，原终止失败保留；原完整自动15/18、人工离线复核17/18。框架435+25既有通过，已核对冻结和原始证据。只属合成开发集隔离结果，未测试正式卡片或采集，P0未整体通过、待原会话验收。


## 2026-10-15 自然事件原句补测：未完成草案配置

用户补充授权原句“调查克拉玛依大火的舆情”，随后要求绕过TUN并继续DeepSeek Pro。保留原confirmation代码/提示/schema，新增一条独立探针及火灾主题模拟资源，不改固定18dev。首次SSL失败1请求0响应保留；en0直连4请求4响应，TLS校验通过，无自动重试。模型记录keep，之后query两轮、read一轮（两工具），四轮结束，没有configure，补测草案0/1。语义理解未由草案证明，不把预置事件词库当模型理解证据。冻结set标签原判失败保留；事件主题与显式地域限制不等价，不能仅以keep断言理解错误。第三轮请求详情但fixture仅回列表，是循环预算的影响因素；本轮不修提示、不追加轮次。项目累计507请求499响应，旧5/5固定dev结果不改。

完整输入、两次尝试、逐轮工具行为、原判与人工限制及绝对证据路径见 [自然事件补测](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/NATURAL_EVENT_PROBE.md)。保留减少重复确认版本，P0未整体通过、待原会话验收；未改正式业务或进入P1。
