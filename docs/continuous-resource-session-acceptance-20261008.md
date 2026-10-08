# 连续会话验收矩阵与基线结果

本矩阵配合 [设计方案](continuous-resource-session-plan-20261008.md) 使用。现有测试通过只证明当前基线，不代表连续会话改造已经完成。下列新增链路均须在相应阶段实际实现和验收，不能以相似测试代替。

## 数据和判定方式

- 两个普通用户、一个管理员、两个会话；规则 A/B、词库 A/B，包括同名和不同名样本。管理员也不能绕过私有资源权限。
- B 至少保留 v1 至 v4；任务固定采用 v3。记录每个版本的内容哈希、保存回执、采用依据、草案修订和冻结配置。
- 业务断言以数据库内容、唯一数量、权限响应、工具参数和回执为准；模型“已成功”不能作为写入证据。
- 冻结配置与已发布报告在测试前后做规范化内容/哈希比对；允许任务运行状态正常前进，不误把心跳时间变化算作配置篡改。
- 旧库使用合成的旧 schema/历史记录副本，包含历史草案、工具回执、已发布报告和运行中任务；不使用或重置线上库。
- 恢复测试清空模型历史、重建服务对象或进程；浏览器刷新、SSE 断线、进程终止分别测试，不混称。

## 阶段 1 资源状态恢复

“基础”表示可复用的现有测试，不表示新目录已通过该项。

| ID | 场景 | 必须断言 | 基础与缺口 |
| --- | --- | --- | --- |
| S01 | 同会话生成规则 A/B 和词库 A/B | 各有独立 ID，全部可发现，含未保存和未采用候选；同名不合并 | `test_resource_lifecycle.py`；新目录待测 |
| S02 | 保存 A 后编辑 B 至 v4 | A 的保存版本不变，B 历史可恢复；保存不自动选择或采用 | 生命周期基础；统一投影待测 |
| S03 | B v3 建草案后编辑至 v4 | 目录同时呈现编辑 v4、草案 v3，不以最新替换 | `test_ruleset_proposal_approval.py`、草案配置测试；目录关联待测 |
| S04 | 启动后继续编辑 B | Run 冻结 v3 的内容/哈希完全不变 | 草案配置/私有资源基础；跨目录链路待测 |
| S05 | 无模型历史、新进程、报告完成后读取 | A/B、保存记录、采用及冻结版本都能恢复 | workspace/ref 重建基础；完整资源目录恢复待测 |
| S06 | 明确选择 B 后刷新；保存或展示 A | 选择仍为 B，来源事件可查；不存在选择时返回未知 | 审批已有；通用选择记录待实现和测试 |
| S07 | 两个候选均可能是“刚才那个” | 不生成已确认选择，不触发写入；等待消歧 | 先测契约；语言理解在 Qwen 用例中验收 |
| S08 | 重放选择事件，并发选择/编辑 | 同一事件仅一条；预期版本冲突明确，不丢失新选择 | 新选择机制待测 |
| S09 | 重复读取和分页 | 业务表和引用表不增加行、不改变版本；无遗漏/重复资源 | 现有 get_edit 有引用副作用，新投影必须单独测 |
| S10 | 大目录、并发保存、跨库读取中断 | 受限响应和稳定分页；不一致或未知保存状态明确返回，不伪装未保存 | 新投影一致性和规模测试待测 |
| S11 | 跨用户、跨会话、管理员、伪造引用 | 无越权元数据/正文泄露；正式资源和临时资源边界分别校验 | `test_private_resource_lifecycle.py`、`test_resource_edit_api_contract.py`、refs；新端点待测 |
| S12 | 引用失效、来源删除、历史缺失归属 | 不静默升级、不猜归属；冻结内容可按既有权限读取，来源缺失明确 | refs 和旧数据基础；目录降级待测 |
| S13 | 旧库迁移、重复初始化、并发启动、迁移中断 | 历史记录和冻结哈希不变；附属 schema 可重入、失败可恢复 | e8fdaef 迁移测试只覆盖草案幂等，不能替代新表测试 |
| S14 | 新代码附属表存在时回到基线代码 | 旧读取和原流程可用；不 drop 表、不覆盖数据库 | 兼容演练待做 |

## 阶段 2 能力衔接与故障恢复

