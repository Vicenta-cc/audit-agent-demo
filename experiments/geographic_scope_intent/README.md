# 地域意图 P0 隔离实验

当前状态（2026-10-15）：**保留减少重复确认confirmation版本、待原会话验收；P0未整体通过。** 固定18dev的set识别7/7、可落地set5/5、clear1/1、实际配置13/14、严格记录15/18保持。另补自然原句“调查克拉玛依大火的舆情”：首次SSL失败1/0，en0直连4/4响应但未配置草案（0/1），单列保留；累计507请求499响应。当前版本代码未改、未进入P1。

当前入口恢复为 `contrast_runner.py` 的 `run_arm(case, provider, 'bridge')`：沿用原完整提示、原工具集、原 record schema、原工具选择与循环，不包含后续强制记录或自动承接修复。原版缺记录、四轮额度与草案漏设问题随版本恢复保留。既有真实 CLI 和结果的禁止覆盖保护保持，本次恢复不授权重新运行评测。

后续修复实现的 10 个代码/提示/方案/测试文件已移出当前目录并完整归档到 [bridge-repair-v1-before-original-restore.zip](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/archived-code/bridge-repair-v1-before-original-restore.zip)。原/修复版评测、冻结清单与失败证据全部保留；后文修复版命令仅为历史记录，其模块现已归档。恢复证据见 REPORT 末尾。

本目录仅准备协调模型的地域意图实验。正式提示、工具注册、业务契约、store、搜索、精选和审核链路均未修改。默认导入、请求准备及 scripted 模式不联网；所有工具通过 `FixtureDispatcher`，无业务服务转发。

`baseline_prompt.txt` 和 `baseline_tools.json` 来自 `a4997d3` 的 creation 提示和 `creation_tool_schemas()`。`snapshot.py` 在临时数据目录中导出 schema，阻断网络/数据库；不会启动应用或 worker。候选指导来自主方案 2.1，追加实验边界说明。候选 configure 的原类与 validator 通过 AST 隔离提取；只扩展可选 scope change 和 update 变化白名单。原字段、资源引用格式、create 必填、revision、null 禁止规则保留；schema 同正式模型接口隐藏 `wb`。各来源及副本 hash 在 `manifest.json`、运行输出中。

`cases.jsonl` 是人工编写的合成标注，按完整会话固定 dev/holdout。模型输入不含 expected、判分原因、文本匹配模式或授权判分标注。没有真实标注对话；holdout 也由同一实施团队构造，不能当作独立实证。`source_evidence_span` 是允许摘录的最小标注证据片段；来源验证只证明用户原话及任务归属，不能证明模型理解。行政地区归一化仅是受控 fixture，未知地区不能推测，朝阳保留歧义。

在 worktree 根目录使用现有环境（下列 `PY` 为实际解释器）：

```sh
PY=/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python
PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest tests/test_geographic_scope_contract.py tests/test_geographic_scope_data.py tests/test_geographic_scope_scoring.py tests/test_geographic_scope_runner.py -q
PYTHONDONTWRITEBYTECODE=1 "$PY" scripts/resource_experiment.py tests/test_conversation_provider_boundary.py tests/test_unified_draft_configuration.py
"$PY" -m experiments.geographic_scope_intent.runner --prepare --output outputs/geographic-scope-intent/prepared-requests.json
"$PY" -m experiments.geographic_scope_intent.runner --mode scripted --scripts experiments/geographic_scope_intent/scripted_smoke.json --case-id city-kelamayi --case-id clear-unlimited --case-id keep-title --case-id ambiguous-chaoyang --case-id defer-missing-all --output experiments/geographic_scope_intent/evidence/scripted-smoke.json
```

`scripted_smoke.json` 是单独手写的五个固定响应，不从 expected 生成答案。它验证请求构造、工具回执和判分接线，不能衡量模型效果。日志 `evidence/` 单列框架证据；完整离线请求文件保留在被仓库忽略的 `outputs/`，可随时由上述命令重建。

真实调用已获用户明确授权，本轮按完整 dev v1 → 最小实验修复 → 完整 dev v2 → 逐例复核及冻结 → 完整 holdout v1 一次执行。最终状态、结果与限制以 REPORT.md 最新追加记录为准；首次交付的未授权说明及初始凭据阻断均为历史。

