# M3 资源管理实施与验收记录

基线：`cad3eabd583543fac27a45f211122e2ca924ed2b`。
实验分支：`codex/m3-resource-lifecycle-experiment`。
所有实现仅位于独立 worktree `xhs-audit-agent-m3-resource-lifecycle-experiment`。

2026-09-11 增补：按用户要求完成 8 类真实对话流程的分场景验收（22 个成功轮次、5 个隔离 Run/Job），修正“修改但不保存仅给文字”及“完整词库指纹误用于任务搜索”两个问题；相关回归 132 项通过。新增 12 次新旧响应时间测量，配对成功样本中 Draft 创建约增加 7.2%，规则生成约增加 14.3%，每类仅 2 组有效配对。详见 [多流程与耗时验收](M3_FLOW_AND_LATENCY_VALIDATION_20260911.md)。下文保留 2026-09-10 的实施及历史验收记录。

## 运行隔离

没有修改或重启 K2 的代码、数据库、输出、进程、端口、监控、抓取器或账号。
离线测试与浏览器验收均使用一次性 SQLite 和合成账号；浏览器服务使用独立临时端口，退出即关闭。
真实 Qwen 验收只用用户另行提供的 Key 和专属地址，在独立进程内设置。Key 经无回显输入，仅驻留内存，不进入代码、配置文件或证据。
没有进行真实平台抓取。

## 最终产品范围

用户明确要求不增加资源编辑面板。主页面、会话页面保持原状；新增面板已移至 `Audit_assistant/tests/fixtures/resource-workspace/`，只供测试。
日常仍通过自然语言查看、生成、修改、保存与使用资源。原有后台词库编辑页只补齐稳定词条 ID 与版本检查。

- 保存即发布，不增加单独的发布确认；规则、词库可各自保存或只临时使用。
- 生成、编辑、保存本身均不启动任务。原有规则展示后采用、Draft 预览、明确确认执行边界保留。
- Draft 确认前可改；确认后冻结。修改后的需求需要新 Draft，原任务快照不变。
- 变体通过固定词条 ID 关联主词。修改主词不丢失变体；仅启用主词进入实际搜索，变体和标签不自动展开。
- 编辑正式资源先建立会话副本；保存可更新原资源或另存。系统规则只允许另存。
- 并发保存检查完整资源版本；冲突不覆盖。正式内容、版本与保存回执在同一数据库事务内提交。
- 相同保存请求可恢复回执；连续编辑后再次保存更新同一资源。明确“另存”才产生新副本。

## 分阶段结果

| 阶段 | 实现与验证 | 状态 |
| --- | --- | --- |
| S0 基线及隔离 | 278 项基线测试、23 项子测试；保存代码指纹、民族指导、规则样例 | 完成 |
| S1 会话编辑 | 完整资源读取；词库副本；规则沿用已有 Proposal；版本化局部修改与历史 | 完成 |
| S2 正式保存 | 规则保存即发布；完整词库版本；事务回执；并发冲突；复制后继续编辑 | 完成 |
| S3 自然语言工具 | 保留原 10 个工具，增加 7 个资源工具；同回合可分别保存多个资源；重新展示后仍可采用 | 离线与真实 Qwen 通过 |
| S4 任务组合与冻结 | 正式/临时规则 × 正式/临时词库四种组合；确认后修改资源不改变任务快照 | 本地通过，未真实抓取 |
| S5 接口及界面验证 | 现有词库页兼容；测试面板验证保存、刷新、冲突、丢响应恢复、窄屏 | 通过；新增面板不交付 |
| S6 回归及真实生成 | 全套 1081 通过、24 跳过、67 子测试通过；2 项原基线已有失败；真实 Qwen 四轮资源流程与原自然语言入口通过 | 完成；生成质量限于已验样例 |

完整回归用时 140.11 秒。2 项失败已在原始提交的临时副本中独立复现，详情见 `docs/evidence/m3-resource-lifecycle/baseline-known-failures.txt`：

1. `test_authoritative_provider_failure_prevents_completed_result_persistence`：测试构造的 provider failure 场景返回 completed，而断言要求 failed。
2. `test_v3_snapshot_accepts_analyze_limit_greater_than_one`：兼容快照的 analyze_limit=2 超过基线默认执行上限 1。