| ID | 场景 | 必须断言 | 基础与缺口 |
| --- | --- | --- | --- |
| C01 | 开关关闭、旧会话带 reportBinding | 原路由和报告专业工具行为保持；未授权新写工具不暴露 | creation/report 基础；开关组合待测 |
| C02 | 只读阶段：问报告→读 B→再问报告 | 原报告身份与证据正确；读取 B 不改变报告上下文或任何资源 | 新协调路径待测 |
| C03 | 报告后修改并保存未采用的 A | 实际改的是 A 的指定版本，回执可查，原任务/报告哈希不变 | 私有资源 HTTP 已有；新聊天路径待测 |
| C04 | 草案不满意，只保存资源 | 保存成功；没有创建 Run 或触发采集 | 服务层基础；自然语言路径待测 |
| C05 | 报告后保存任务所用 B v3；随后保存编辑 B v4 | 两个请求准确指向不同版本；不从最新稿推断任务版本 | 任务保存基础；与目录/Agent 联动待测 |
| C06 | 生成、保存、采用、启动相邻请求 | 保存不采用、采用不启动；已启动配置不可改；新调查仍走新会话 | 现有边界回归 + 新入口组合待测 |
| C07 | bridge、延迟加载、read_report、非法 shell | 注册/实际暴露/调用一致；不能用桥递归或调用未授权工具；错误不伪称读取成功 | `test_report_tool_loading.py`；新模式实际构造待测 |
| C08 | 报告内部 session 或 workspace ID 被篡改 | 后端拒绝；不能借报告授权访问另一会话资源 | 现有 handoff 授权基础；新协调入口待测 |
| R01 | 输入拦截四次后成功；连续五次失败 | 恰好最多五次同请求；成功/耗尽状态真实，业务不重放 | `test_creation_input_retry.py` 已覆盖核心适配器；新入口接线待测 |
| R02 | 流式降级非流式、重复安装、SDK重试 | 单一请求预算，不变成 25 次；记录模型请求和工具执行两个计数 | 已有防叠加测试；新模式及 SDK 接线待测 |
| R03 | 用户取消、输出了一部分后失败、输出拒绝 | 不重放已显示内容，不按输入拦截逻辑处理输出拒绝 | retry 基础；新入口端到端待测 |
| R04 | 保存提交成功，模型回复被拦截或断线 | 查原回执、报告准确对象版本；写入次数为 1；不要求用户重建资源 | 回执已具备；草案检查点兜底不等于资源保存兜底，此项待补 |
| R05 | 同轮 A 保存成功、B 保存失败、最终回复失败 | 明确 A 成功、B 失败；无整轮重放、无笼统“全部失败/成功” | 部分检查点基础；逐资源异常答复待补 |
| R06 | 写入中超时、提交后响应丢失、回执查询暂时失败 | 状态未知就停止新写，查询原操作；不得更换操作 ID 绕过去 | 草案恢复/资源回执基础；协调层故障注入待测 |
| R07 | 规则过期、参数错误、权限拒绝 | 仅已证实未写入的请求可纠错；内容改变不能自动采用；越权不重试 | e8fdaef 与版本测试已有；跨能力调度待测 |
| R08 | 网络错误、超时、限流、连续读失败 | 明确总预算与取消；不多层重试；读失败不编造已参考的内容 | 需先盘点 runtime 网络预算，再确定断言；不能沿用 censor 五次套所有错误 |
| R09 | SSE重连、刷新、进程重启、模型上下文压缩 | 使用既有业务回执与精确资源状态；不重复调用成功的写工具 | 原 SSE/workspace 基础；新入口完整链路待测 |

## 阶段 3 真实 Qwen 验收

这些测试必须调用真实 Qwen，保存脱敏的工具轨迹及落库断言；运行次数和失败次数都写入结果，不能只保留成功样本。当前均未执行。

| ID | 连续用户流程 | 关键检查 |
| --- | --- | --- |
| Q01 | 创建 A/B→保存 A→反复编辑 B→选择 B v3→建草案→先保存不启动→启动→B 改 v4→报告后保存任务所用版本 | 每步精确身份/版本/回执；任务只存在一个且冻结 v3；之后编辑 A 不影响报告 |
| Q02 | 问报告证据→编辑另一份临时规则→保存→再次问同一报告 | 报告工具、证据引用与资源工具连续切换；专业分析能力保留 |
| Q03 | 同名 A/B、“刚才那个”、明确指定版本、用户纠正目标 | 有歧义才询问；目标明确不无谓反复确认；错误对象没有被写入 |
| Q04 | 刷新/重启/清空长历史后继续；旧会话已完成报告 | 从业务状态恢复候选和版本，非模型凭记忆重造；旧任务不变 |
| Q05 | 在工具成功后注入模型回复失败；输入请求连续拦截；状态未知 | 实际写入一次，回复准确反映事实；请求次数受限；拒绝不被规避 |
| Q06 | 相同正常问题在旧入口与候选入口对照 | 人工检查语气、解释粒度、自然表达及报告证据能力；不要求逐字相同，不用固定文案替代正常回答 |