模型配置来自用户指定的私有 JSON `/Users/ext.wanghongtao6/Documents/Codex/acceptance/unified-draft-frontend-20261012/environment.private.json`。操作包装器仅注入 `CONVERSATION_MODEL`、`CONVERSATION_BASE_URL`、`CONVERSATION_API_KEY`，固定 `deepseek-v4-pro-ga-260813` 与 `https://api.qhose.net/v1`。旧文件的资源生成配置未注入，未读取或修改云端配置。指定业务 worktree 的 `.env` 缺失不表示私有配置不存在；单独读取默认模型地址也不表示密钥已加载。

以下记录实际使用的命令，包装器保留逐例 checkpoint 与进度，调用原 runner，拒绝覆盖已有结果。最终版本冻结在 `evidence/real-evaluation-freeze.json`；v1 的旧指导以其原始请求和运行 manifest 为准，不能把当前指导当成 v1 版本。本轮已执行的评测不得重新调用覆盖或刷分。

```sh
"$PY" -B outputs/geographic-scope-intent/run-authorized-eval.py dev v1
"$PY" -B outputs/geographic-scope-intent/run-authorized-eval.py dev v2
# 仅在开发分析及版本冻结完成后执行一次
"$PY" -B outputs/geographic-scope-intent/run-authorized-eval.py holdout v1
```

包装器向 runner 传递 `--mode real --enable-real-model --authorization-label '用户授权P0隔离真实模型评测：先开发集后留出集，不采集、不修改正式业务' --config-root /Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo --split <dev|holdout> --output outputs/geographic-scope-intent/real-<split>-<version>.json`。日志重定向到同名 `.progress.txt`，逐例完整 trace 保存在 `.checkpoints.jsonl`。

真实入口只调用协调 provider 的 `/chat/completions`，deepseek 沿用 `thinking: disabled`；不创建 Hermes runtime，不注册正式工具，不用审核或资源生成 provider。无凭据时在 HTTP 前拒绝运行。显式开关与描述不替代用户真实授权。密钥及 Authorization 请求头不进入 trace，包装器和交付检查验证已保存证据不包含私有 API Key。

每例最多 4 次模型响应、12 次工具请求；单次网络 timeout 45 秒，无自动重试。记录原始请求/响应、文本、工具顺序、参数、模拟回执、终止原因、耗时及可取得 usage。非法 JSON、错误参数、未知工具、无回复、超时和循环上限全部失败；同批尚未执行的请求也捕获。生成/编辑/保存只捕获，不编造新资源引用；确认/启动始终阻断。configure 仅可在内存模拟状态变化，来源/资源/revision/采用失败不部分修改。

判分分动作、目标、策略、来源、工具边界；set precision/recall 用完整通过的 set 为分子，所有尝试 set/所有期望 set 为分母，缺失与失败例保留。按模式与 split 分开，单列误清除、漏识别、策略/来源错误、澄清/延迟、未知/越界请求和运行失败。澄清/延迟/no_task 文本判分是标注 regex 加完成声明反例的启发式，真实评测需人工复核。费用未返回时为未知，不能推算为零。95% 仅是待验证建议门槛，固定关键反例不允许误设/误清；不自动宣布通过或进入 P1。


## 18条三组隔离对照（已执行，2026-10-14）

最新用户授权固定dev18×A/full、B/bridge、C/simple，预算162、实际119请求/119响应，无自动重试。先完成390项针对性测试、25项回归和31项版本冻结再真实调用。同一DeepSeek Pro协调模型；所有业务工具均FixtureDispatcher，record仅捕获不泄露答案；C单响应即停。正式业务与P1未接入。

本轮入口为 `python -B -m experiments.geographic_scope_intent.contrast_runner --prepare` 和带 `--enable-real-model --authorization-label` 的真实入口；完整已执行命令、配置来源、分母、判分分歧与限制见 [REPORT.md](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/REPORT.md)。已有文件采用禁止覆盖保护；本次授权已执行完成，不应再次运行已有真实结果或扩样刷分。

冻结：[contrast-v1-freeze.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/contrast-v1-freeze.json)；54行离线复核：[contrast-v1-review.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/contrast-v1-review.json)；汇总：[contrast-v1-summary.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/contrast-v1-summary.json)。原始轨迹分别为 [full.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/contrast-v1/full.json), [bridge.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/contrast-v1/bridge.json), [simple.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/contrast-v1/simple.json)；每次HTTP调用日志：[http-ledger.jsonl](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/contrast-v1/http-ledger.jsonl)。