这两项不算作通过；本次不改动相关抓取执行实现。TypeScript 检查与 Vite 构建通过。构建仍有原体量级别的大 chunk 提示。

## 民族类规则能力

原民族类生成指导逐字保留，规则编译器、模型选择与运行适配器未改。额外增加生成自查：命中条件与裁决说明的逻辑应一致；个人生活选择、血统纯洁、强制干预应区分；语言线索按实际可承载证据选择阶段。检查暴力、权利、通婚及民族关联个体攻击等原有覆盖，限制条数时合并相邻风险，不丢独立触发条件。维汉关系中的攻击判定对双方适用，不推断任何人的真实民族身份。扩展仅用于民族关系请求。

免费额度首次真实验证成功生成 6 条规则和 3 主词 + 3 变体，并且没有保存、没有创建 Draft、没有启动任务。原有的正常文化表达豁免、具体行为批评与群体攻击区别、非人化/暴力/剥夺权利的分级、维语低优先级辱骂线索均被保留。此单次结果不能证明所有输入均稳定。

首次运行后续保存阶段因测试预算停止：7 次 provider 请求，累计 89,240 tokens；另有连通探测 131 tokens。没有把这一中止记为功能通过。用户随后提供独立付费 Token Plan 凭据，继续完整真实验收。

## 真实 Qwen 验收

使用 Hermes 0.20.4、qwen3.7-plus、thinking 开启；独立 Token Plan endpoint 与用户提供的专用凭据。模型实际调用业务工具与一次性数据库，不是模拟模型答案。

- `qwen-resource-paid-acceptance-v3.json`：四轮通过，21 次 provider 请求、315,107 tokens。生成两类临时资源 → 改主词保留变体并分别正式保存 → 正式规则搭配临时主词生成 Draft → 新会话读取后台资源，词库保存回原处、规则仅改名另存。另存前后规则条件相同，变体父 ID 相同，最终仅一个 Draft、零个 Run。
- `qwen-existing-natural-task.json`：原自然语言博彩调查入口通过，选择既有规则与词库，产生一个 Draft、零个 Run，77,413 tokens。没有把新增民族指导错误用于其他领域。
- `qwen-ethnic-final-generation.json`：专项生成通过，6 条规则与完整临时词库，34,748 tokens。已检查民族对象覆盖与正常表达豁免；发现通婚规则裁决说明可能额外增加强制门槛，随后补充独立分支提示并再次验证，见质量检查记录。
- `qwen-ethnic-final-generation-v2.json`：最后一次专项复验通过，6 条规则、3 主词 + 3 变体，46,565 tokens。通婚规则的命中条件与裁决说明均保持三个独立分支；未保存、未创建 Draft、未启动任务。保留模型在证据阶段选择上仍有波动的限制说明。

最后代码状态的资源与原会话回归共 81 项通过（15.31 秒）；浏览器验收再次通过；TypeScript 与生产构建通过。独立付费测试累计 682,580 tokens，免费测试累计 89,371 tokens，均为 provider 返回的 usage 总计。

早期真实验收中的预算停止、仅给文字未创建资源、规则条数及覆盖不足等结果均保留，未改写为通过。`v2` 的流程通过不等于其规则质量通过。详见 `docs/evidence/m3-resource-lifecycle/ethnic-quality-review.md`。

这些是有限样例的功能与语义验收，不是统计稳定性或真实抓取效果证明。未合并回当前基线、未部署、未运行真实平台抓取。

## 复现

从实验 worktree 使用已安装项目依赖的 Python：

```sh
python scripts/resource_experiment.py tests/test_resource_lifecycle.py
python scripts/resource_experiment.py tests
python scripts/resource_browser_smoke.py --node /path/to/node --chromium /path/to/chromium
python scripts/resource_qwen_acceptance.py --base-url YOUR_AUTHORIZED_BASE_URL --token-budget 320000 --max-calls 24 --output qwen-resource-paid-acceptance.json
```

最后一条是显式选择的真实模型测试，会提示无回显输入 Key；其他命令不使用真实 provider。不会启动采集工作进程。