每个 Qwen 场景至少运行两个独立会话样本；出现错误目标、越权、重复写入或任务变化即阻断发布，不以总体成功率掩盖。少量样本不证明模型行为稳定，后续应积累同类真实问题的回归集。

真实模型测试与真实采集分开计。隔离数据库配受控的执行器/报告样本可以验证 Agent 业务链路；它不能证明爬虫、ASR、OCR 和线上流水线已经通过。浏览器按钮、SSE断线和恢复需另有浏览器验收记录。

## 320 项与 240 项的覆盖差异

已从本会话本地执行日志找回两次命令及结果，两次均在 cloud-triage-deploy 工作区执行。不是同一组测试减少了 80 项。

两组共有四个文件：`test_draft_creation_recovery.py`、`test_investigation_creation_conversation.py`、`test_investigation_creation_m3.py`、`test_ruleset_proposal_approval.py`。

- 320 项那组额外运行 `test_investigation_draft_configuration_m3.py` 与 `test_creation_prompt_isolation.py`。
- 240 项那组额外运行 `test_resource_snapshot_refs.py` 与 `test_resource_lifecycle.py`。
- 两组还使用了不同本地 Python 环境和启动参数。不能只比较数量就判断覆盖或环境完全相同。

320 项的历史原始命令：

```sh
PYTHONPATH=/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/external/hermes-agent \
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python \
-B -m pytest -q -p no:cacheprovider \
tests/test_draft_creation_recovery.py \
tests/test_investigation_creation_conversation.py \
tests/test_investigation_creation_m3.py \
tests/test_investigation_draft_configuration_m3.py \
tests/test_ruleset_proposal_approval.py \
tests/test_creation_prompt_isolation.py
```

历史结果：`320 passed, 5 warnings, 23 subtests passed in 33.30s`。

240 项的历史原始命令：

```sh
PYTHON_DOTENV_DISABLED=1 \
/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python \
-m pytest -q \
tests/test_draft_creation_recovery.py \
tests/test_investigation_creation_conversation.py \
tests/test_investigation_creation_m3.py \
tests/test_resource_snapshot_refs.py \
tests/test_ruleset_proposal_approval.py \
tests/test_resource_lifecycle.py
```

历史结果：`240 passed, 6 warnings, 23 subtests passed in 30.60s`。

## 本次基线运行

2026-10-08，在基线 `e8fdaef177f1fde5263ae17d974cc928a9157fa4` 取上述两组并集，并加入 censor 重试、私有资源、编辑接口、报告工具加载及报告快照测试。没有新增业务代码或测试代码。

工作目录：`/Users/ext.wanghongtao6/.codex/worktrees/cloud-triage-deploy/xhs-audit-agent-demo`。

```sh
PYTHON_DOTENV_DISABLED=1 \
/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python \
-B -m pytest -q -p no:cacheprovider \
tests/test_draft_creation_recovery.py \
tests/test_investigation_creation_conversation.py \
tests/test_investigation_creation_m3.py \
tests/test_investigation_draft_configuration_m3.py \
tests/test_ruleset_proposal_approval.py \
tests/test_creation_prompt_isolation.py \
tests/test_resource_snapshot_refs.py \
tests/test_resource_lifecycle.py \
tests/test_creation_input_retry.py \
tests/test_private_resource_lifecycle.py \
tests/test_resource_edit_api_contract.py \
tests/test_report_tool_loading.py \
tests/test_published_report_runtime_snapshot.py
```

实际结果：**424 passed，23 subtests passed，6 warnings，39.74 秒**。警告为 multipart、AnyIO/Starlette、FastAPI lifespan 的弃用提示。

`tests/conftest.py` 将应用数据、输出、Hermes状态及调度数据库指向临时目录；禁用 dotenv 自动加载。该批是隔离数据库和模拟运行时回归，没有调用真实 Qwen、线上服务或爬虫。它也不是完整项目测试、前端测试或新增方案验收。

本轮只新增设计和矩阵文档。尚未覆盖的风险集中在：新目录跨库一致性、选择事件来源、报告与资源工具上下文隔离、保存成功后的答复恢复、真实模型目标选择及浏览器断线行为。后续必须按上面的阶段门槛逐项补齐。