当前状态：P0真实模型评测已完成、待原会话验收。B地域record16/18、set召回7/7，草案set4/5；C record17/18但作者背景误set1例。不能据此判定P0整体通过或工具数量是唯一原因。


## 桥接承接本地修复（历史记录，代码已归档）

采用实测B桥接作为基线，原代码/结果冻结不改。新 `bridge_handoff.py` 复用B请求、record schema与FixtureDispatcher，增加强制单次record门禁、set/clear自动承接与keep保留、configure成功本地收尾。总4响应/12工具不变；缺配置或预算耗尽保留pending。用户未指定地域时新任务keep保持scope=None，既有草案keep保留地域并处理其他配置。AI仍判断语义，不用expected纠错。

新runner只允许scripted provider。本轮真实模型请求新增0，历史累计229/228不变；新版本语义效果未评测，正式业务与P1未接入。48项新增测试、全量438项针对性及25项回归通过。离线入口：`python -B -m experiments.geographic_scope_intent.bridge_handoff_verify --prepare`（已有36项freeze/18请求，禁止覆盖），`--replay`（只取历史record/config做承接适配检查，不是新模型分数）。

[bridge-handoff-v1-freeze.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-handoff-v1-freeze.json)、[bridge-handoff-summary.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-handoff-summary.json)、[bridge-handoff-historical-replay.json](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-handoff-historical-replay.json)；完整结果和命令见 [REPORT.md](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/REPORT.md)。当前状态：本地桥接修复完成、待原会话验收，新版本真实语义未评测。


## 修复桥接真实18dev评测（历史记录，代码已归档）

用户授权72上限，实际31请求/31响应，无重试/HTTP失败，全部18例一次完成；freeze41项，提示/schema/36修复文件保持。492+25本地通过。旧B严格地域16/18与set7/7，本轮自动8/18、人工7/18，set精确率0/8、召回0/7、草案set0/5，模拟草案4/14，效果退化。原判/协议适配/人工差异均保留，不推荐正式落地。新入口 `bridge_real_eval --prepare` / `--enable-real-model --authorization-label` 记录于REPORT，已有文件禁止覆盖，当前授权轮已完不再调用。累计260请求259响应，P0未整体通过。

[完整报告](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/REPORT.md)、[评测冻结](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-real-v1-freeze.json)、[汇总](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-real-summary.json)、[逐例复核](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/bridge-real-review.json)、[全部原始结果](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-real-v1/results.json)。当前状态：P0真实模型评测已完成、待原会话验收。此前“新版本尚未真实评测”为上一交付的历史状态。


## 原B恢复后的真实复测（2026-10-14，已结束）

完全复用原 `run_arm(..., 'bridge')`，单独输出目录 `outputs/geographic-scope-intent/bridge-original-retest-v1`。18dev一次完成：48请求46响应2超时，无重试；严格地域11/18，set5/5精确率/5/7召回，草案set5/5、clear0/1，实际配置8/14。原31冻结代码及18初始请求一致，没有强制tool_choice、新提示或自动地域注入。本次未出现单字/空白串，但漏记录、漏落地、错误clear记录仍在，未整体通过。

本轮包装器 `run.py --prepare` / `--enable-real-model --authorization-label` 已完成且拒绝覆盖；已有授权已执行，不重新运行刷分。全部结果、freeze、逐例人工复核及三版对比见 [REPORT最新记录](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/REPORT.md)。后续复测不改变当前原B源码基线；原修复代码仍仅在ZIP归档，未恢复其入口。


## 原桥接最小指导四候选（最新，2026-10-14）

bridge_minimal.py复用原循环，只追加独立提示及只读进度诊断，真实调用默认关闭。用户追加授权后四组各18dev已完成，194请求190响应4超时，无重试。原31文件未改，435+25本地通过。记录最佳18/18与配置最佳13/14来自不同候选，组合未过朝阳反例，P0未整体通过。

[全部结果及72例表](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/REPORT.md)；[新冻结](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/bridge-minimal-v1/freeze.json)。

## 2026-10-15 当前保留版本

明确选择 confirmation，冻结代码与其他候选不变。不要将 API 默认 combined 当作已选版本。参见 [减少重复确认验收交接](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/CONFIRMATION_HANDOFF.md)；本次仅整理，新增真实调用0，原判与失败保留，待原会话验收。


自然事件补测详见 [NATURAL_EVENT_PROBE.md](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/NATURAL_EVENT_PROBE.md)；本次结果未落草案，不能把原固定dev5/5扩展到事件自然输入。
