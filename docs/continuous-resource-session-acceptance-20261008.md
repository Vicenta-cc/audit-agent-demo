# 连续会话验收矩阵与基线结果

> 本文按时间保留审查和验收记录，早期结论不代表当前产品要求。用户后续明确的词库规则为：优先启用变体；没有启用变体（包括全部停用）时回退启用主词；主词停用时整组排除。下文“再次细审”中 R05 对主词回退的否定已被这一确认取代。R01–R04 和成功兜底续聊的最新实施、结果与限制见文末对应章节。

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

截至设计提交 `cd5c8fb`，只新增设计和矩阵文档。该次基线结果不代表后续新功能通过。阶段 1A 的实际实施记录如下。

## 阶段 1A 实施和验收记录

2026-10-08，仅实施只读资源目录及精确版本详情。没有修改聊天入口、模型工具集合、Prompt、censor 请求预算或 `e8fdaef` 的幂等代码，也没有建立选择记录表。

### 实际修改

- `backend/resource_management/session_state.py`：独立读取器，只接受三个数据库路径和调用者身份；使用 SQLite `mode=ro`、`query_only=ON` 和读事务，不调用会建表、迁移、发行引用的服务构造或读取方法。
- `backend/resource_management/api.py`：新增两个经当前认证依赖保护的 GET 端点：`/api/investigation-workspaces/{session_id}/resource-state` 和其 `/detail?key=...`。读取器再次核验 creation 会话归属，不接受报告内部会话冒充业务会话。
- `tests/test_session_resource_state.py`：16 项隔离测试。
- 两份设计/验收文档：更新已实施范围与证据。

目录按条目类型区分编辑版本、保存回执、正式资源版本、草案修订和 Run。默认每页 20 条、最多 100 条；摘要不含正文。详情使用带版本的 key，不能将 v1 查询自动替换成 v2。分页游标绑定目录摘要；期间发生变化返回 409，要求重新读取。

草案会话归属来自创建操作、成功工具回执及采用审批，再检查草案/任务的所有者；不用聊天正文推断。任务规则来自冻结配置；仅在 ID、版本和内容哈希确实对应本会话编辑历史时标记来源已验证。缺少历史、来源或回执依据时返回未知/partial，不补写、不迁移。

### 核心恢复用例的实际结果

准备两套规则 A/B：保存 A v1，B 经实际编辑形成 v1 至 v3，经已有展示/采用审批工具创建草案并确认，任务冻结 B v3；再将 B 编辑至 v4。同时建立已保存词库、其更新版本及未采用的同名候选。

启动独立 Python 子进程，只有数据库路径、会话 ID 和用户身份；通过 SQLite authorizer 禁止读取聊天消息、轮次和事件表。读取结果仍能恢复：

| 检查 | 实际结果 |
| --- | --- |
| A 保存结果 | 编辑 ID、v1、正式资源 ID、保存操作回执一致 |
| B 编辑历史 | v1/v2/v3/v4 分别存在，当前版本为 v4 |
| 任务实际规则 | 冻结 B v3；详情正文仍为 v3，与当前编辑稿区分 |
| 未保存、未采用及同名词库 | 保留各自独立 ID，不按名称/内容合并 |
| 读取副作用 | 三个数据库读取前后完整逻辑 dump 相等 |
| 用户/会话权限 | 其他用户、其他管理员、同用户的另一会话及报告内部会话不能取得此目录/详情 |
| 旧版本详情 | 正式 A 后续保存新版后，旧发布版本仍返回原内容 |
| 旧数据缺失 | 删除隔离副本中的历史/操作附属表后，返回 partial 和来源未知，没有自动建表或补造关联 |
| 跨库并发 | 调查库读取快照为 v4，另一个真实 WAL 连接保存 v5；回执关联返回 unknown/partial，刷新后才恢复完整 |
| 读取故障/损坏数据 | 数据库缺失、无效 JSON、冻结正文与哈希不一致时返回 503，不返回空目录或假验证结果 |
| 已发布状态 | 将隔离任务状态设为 PUBLISHED 后仍能读取冻结 v3，任务记录及其他数据库内容保持不变 |

以上用例使用真实业务服务、SQLite、工具/审批接口及独立读取进程；资源生成的模型响应使用固定样本，报告状态使用隔离测试数据，没有调用真实 Qwen、爬虫或生产服务。

### 最终回归

运行“本次基线运行”中的同一命令，并在测试文件列表加入 `tests/test_session_resource_state.py`。最终代码上实际结果为 **440 passed，23 subtests passed，6 warnings，43.29 秒**。其中 424 项为原基线范围，16 项为新增读取验收。警告仍为既有依赖弃用提示。

`git diff --check` 通过。相对于 `e8fdaef`，幂等实现、creation conversation/tools、runtime adapter/input_retry、资源工具提示及整个前端没有改动。

### 保留的限制和回退

- 这是阶段 1A 的本地后端验收；没有恢复报告后的聊天编辑能力，没有验证真实 Qwen 多轮选择、浏览器 SSE 或整个采集/报告流水线。
- 三个数据库各自有一致读快照，不提供跨库原子快照；`revalidate_before_write=true` 是接口约定，不能代替原写入工具的权限和版本校验。当前没有接入任何新写入路径。
- `selection.status=unknown` 明确保留至 1B；目录恢复不能证明用户下一句想操作谁。
- 没有可靠业务关联的历史草案不归入某个会话；不从自然语言猜测。历史临时词库可能只有嵌入内容/搜索词、没有编辑稿 ID，保留未知来源。
- 目前分页限制响应大小，但读取器会整理本会话及关联资源的版本再分页；超大历史量的负载测试尚未完成，不能称为大规模性能已验收。
- 旧数据兼容测试使用合成副本；没有读取生产数据库做全量兼容检查。
- 无新增表、迁移或持久化状态；回退本阶段代码即可移除新 GET 端点，不需要回滚数据库，不会撤销已有资源或任务。

## 阶段 1B 实施与验收记录

2026-10-08，基于 `b38fc36` 实施显式界面选择的记录和恢复。未修改 Qwen Prompt、模型工具集合、正常回答展示、五次 censor 请求预算、草案创建幂等实现、报告强制路由或任务冻结配置；没有推送、部署或操作线上数据。

### 修改文件和数据影响

| 文件 | 作用 |
| --- | --- |
| `backend/resource_management/selections.py` | 输入契约、附属表初始化、显式选择事务和只读投影 |
| `backend/investigation_creation/store.py` | 在既有启动迁移事务中调用新表初始化；其余幂等逻辑不变 |
| `backend/resource_management/session_state.py` | 在同一调查库读快照恢复选择，分页摘要包含选择；保持 GET 无写入 |
| `backend/resource_management/api.py` | 新增认证的 POST `.../resource-selection`；不新增 Agent 工具 |
| `Audit_assistant/src/services/sessionResources.ts` | 分页读取、精确版本预览及选择写入 |
| `Audit_assistant/src/features/investigation/SessionResourceSelection.tsx`、对应 CSS | 本地验收面板，显式选择/取消、刷新、同操作重试；查看内容不自动选择 |
| `Audit_assistant/src/features/investigation/InvestigationCenterArea.tsx` | 三行开关接线，默认关闭；只在 creation 工作区显示 |
| `tests/test_resource_selections.py` | 17 项新增后端验收 |
| `Audit_assistant/tests/sessionResourceSelection.*`、`sessionResourceSelectionMount.tsx` | 5 项浏览器交互验收及独立挂载页面 |

只增加 `resource_selection_events` 表和查询索引。记录精确资源版本及用途，不复制资源正文，不回填旧聊天的“选择”。正常数据库读取不建表。规则/词库、编辑/查看分别有自己的前序事件；同一事件重放返回原回执，不能恢复成旧选择。旧标签页携带过期前序事件返回 409。

已有采用审批、Draft 修订、Run 冻结快照继续作为任务采用事实的来源。普通生成、查看、编辑、保存不生成新的已确认选择。自然语言“刚才那个”的含义仍未接入新机制，不能称为已解决模型指代。

### 验收结果

| 场景 | 实际结果 |
| --- | --- |
| 选择编辑 B v4 → 读取/保存 A → 选择查看 A → 发布状态 → 新进程读取 | 编辑对象仍为 B v4；查看对象为 A v1；任务冻结 B v3；三个库读取前后 dump 相等 |
| 真实聊天工具生成/更新/采用，但没有显式界面选择 | `selection.status=unknown`；未自动把活动变成编辑选择 |
| 响应丢失、相同事件重放 | 返回原回执，表中不新增；之后已选择 A 时，重放旧 B 请求不能抢回当前选择 |
| 两个页面同时选择；选择与编辑并发 | 一个选择成功、一个冲突；编辑后原版本标 stale，或选择因过期而拒绝；不静默升级 |
| 历史 B v3、当前 B v4、取消选择 | 历史版可查看但不可选为当前编辑；取消也有独立事件，旧页面不能利用“未选中”覆盖后续状态 |
| 权限、错误哈希、错误种类、伪造 source/采用用途 | 拒绝跨用户、管理员越权、同用户其他会话；不允许冒充聊天确认或采用审批 |
| 资源来源消失 | unavailable，不替换成前一次选择或当前版本，不返回不可访问的目标 key/hash |
| 旧库读取、真实 Store 重启升级、重复初始化、建索引中断 | 读取不迁移；升级后旧表完整 dump 不变；故障事务回滚，重试成功 |
| 选择插入后触发故障 | 事务完整回滚，重试可以成功；不产生伪回执 |
| 删除某个工作区 | 现有删除事务清掉该会话选择，其他会话保留；迟到的取消选择请求返回 404 |
| 浏览器刷新、版本预览、断线、冲突、读取失败 | 恢复两种用途；预览不发 POST；重试沿用原事件和版本；冲突不自动重试；读取错误不假装空目录 |

后端沿用 1A 的完整命令范围，增加 `tests/test_resource_selections.py`。最终实际结果：**457 passed，23 subtests passed，6 warnings，49.75 秒**。其中原有 440 项、1B 新增 17 项。全部使用临时数据库；没有真实 Qwen 或云端调用。

前端命令（在 `Audit_assistant` 内）：

```sh
/Users/ext.wanghongtao6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node node_modules/typescript/bin/tsc --noEmit
PATH=/Users/ext.wanghongtao6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH \
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
node node_modules/@playwright/test/cli.js test tests/sessionResourceSelection.spec.ts tests/resourceConversationPresentation.spec.ts tests/resourceMarkdownRendering.spec.tsx
```

类型检查通过；前端 **14 passed，5.7 秒**（5 项新交互测试、9 项原回答展示回归）。首次运行因 Playwright 默认浏览器文件缺失，5 项浏览器用例未启动；明确指定已安装的 Chrome 后通过，未改用例跳过该问题。截图已人工核看。浏览器用例使用模拟 HTTP 响应；后端 API、事务与迁移另由真实 SQLite 集成测试验证，未宣称已跑真实模型全流程。

### 扩展检查的两项既有失败

额外运行 `tests/test_resource_selections.py tests/test_workspace_deletion.py` 时，删除模块出现以下问题；在独立临时目录用 `git archive b38fc36` 提取的未修改基线上，仅运行同样两项，得到相同失败。因此不能报告全项目测试全绿，也未扩大本轮修改到额度/历史报告模块。

1. `test_workspace_deletion.py::test_historical_delete_survives_registration_restart`：缺两个 Gate 历史数据库样本，fixture 初始化失败。
2. `test_workspace_deletion.py::test_end_waits_for_fence_and_deletion_preserves_charge_ledger`：`AdmissionStore.reserve` 使用 `user["role"]` 时收到字符串，抛出 TypeError。已在旧基线复现，尚未进一步定位或修复其来源。

### 开关、回退和剩余边界

- 本地开启方式：启动/构建前设置 `VITE_SESSION_RESOURCE_SELECTION_ENABLED=true`。默认关闭；模型并未消费新选择，不影响现有聊天。关闭开关或回退提交即可撤下入口；新增附属表可以保留，无需删除数据。
- 明确界面选择可恢复；现有聊天采用事实可按既有记录恢复。通用聊天选择、用户消息依据与资源绑定、Agent 读取/使用选择、歧义追问及真实 Qwen 多轮行为留待阶段 2，不能拿本轮测试代替。
- 本轮选择不会保存资源、修改任务、授予权限；后续工具仍必须验证目标版本，不得把旧选择当作写入授权。
- 跨库原子快照、超大历史量性能、浏览器与真实 Qwen 的整体链路仍未覆盖。旧库测试使用合成隔离数据，没有扫描生产库。
- 会话删除清理与权限测试通过；没有对真实服务进程做强制断电测试。本轮断线用例分别模拟提交后响应丢失、事务内注入失败及浏览器请求失败。

## 2026-10-08：阶段 1A/1B 真实 Qwen 组件验收

基线为 `6f295a2b03afe661aea600921b105494b2d852c3`。结论：**恢复目录和显式选择的组件检查通过；真实 Agent 业务流程发现失败，不能标为整体通过，也不能据此宣称阶段 2 已接通。**

本轮仅新增验收运行器 `scripts/session_resource_qwen_acceptance.py` 并补充本文档。没有修改业务代码、System Prompt、工具权限、censor 重试或幂等实现，没有提交、推送或部署。

### 测试边界与真实调用

- 在新建隔离目录使用真实 SQLite、现有业务服务、工具执行器、Hermes 与 Qwen。没有修改模型请求参数、Prompt 或业务工具返回值；观测包装仅记录请求元数据、用量、真实工具参数/回执。
- 一条会话实际执行 **8 个用户轮次**，其中第 7 轮是失败后的额外确认。主运行器发出 **29 次真实 Qwen 请求**：25 次走 DashScope，4 次走现有独立资源生成接口，模型均为 `qwen3.7-plus`。29 次包括一次无聊天历史的目录理解请求，不是 29 次用户操作。
- 随后追加 1 次独立 Qwen 词库来源核对，合计 **30 次真实请求**。该次问答和用量单独保存在 `lexicon-lineage-probe.json`，不混入 8 轮业务会话。
- 显式选择通过真实 POST 路由调用，使用 FastAPI TestClient；不是 Qwen 发出的选择，也不是浏览器点击验收。测试用户由隔离 fixture 注入，不能代替正式登录链路验收。
- 冻结任务是测试程序调用真实 `confirm_and_queue` 业务接口，明确与模型行为分开记录。唯一隔离 Run 停在 QUEUED，无 worker、爬虫或真实报告生成。
- 1A/1B 目前没有注册成产品 Qwen 工具。目录理解请求直接提供恢复投影，仅证明模型可以读懂数据，不证明产品 Agent 已会自动读取/采用该目录。

证据保存在：

`/Users/ext.wanghongtao6/Documents/Codex/acceptance/session-resource-qwen-20261008-run1/`

其中 `evidence.json` 含逐轮输入、Qwen 答复、真实工具参数和回执、数据库投影、请求 ID 和用量；`recovered-state.json` 是独立进程读取结果；`review.json` 是人工核对后的摘要与逐字段差异。原始失败证据另存于 `evidence-before-resume-*.json`，没有用后续成功覆盖失败。

### 已验证的恢复与选择结果

| 场景 | 实际结果 |
| --- | --- |
| Qwen 生成 A、B 两套规则与两份词库 | 各自 ID 独立；没有保存、草案或任务副作用；未凭模型活动生成“明确选择” |
| 显式选择编辑 B、查看 A，再让 Qwen 只保存 A | 两份 A 均保存 v1；B 未保存；编辑 B 的选择未被保存 A 覆盖 |
| Qwen 连续修改 B 至 v2、v3 | 同一 Proposal ID；原来选择的 v1 保留且标 stale，没有自动改选 v2/v3 |
| 额外确认后创建草案 | 仅一个草案，真实采用 B v3；没有误用已保存的 A |
| 业务接口冻结 B v3，Qwen 再改 B v4 | 当前编辑稿为 v4；冻结任务详情和内容哈希与修改前完全相同 |
| 选择编辑 B v4 后重放旧 B v3 选择请求 | 返回旧回执，不新增选择、不回滚当前选择 |
| 新 Python 进程恢复，SQLite 禁止读取聊天消息、轮次及事件表 | 恢复 A 保存 v1、B v1～v4、草案/任务 B v3、编辑 B v4、查看 A v1；status=complete |
| 读取前后比较三个数据库完整逻辑 dump | 完全一致；其他用户请求目录返回 404，无写入 |
| 新 Qwen 请求只读取恢复目录，不提供聊天历史 | 答对 A 保存版本、B 当前版本、任务实际版本、两种选择；明确不能把当前 v4 当成任务 v3 |

“已保存规则 A”是 `ruleset-proposal:f8c64eb8c0fc41ad803adad1d3187405`；“当前 B”是 `ruleset-proposal:758e7d0f8beb4c728f8cd54b4d675a67`。这些身份取自真实工具回执，没有为了符合预期手工改名或补造资源。

### 真实流程暴露的问题

**1. 指定名称没有落库，答复与真实名称不一致。**

要求生成“验收A旅游服务规则/词库”和“验收B交通服务规则/词库”，但协调模型的 `generation_request` 遗漏指定名称，生成结果用了其他名称。至少第一轮词库答复仍写“验收A旅游词库”，数据库实际为“克拉玛依旅游服务投诉调查”。初始按指定名称断言失败；之后使用真实创建回执里的 ID 继续检查，命名失败仍保留。此问题不在 1A/1B 新增读写路径内。

**2. 只使用主词的资源投影与草案校验存在冲突。**

第 6 轮 Qwen 调用 `update_resource_edit` 停用 B 词库的两个变体。工具成功并返回 `search_terms=[克拉玛依出租车, 克拉玛依停车]`。随后 `use_ruleset_proposal` 采用此 `resource_ref`，在解析后校验失败：`each enabled structured theme must contain at least one enabled variant`。

相关代码：`backend/resource_management/contracts.py` 的 `_search_entries` 支持无启用变体时回退主词；`backend/investigation_creation/contracts.py` 的 `structured_lexicon_matches_search_terms` 要求每个启用主词必须有启用变体；`backend/investigation_creation/tools.py` 先登记工具回执，再解析资源引用并进行这个校验。

**3. 该采用工具的同轮纠错仍被幂等冲突拦截。**

Qwen 随后自行改成 `temporary_terms` 的两个主词重试 `use_ruleset_proposal`，返回 `IDEMPOTENCY_CONFLICT`。`store.begin_tool_execution` 对这个工具仍走旧的同轮单条变更回执检查；`e8fdaef` 新操作/尝试机制只处理 `draft_operations.TOOL == create_investigation_draft`。这是另一个实际创建草案入口的恢复缺口，不能声称已有修复覆盖了它，也不能不经设计直接删除保护。

该失败轮数据库草案、任务均为 0。Qwen 最后称“展示 ID 已被消耗”，但工具回执的直接失败原因是前述参数校验和幂等冲突；不能把模型解释当作已核实根因。下一轮测试程序按助手要求补发明确确认，Qwen 才创建唯一草案。这是**额外确认后的恢复通过，同轮自动恢复失败**。

**4. “其余内容不变”发生了格式漂移。**

B v1→v2 的完整规则替换除 `audit_goal` 外，还改了一个豁免条件和四处规则文字中的引号；人工对比未发现这些额外变化改变规则含义，但逐字不变的要求没有满足。v2→v3、v3→v4 均只改变 `audit_goal`。保留原样要求应优先通过有字段边界的编辑工具实现，不能用流畅答复代替逐字段比较。

**5. 临时搜索词降级后，任务词库身份仍不可确定。**

恢复后的草案/Run 保留准确搜索词，但本次采用 `temporary_terms` 后没有可靠词库 ID/版本来源，目录明确返回 `lexicon.lineage=unknown`。1A 没有把相同词语猜成 B 词库，这是正确边界；但这条链路尚不能支持“准确保存任务用的那版词库”的承诺。规则的 B v3 关联则为 `verified_edit_version`。后续应把词库来源关联作为独立验收项。

额外的无历史 Qwen 请求也回答“不能确定就是交通词库 v2”，没有编造来源；这一主结论正确。答复中对不同结构的 `content_hash` 作比较，不能作为来源相同或不同的决定性证据，真正依据应是缺少 ID/版本及采用凭证。该解释局限也保留，不能把模型整段答复视为数据库事实。

### 本轮回归、运行方式与未覆盖范围

```sh
/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -B \
  scripts/resource_experiment.py tests/test_session_resource_state.py tests/test_resource_selections.py
```

结果：**33 passed，2 项既有依赖弃用警告，8.41 秒**。这是 1A/1B 定向回归，不是全项目回归或此前 457 项的重复运行。

真实模型运行器使用显式 `--coordinator-env`、`--generation-env` 和全新 `--output-dir`，只读取提供方白名单配置。首次命名失败后用 `--resume` 沿用真实资源；第 6 轮失败后再用 `--resume --confirm-after-failure` 追加确认。运行器保留失败，并在存在业务失败时返回非零，不能当作全绿验收。

尚未覆盖：真实浏览器+后端+Qwen 整体链路、报告完成后统一能力路由、真实采集/报告生成、生产旧库兼容扫描、多次随机模型运行的稳定率。本轮没有触发供应商 censor 或网络故障，因此未实测五次预算和流式故障恢复；相关业务代码未改，不能把未触发当作已验收。没有对规则内容的专业审核有效性作质量认证。

上述问题先做旧新版本归因，再决定必要修复范围；不能仅凭未直接经过新增代码，就排除新代码的间接影响。后续对照结果见下一节。

## 2026-10-08：真实调用的旧新版本故障归因

### 结论与实施决定

本轮暂停阶段 2，只新增离线重放运行器与验收记录，没有修改业务实现。**对这条已捕获的故障链，旧版与新版结果一致，没有发现 1A/1B 引入行为差异；这不等于所有新代码都没有回归。**

- 保留 1A：通过既有数据投影恢复资源，不重新维护资源内容或任务版本。
- 1B 保留现状、入口默认关闭。显式界面选择是可选交互，并非修复报告后资源管理的必要前提；不能要求用户先手工选资源才能自然对话。
- 不增加协调层、Prompt 内容或其他工具改动。优先审查阻断业务的主词校验契约和采用工具恢复，后续修复分别提交、分别验收。
- 对名称遗漏、全量替换导致的格式漂移，记录为独立问题，不借机扩大本轮开发。

需要纠正之前的概括：`e8fdaef` 只修复 `create_investigation_draft` 的操作级恢复，不能概括为所有创建草案入口都已覆盖。真实模型流程应更早进入基线验收，而不是等 1B 完成后才发现第二个入口的缺口。

### 可复现方法和证据

使用 `git archive` 分别导出 `e8fdaef177f1fde5263ae17d974cc928a9157fa4` 和 `6f295a2b03afe661aea600921b105494b2d852c3`，不切换现有工作树。运行器为 `scripts/replay_session_resource_faults.py`。

三组均使用全新隔离 SQLite、相同用户输入、同一份真实 Qwen 调用日志。协调模型调用与资源生成模型输出固定重放；只替换新数据库生成的资源 ID/引用，不改变业务参数。实际工具派发、参数校验、业务写入、展示与回执均执行各自版本代码。运行器清除提供方环境并禁止网络连接，因此本轮没有新增模型请求。

| 对照组 | 用户轮次 / 工具调用 | 实际新增选择 | 失败轮结果 | 追加确认后 |
| --- | --- | --- | --- | --- |
| e8fdaef 旧版 | 8 / 14 | 0 | 搜索词校验失败 → 幂等冲突；0 草案、0 Run、0 采用确认 | 1 草案 |
| 6f295a2 新版默认路径 | 8 / 14 | 0 | 与旧版相同 | 1 草案 |
| 6f295a2 新版含 1B 选择 | 8 / 14 | 3 | 与旧版相同 | 1 草案 |

三组逐轮工具结果、资源正文与版本、草案/Run/采用确认数量一致。第三组实际记录编辑 B、查看 A、选择 B v3，避免仅测试开关关闭的路径。最后均由测试程序冻结一个 Run，再重放 B v4 修改，不启动 worker。

证据目录：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/resource-fault-attribution-20261008/`。各组 `replay.json` 保留参数映射、回执、资源正文和计数；`comparison.json` 保留对比及关键源文件 SHA256。源日志为前节 `evidence.json`，各组记录相同源文件哈希。

复现命令（将 REPO 和 OUTPUT 分别替换为导出代码与全新输出目录；第三组增加 `--with-selections`）：

```sh
/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -B \
  scripts/replay_session_resource_faults.py \
  --repo REPO \
  --evidence /Users/ext.wanghongtao6/Documents/Codex/acceptance/session-resource-qwen-20261008-run1/evidence.json \
  --output OUTPUT
```

### 逐项归因

| 现象 | 本轮证据与归因 | 结论边界 |
| --- | --- | --- |
| 指定名称未传给生成工具 | 捕获的 `generation_request` 已缺名称；两版保存相同生成结果，生成接口及 Prompt 源文件相同 | 原有 Agent/工具契约问题被暴露；固定输出不能证明旧模型独立运行时必然同样遗漏 |
| 修改目标字段时改变引号 | 捕获的全量更新参数已包含额外变化；两版均原样保存 | 既有全量编辑方式缺少字段保护；不是目录读取改写正文 |
| 主词可投影、却不能采用 | 两版资源工具均返回主词，采用入口均要求每个启用主题含启用变体 | 旧契约不一致，已确定性复现 |
| 修正后幂等冲突 | 两版 `use_ruleset_proposal` 同轮改参均冲突，失败时无草案或采用确认 | 旧入口的恢复缺口，未走 e8fdaef 新操作逻辑 |
| 模型不知道界面选择 / 报告后不能管理资源 | 产品 Qwen 尚未接入目录及选择，报告入口仍有阶段限制 | 尚未实现的目标能力，不能称为 1A/1B 的端到端验收通过 |
| 临时搜索词无法证明来自词库 B v2 | 原调用纠错后采用 `temporary_terms`，未提供词库来源身份 | 既有调用路径缺少来源事实；1A 返回未知是正确边界，不应猜测 |

### 幂等冲突的实际因果链

1. `use_ruleset_proposal` 的外层参数含合法 `resource_ref`，初步校验通过，先创建工具执行回执。
2. 解析引用得到完整词库后再次校验，因无启用变体失败；此时还未执行草案写入。错误以 `TOOL_EXECUTION_FAILED` 返回，未携带可靠的“明确未写入”恢复语义。
3. Qwen 将参数改成 `temporary_terms`，同轮再次调用该工具。
4. `store.begin_tool_execution` 对此工具仍按同会话、同轮、同工具检索既有变更回执；参数指纹不同，直接返回 `IDEMPOTENCY_CONFLICT`。未进入 `draft_operations`，因此不是该模块的某个新错误类型漏进可重试名单。
5. 三组失败轮均没有 `ruleset_proposal_approvals`。Qwen 所称“展示 ID 已消耗”不符合实际记录。

未来修复不能简单允许所有 FAILED 重试，也不能删除唯一约束或逐个添加错误文案特判。需要先明确采用入口的状态边界：写入前失败可纠正，但仍须验证展示及用户确认；成功返回原结果；执行中或结果未知先核查。`use_ruleset_proposal` 同时承担采用确认和草案写入，复用操作恢复时必须保留这些约束，不能直接绕到普通创建入口。

### 原修复专项回归

在两个导出目录各运行同一命令：

```sh
/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -B \
  scripts/resource_experiment.py tests/test_draft_creation_recovery.py
```

两版均 **14 passed，2 项相同依赖弃用警告，3.35 秒**。覆盖旧规则纠正、并发、提交后回执丢失、未知状态、事务回滚、进程恢复和旧记录迁移。证明原入口修复在这些用例中仍有效，不能用这 14 项替代采用工具的专项测试。

### 1B 的权威边界与剩余风险

资源正文以编辑历史及正式资源版本为准；草案采用以草案修订及采用确认为准；任务实际内容以冻结 Run 为准。1B 事件只表示明确选择的查看/编辑对象及版本，不保存另一份正文，也不授予保存、采用或启动权限。当前业务工具未读取它，所以本轮未出现两套采用状态争夺权威的问题。

这只能证明当前边界，没有证明新增选择界面是原目标的必要投入。进入下一步之前，应先明确产品入口最小需要哪些已有查询，而不是继续增加状态表和选择步骤。

限制：固定调用重放隔离了模型波动，不比较两版真实模型的错误概率；数据库是相同构造的新库，不是生产库副本；没有浏览器完整链路、生产并发、供应商故障注入或全项目回归。本轮未修改 censor 预算、Qwen 风格或幂等业务代码。前一轮独立 Qwen 恢复探测已经完成，但业务流程仍有失败，不能把探测完成或三组成功复现故障说成业务验收通过。

## 再次细审：以当前产品语义核查端到端边界（2026-10-08）

### 审查范围与结论修正

当前审查基线为 `6f295a2b03afe661aea600921b105494b2d852c3`，分支 `codex/continuous-resource-design-20261008`；对照为原幂等修复 `e8fdaef177f1fde5263ae17d974cc928a9157fa4` 的导出源码。没有切换工作树、修改业务代码、调用云端或新发 Qwen 请求。写入仅发生在合成的隔离测试库及本地审查证据文件，既有未提交工作保留。

本次必须纠正的前提：

1. **主词只分类，实际搜索使用启用变体。** 保存不完整编辑稿可以成立，但不能因此允许用分类主词创建可执行搜索配置。此前验收程序主动要求“只用主词、不用变体”不符合当前需求；应保留原日志并将该输入视为负向契约测试，不能据此放宽创建校验。
2. 恢复缺口不只在 `use_ruleset_proposal`。普通创建对 `RESOURCE_REF_STALE` 也无法恢复，两个入口之间还没有共享的一次创建保障。
3. 系统已经有局部修改工具、无变化不升版判断、保存回执和任务冻结快照。不能把调用错工具、序列化改写或结果衔接缺失，误判为这些基础能力不存在。
4. 临时词库来源身份缺失不仅发生于降级到裸 `temporary_terms`：合法 `resource_ref` 成功采用也有此缺口。但其完整内容仍被保存，不能说任务词库内容丢失。
5. `read_report` 是报告工具集中真实存在的工具。应核对特定阶段实际暴露的工具与调用参数，不能把它整体认定为不存在，更不能因此新增 `shell` 权限。

### 优先级定义

- P1：会创建额外草案、让分类主词进入待执行搜索配置、阻断安全纠错或使已提交业务结果不可正确恢复；先于统一会话入口修复。
- P2：精确编辑、无操作升版、来源追溯、报告后能力衔接等问题；应解决，但分别复用已有能力并独立验收。
- 以下为本地代码与隔离复现结论，不等于已经确认线上发生了每一种故障；没有证据支持宣称生产数据损坏或 P0 紧急事故。

### R01 · P1：两个创建入口之间不能保证一次创建只落一个草案

代码：`backend/investigation_creation/store.py:465` 只把 `create_investigation_draft` 接入 `draft_operations`；其余变更按工具名检索回执。`use_ruleset_proposal` 查找的是采用确认所关联的草案，未与普通创建操作共享结果。

复现：在一次用户明确要求“创建一个草案，不启动”的轮次内，构造普通创建和采用规则两个有效工具调用。分别测试两种执行顺序，均返回成功，数据库均有 **2 个草案**。两次调用使用不同规则配置，因此这不是“同参数重放失败”，而是模型在完成同一个创建请求时切换入口的防重复边界缺失。未启动任务。

影响：单个入口内部的幂等测试通过，仍不能保证 Agent 跨工具纠错或重复操作时只产生一个结果。该异常轨迹是本次构造的压力场景，不能说真实 Qwen 或线上已经这样调用。

最小修复方向：先定义应用所有的一次创建操作身份，让两个入口共享创建结果与写入一致性；保留采用入口的展示、版本、确认约束。成功后不同参数不能静默另建，明确新建意图才允许另开操作。不能仅靠工具名隔离，也不能只把所有 FAILED 改成可重试。

### R02 · P1：普通创建把明确未写入的过期引用判为 UNKNOWN

代码：`backend/resource_management/edit_refs.py:44` 抛出 `RESOURCE_REF_STALE` 并明确 `mutation_applied=False`；`backend/investigation_creation/draft_operations.py:40` 的 `known_no_write` 只识别另外三个错误码。

复现：词库 v1 引用过期后普通创建失败；读取 v2 引用并在同一操作纠正重试，得到 `MUTATION_RESULT_UNKNOWN`。数据库 **0 草案**，操作却保持 UNKNOWN。第一份回执同时含 `mutation_applied=false` 与 `write_status=UNKNOWN`。

修复方向：由经过验证的写入阶段产生统一的执行状态，区分校验失败、事务回滚、已提交、执行中及结果未知；不要扩展成逐个错误字符串补名单。权限失败与写入未知仍不可自动换参数重试。原 `e8fdaef` 的 14 项专项覆盖并未证明公开 `resource_ref` 路径可恢复。

### R03 · P1：采用入口的合法纠错仍被旧幂等记录阻止

代码：`backend/investigation_creation/tools.py:645` 先做外层校验，登记工具回执后在 `:511` 解析资源引用并再次校验；失败后 `store.begin_tool_execution` 仍以旧参数指纹锁定同轮采用操作。

本次使用符合产品要求的负向／纠正样本：启用分类下所有变体停用 → 采用被拒绝且无写入 → 明确启用一个具体变体 → 使用新引用重试。结果仍是 `IDEMPOTENCY_CONFLICT`，**0 草案、0 采用确认**。

第一次拒绝是正确行为，第二次无法纠正才是缺陷。无需放宽“变体才能搜索”的校验。修复应与 R01/R02 共用经过验证的创建恢复核心，同时保留采用授权。

### R04 · P1：采用已提交，后续预览失败却缓存整个操作失败

代码：`store.py:875` 一带事务提交草案修订和采用确认；`tools.py:544` 随后生成 `confirmation_preview`。采用入口没有普通创建已有的成功结果恢复分支。

复现：在提交后注入预览异常，数据库已有 **1 草案、1 采用确认**，工具返回 `TOOL_EXECUTION_FAILED`，细节为空。移除异常、以完全相同的调用身份重试，仍返回缓存失败。

此路径本次没有产生第二个草案，但会让模型和用户无法从该工具回执判断已经创建成功。应从持久化创建结果恢复草案，预览生成失败单独记录；不能据此重新执行创建。

### R05 · P1：主词回退仍会进入正式词库的可确认草案

代码与矛盾：

- 前端 `Audit_assistant/src/features/investigation/lexiconDraft.ts:46` 已只投影启用变体，并要求启用主题有启用变体。
- 后端 `backend/resource_management/contracts.py:90`、`backend/audit_agent/lexicon_store.py:780` 在无可用变体时回退主词。
- `backend/resource_management/tools.py`、`authoring_guidance.py` 和 `backend/investigation_creation/conversation.py:75` 还明确教模型使用该回退。

复现：新建现代结构化词库，主词为“交通服务分类”，唯一变体“出租车绕路”停用。临时采用被正确拒绝；同一份内容正式保存后再采用却成功，预览 `can_confirm=true`，实际配置搜索词为“交通服务分类”。未确认启动或执行爬虫。

所以这不只是历史旧词库影响，也不应称为“主词不能创建草案”的 Bug。真正的问题是分类标签可以沿正式路径进入搜索。保存编辑稿与允许用于搜索需分开校验；临时、正式、前端、预览和执行准备应遵循同一投影规则。

附带确认：正则变体在临时资源和保存回执中进入 `search_terms`，正式读取却过滤正则并回退主词。应统一搜索投影，保留正则匹配和搜索输入的不同用途。不能仅凭旧字段名 `enabled_main_terms` 全局替换：部分调用实际通过它承载已经投影的搜索词。

历史数据处理：不覆盖旧任务冻结内容，不自动把分类名复制成变体。旧资源如确无可用搜索词，应在新采用时给出可理解的原因；是否做显式迁移另行决定。

### R06 · P1（接通资源写入前）：保存成功但模型末尾回复失败，未恢复业务成功

代码：`backend/investigation_creation/conversation.py:1248` 的已验证成功兜底只接受 `investigation_draft`、`investigation_run`，未覆盖资源保存回执。

复现：真实业务工具完成 `save_resource`，随后由模拟模型注入最终回复失败。`get_save` 返回 saved、正式版本存在，但整轮状态为 error。

现有成功工具 checkpoint 和下一轮恢复上下文仍有价值，不能说系统完全没有保存恢复机制。缺的是当前失败轮对持久化保存事实的核验及准确告知。应复用现有保存回执，不能为了生成一句答复再执行一次保存。资源库与对话回执库分离，因此也不能声称二者已经处于同一事务；跨库提交窗口需补专门故障测试。

### R07 · P2：不编辑也升版，原因包含前端序列化改写

代码：`Audit_assistant/src/services/resourceLibrary.ts:42` 读取规则，`:59` 写回规则，`:67` 和 `:75` 将原始分类／规则 order 改为数组索引。`KnowledgeCenterPage.tsx:95` 虽计算 dirty，但保存并不以此拦住无操作写回。后台 `_save_ruleset` 和 `update_ruleset_proposal` 已有相同内容不升版判断。

复现采用实际 TypeScript 函数，而非 Python 仿写：标准内容 → `ruleSetView` → 未编辑 → `rulesContent` → 真实保存服务。原 order 为 10/20、1/2，被改为 0/1；发布版本 **v1 → v2**。直接用完全相同后台内容保存则不升版。两版前端文件哈希相同。

边界：这证明一个确定的本地机制，不能单凭它断言此前生产那次升版一定由该机制触发。原样回传需保留所有不由界面编辑的字段；当前样本中省略的空 `source_mappings` 会由模型补回默认值，不应把省略空数组也算成实质数据丢失。非空来源映射、空注释等边界尚须专项验证。

### R08 · P2：精确修改工具已存在，旧提示却仍引导全量替换

代码：`backend/resource_management/service.py:291` 已支持 `set_metadata`、版本检查和局部字段更新；`backend/investigation_creation/conversation.py:247` 仍要求把完整修改后规则传给 `update_ruleset_proposal`。

复现：只 patch `audit_goal`，其余字段逐项相等；重复相同 patch 不升版。此前真实 Qwen 捕获的额外引号变化已经在模型发出的全量参数中，不能归因于资源目录读取。

修复方向：小范围编辑优先调用现有 patch；需要整体替换时仍允许对应工具。修正冲突的调用约定，不重做编辑系统，不重写自然语言回答风格。后端确定性测试不能替代“Qwen 是否选择正确工具”的实际多轮验收。

### R09 · P2：合法临时词库采用保留正文，但未保留可直接恢复的编辑来源

代码：`backend/resource_management/edit_refs.py:47` 展开引用后保留正文和部分正式资源信息，未持久化临时 `edit_id/edit_version/resource_ref`；`session_state.py:198` 对没有正式来源的采用返回 unknown。

复现：使用合法启用变体和真实临时引用成功采用，草案 `lexicon_content` 与编辑稿规范化正文完全一致，但上述来源身份不在冻结召回配置中。

影响：仍可使用已有 `save_draft_lexicon` 保存任务所用词库内容；但不能仅凭相同正文认定它来自候选 B 的某一版本，尤其存在同内容不同候选时。若要求目录精确标示候选来源，应复用现有引用身份补关联，不能再存另一份权威正文。用户直接提供临时词而没有原词库时，来源未知本身是合法情况。

### R10 · P2：报告后能力限制确实跨前端、后端和提示三层

代码：`InvestigationPage.tsx:2266` 在存在 `reportBinding` 时把消息交给报告路径并提前返回；`conversation.py:1156` 对 PUBLISHED 状态直接结束创建路径；System Prompt 的报告阶段约定也限制继续操作。

因此只移除一个前端 if，或只改一句 Prompt，都不足以完成连续会话。应先修上述写入与恢复边界，再把报告关联与当前允许的业务能力分离。保留报告原有查询、证据权限与只读边界，继续冻结已启动任务的配置；独立资源编辑不等于修改旧任务。

已有运行时对报告工具注册的检查、跨工具集切换测试和跨用户隔离测试可复用。`read_report` 在报告工具集中存在；历史 `shell`／错误调用问题本轮未重新用真实模型复现，不另列为本轮新证实的故障。

### 1A / 1B 及既有能力应如何处理

| 能力 | 本次核查结果 | 处理建议 |
| --- | --- | --- |
| 1A 资源目录 | 只读连接、权限范围、读取失败不伪装为空；相关隔离与新进程恢复测试通过 | 保留；跨库不是同一快照，写入前继续验证版本 |
| 1B 选择 | UI 默认关闭；选择 API/表并不因此消失，当前业务工具尚不消费该选择 | 停止面板产品化；不让用户选择面板成为正常对话前提，不急于删表或扩大其权威 |
| 编辑与冻结版本 | 已有编辑历史、采用确认、草案修订、Run 冻结内容 | 复用；当前编辑版本不能替代任务实际内容 |
| 局部编辑 | 已有版本保护和精确 patch | 修调用契约，避免重复建设 |
| 保存回执 | 正式保存已有持久回执和幂等检查 | 补模型失败后的结果恢复 |
| censor 机制 | 最多 5 次模型请求，包含首次；限定输入检查失败，阻止流式／非流式预算叠加 | 保留；不把模型请求重试升级成整轮业务写入重放 |

1A 当前会先读取资源及历史再对目录分页，数据量很大时可能增加查询成本；本次没有规模性能测量，不作为急需重构理由。1B 没有进入业务决策，因此不能据当前测试宣称它已经解决自然语言指代。

### 最小修复批次与验收门槛

1. **搜索契约**：分类主词不搜索；临时、正式、预览和调用约定统一；覆盖全部变体停用、部分启用、主题停用、正则、重复变体、旧资源和任务冻结不变。不能以删掉正确校验作为修复。
2. **一次创建的执行边界**：两个入口共用创建结果与恢复保证，分别保留入口授权。测试同轮同入口纠错、跨入口调用两种顺序、并发、成功后改参、提交后预览失败、进程终止、未知状态、旧记录迁移。不是泛化所有业务工具，也不取消幂等。
3. **精确编辑与保存结果**：复用 patch，修前端无操作往返保存，补资源保存成功／模型回复失败和回执查询。对未修改字段与历史任务冻结配置做逐项或哈希对比。
4. **资源身份与连续会话**：只补真正缺少的来源关联；接通 1A 的按需发现／读取，然后实现报告问询 → 编辑另一资源 → 回到原报告。无需强制面板选择，不增 shell，不重写系统 Prompt，不扩大为一会话多调查。

每批独立验收、提交、回退。写入前明确未执行、已提交、结果未知的语义要成为业务返回契约，不能交给 Qwen 凭文字判断。数据库事实负责资源版本与结果，模型负责理解目标；模糊时才澄清。

### 本轮执行结果与复现材料

证据目录：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/architecture-review-20261008/`。

| 执行组 | 结果 | 能证明什么 |
| --- | --- | --- |
| 当前版本既有恢复、采用、资源、编辑器、1A/1B、censor 测试 | 170 passed，21.91 秒 | 所选既有断言仍成立 |
| 当前版本私有资源、注册切换、已发布状态、checkpoint、跨用户报告测试 | 23 passed，4.77 秒 | 所选隔离、恢复、工具注册行为未失败 |
| 当前版本新增诊断探针 | 13 passed，2.16 秒 | 重现本文故障及已有保障；并非缺陷已修 |
| e8fdaef 对照版同一诊断探针 | 13 passed，2.51 秒 | 这些确定性行为在 1A/1B 前已经存在 |

各组均有两项相同的第三方依赖弃用警告。**193 项既有回归通过不代表整个产品正确；13 项探针的断言包括“出现两个草案”“错误被缓存”，其通过代表故障复现成功。** 第一轮探针有两项测试假设错误（规范化默认字段、失败轮状态名称），在检查实际数据后修正，原 `probes.log` 保留；最终以 `probes-final.log` 和 `probes-old.log` 为准。

`run_audit.py` 清除模型凭据、关闭 dotenv、禁止网络连接，并为运行目录和测试数据库提供临时空间。`test_review_probes.py` 是本轮的 13 个确定性探针；`probe-evidence.json`、`probe-evidence-old.json` 保留逐项结构化回执；`source-comparison.json` 记录关键源码哈希，部分 store 新增内容不同，其余所列关键文件相同。

核心复现命令：

```sh
PY=/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python
EVIDENCE=/Users/ext.wanghongtao6/Documents/Codex/acceptance/architecture-review-20261008
"$PY" -B "$EVIDENCE/run_audit.py" "$EVIDENCE/test_review_probes.py" -s
"$PY" -B "$EVIDENCE/run_audit.py" --repo /Users/ext.wanghongtao6/Documents/Codex/acceptance/resource-fault-attribution-20261008/old-source "$EVIDENCE/test_review_probes.py" -s
"$PY" -B "$EVIDENCE/run_audit.py" tests/test_draft_creation_recovery.py tests/test_ruleset_proposal_approval.py tests/test_resource_snapshot_refs.py tests/test_resource_lifecycle.py tests/test_resource_library_editor.py tests/test_session_resource_state.py tests/test_resource_selections.py tests/test_creation_input_retry.py
"$PY" -B "$EVIDENCE/run_audit.py" tests/test_private_resource_lifecycle.py tests/test_investigation_creation_conversation.py::test_real_hermes_registry_switches_creation_report_creation_without_tool_loss tests/test_investigation_creation_conversation.py::test_published_creation_session_directs_new_investigation_to_new_session tests/test_investigation_creation_conversation.py::test_partial_failed_turn_exposes_only_successful_checkpoints_to_followup tests/test_investigation_creation_conversation.py::test_m3_report_and_resource_routes_hide_cross_principal_resources tests/test_investigation_creation_conversation.py::test_published_report_handoff_reuses_internal_run_anchor_without_exposing_session
```

前端复现：使用 `editor-roundtrip.cjs` 执行实际 TypeScript 序列化函数，API stub 禁止网络，结果与 `editor-roundtrip.json` 校验一致；Python 探针再将序列化结果写入真实隔离资源服务。可用本机缓存 Node 执行该脚本。不是浏览器端到端操作，不冒称浏览器验收通过。

### 尚未覆盖的风险

- 本轮没有新运行真实 Qwen 多轮对话，不能给出自然语言指代、调用工具选择或回答风格的成功率。此前真实调用日志用于归因参考，不等于新方案通过。
- 新增跨入口用例是顺序双入口测试，不是进程级并发／断电测试；既有并发恢复测试只覆盖它原先的入口。
- 模型回复失败为模拟注入；未实际触发供应商 censor、限流、网络丢包，未测生产负载。
- 未用生产数据库副本重放全部历史迁移，未覆盖所有旧 schema 组合。修复操作模型后必须新增两入口历史迁移与中断恢复测试。
- 非空规则来源映射的前端保留、跨库保存已提交但对话回执未提交、超大会话目录性能仍需针对性检查。
- 本轮未部署、未推送、未提交、未改业务代码或既有运行数据。结论是明确修复边界，并非宣称全部问题已经修好。

## 后续实施：R01–R04 草案创建可靠性（2026-10-08）

本节记录上述诊断之后的实际修复，前文“探针通过仅表示复现”的结论保留。此前词库语义已按用户最新确认统一为：启用变体优先；没有启用变体时回退主词；主词停用则排除整组。前文“分类主词绝不搜索”的历史建议不再作为产品要求。

### 本批边界与实现

仅修复两个创建入口的重复创建、明确未写入后的纠错、采用入口的恢复、提交成功但预览或回执失败这四项问题。没有开放连续会话、改造资源选择面板或扩展其他写入工具。采用到已有草案仍走原更新路径，展示、用户轮次、版本及权限校验保留。

| 文件 | 本批修改 |
| --- | --- |
| `backend/investigation_creation/draft_operations.py` | 两个创建入口共用应用轮次下的操作记录；分离 NOT_STARTED、COMMITTED、UNKNOWN；旧回执恢复与增量迁移 |
| `backend/investigation_creation/store.py` | 草案、规则采用、操作成功关联在同一事务提交；读取最初创建修订；向对话提供恢复后的真实成功结果 |
| `backend/investigation_creation/service.py` | 仅在确定写入前或已回滚的边界标记可纠错失败；传递创建操作身份 |
| `backend/investigation_creation/tools.py` | 采用入口接入相同恢复机制；已提交时读取原草案，不重复创建或采用 |
| `backend/investigation_creation/conversation.py` | 仅补异常兜底：区分采用未完成与已保存但预览暂不可用；正常回答风格不变 |
| `tests/test_shared_draft_creation_recovery.py` | 新增跨入口、纠错、并发、真实进程退出、授权、历史记录及迁移回归 |
| `tests/test_draft_creation_recovery.py` | 将预览失败注入点调整到实际预览服务，覆盖初次执行及恢复 |

两个入口在同一应用轮次中共享创建边界。第一个已经成功，第二个入口或不同参数不会被当成成功采用；返回已提交状态及现有草案 ID，引导读取或明确指定已有草案修改。未推断跨用户轮次的“再试一次”意图，也未实现一会话多调查。

操作表增加 `result_json` 列，用于保留经验证的成功恢复结果；原始失败回执保持不变。调整既有回执唯一索引，由操作记录协调创建尝试。没有新增资源状态表，没有复制任务冻结配置。迁移在隔离数据库测试；未运行到现有业务数据库。

### 验收结果

全部使用 `acceptance/architecture-review-20261008/run_audit.py`：隔离数据库、禁用 dotenv 和网络，不调用云端或真实模型。测试断言检查实际草案、采用、运行及回执记录，而不只检查工具返回状态。

- 广泛回归：**463 passed，23 subtests passed**。覆盖创建恢复、采用与展示、会话、临时词库引用、censor 请求预算、Prompt 约束、1A 只读资源恢复、词库语义及私有资源权限。
- 随后新增“旧操作表升级、迁移中断/并发启动、已有 RUNNING 任务不变”用例：**1 passed**。
- 最后补异常提示后，重跑受影响组：**181 passed**。与上面覆盖重叠，不累加为新的测试总数。
- `git diff --check` 通过。测试仅有现有第三方依赖及 FastAPI 弃用警告。

关键验证包括：两入口两种调用顺序仅一个草案；同轮陈旧引用或无有效搜索词失败后合法纠错；提交后预览失败/回执丢失重开数据库恢复；六线程跨入口并发；跨用户资源与过期展示拒绝；事务绑定失败全量回滚；旧 SUCCEEDED/FAILED/STARTED 采用回执恢复；恢复初始修订不冒充后来的修改；迁移不改变历史草案、回执、任务及冻结配置。

真实子进程退出测试区分两个窗口：提交前退出时 SQLite 回滚，但不擅自认定操作可以重试，保持未知结果保护；提交后退出时从数据库恢复唯一草案。权限或展示失败即使明确未写入，也不建议自动重试。

运行命令（在 `cloud-triage-deploy/xhs-audit-agent-demo` 工作树）：

```sh
PY=/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python
RUN=/Users/ext.wanghongtao6/Documents/Codex/acceptance/architecture-review-20261008/run_audit.py
"$PY" "$RUN" tests/test_shared_draft_creation_recovery.py tests/test_draft_creation_recovery.py tests/test_ruleset_proposal_approval.py tests/test_ruleset_proposal_presentation.py tests/test_investigation_creation_conversation.py tests/test_temporary_lexicon_refs.py tests/test_creation_input_retry.py tests/test_creation_prompt_isolation.py tests/test_investigation_creation_m3.py tests/test_investigation_draft_configuration_m3.py tests/test_confirmation_tool_recovery.py tests/test_session_resource_state.py tests/test_lexicon_search_semantics.py tests/test_private_resource_lifecycle.py --tb=short
"$PY" "$RUN" tests/test_shared_draft_creation_recovery.py tests/test_draft_creation_recovery.py tests/test_ruleset_proposal_approval.py tests/test_investigation_creation_conversation.py tests/test_creation_prompt_isolation.py --tb=short
```

第一条记录为新增最后一项迁移用例及异常提示前的 463 项；第二条为最终受影响组 181 项，包含新增迁移用例。

### 尚未覆盖与保留限制

- 未重新运行真实 Qwen 多轮验收，不能据后端回归承诺模型一定选对工具、自然语言指代完全正确或所有回复均正确。
- 使用构造的历史数据库验证迁移，没有生产库副本验收；进程退出测试不等于机器断电或磁盘损坏测试。
- UNKNOWN 不自动解除，需先核查真实写入状态；跨轮次重试意图没有新增自动推断。
- 对已有草案的规则更新、通用资源保存兜底、无意义升版、资源来源关联、报告后的资源管理不在本批修复范围。
- 未提交、推送、部署、重启服务；未删除或重置现有数据。已有词库语义修改与其他未提交文件保留。

## 真实 Qwen 验收与失败归因（2026-10-09）

**结论：四项创建可靠性的目标场景通过；扩大到“模型回复失败兜底后的下一轮追问”时失败，整条恢复链路暂不判为全通过。** 本轮没有修改业务代码、Prompt、模型参数或运行时重试机制，只增加/完善验收运行器和记录。

### 环境与方法

- 运行器：`scripts/draft_recovery_qwen_acceptance.py`；使用现有产品 Hermes 运行时、工具及 `qwen3.7-plus`，不是假模型输出。
- 各场景独立建立合成资源、调查和对话数据库；规则/词库准备及完整展示用已有 fixture 工具执行，之后的创建、纠错及追问由真实 Qwen 完成。不是从自然语言生成资源开始的全产品验收。
- 仅从显式配置文件读取 DashScope 凭据；未配置独立资源生成服务。禁用 dotenv 自动加载，数据库、Hermes 状态和输出指向新建验收目录；未启动爬虫或 worker。
- 版本变化、预览异常和模型连接异常由测试在指定位置注入。未实际制造供应商 censor 或线上断网。跨入口第二次创建是测试主动触发并把真实冲突回执交给 Qwen，不能声称模型自然选择了两个入口。
- 5 次运行合计记录 55 次模型调用尝试，其中 6 次是请求发出前注入的连接失败，49 次实际调用提供方。重复运行用于补足环境、修正记录遗漏和旧新对照，不作为独立覆盖数量累加。

### 场景结果

| 场景 | 实际观察 | 结论 |
| --- | --- | --- |
| 已发布规则在创建前升版 | 首次创建触发未写入错误，Qwen 读取后采用 v2；同轮一个草案 | 通过，run2 |
| 临时词库引用在采用前过期 | `use_ruleset_proposal` 返回 `RESOURCE_REF_STALE` → `get_resource_edit` → 再次采用成功 | 通过，run1/stale_lexicon |
| 已成功后触发另一创建入口 | 第二入口返回 COMMITTED 和原草案 ID；Qwen 读取已有草案，没有第二次写入 | 通过，run3/cross_entry |
| 草案已提交，但生成确认预览异常 | 回执说明 COMMITTED，Qwen 恢复同一草案；后续追问也成功 | 通过，run1/preview |
| 草案已提交，此后模型连接持续失败 | 应用依据成功检查点给出“草案已保存、尚未采集”兜底 | 当轮通过 |
| 上述模型失败兜底后继续追问 | 草案仍然唯一，但对话历史校验失败，追问无法正常完成 | 未通过；run3、run4 与旧基线对照均复现 |

逐一读取 SQLite 核对：成功场景草案状态均为 DRAFT、修订号 1、平台 dy、实际搜索词仅“招聘收费”、运行数 0。临时规则采用记录的版本和内容哈希与草案一致；正式规则采用了 v2。各库 `PRAGMA integrity_check` 为 ok。这些是单样本/定向故障场景，不能推导模型长期成功率。

### 保留的初次失败及测试修正

- run1/stale_rule 在到达草案创建前尝试生成临时词库，遇到 `RESOURCE_GENERATION_UNCONFIGURED`。属于验收环境与准备不足，未覆盖目标故障；随后预置已有词库重跑，通过。没有为让测试通过而改变产品工具或 Prompt。
- run1/cross_entry 的断言错误地只从 mutation receipt 中查找只读工具，漏记了实际执行的 `get_investigation_draft`。持久化 transcript 证明该调用存在；运行器改为读取实际 transcript，run3 重跑通过。原失败 evidence 保留，没有改写成 PASS。

### 追问失败的根因与归属

当前 `conversation.py` 的成功兜底分支将 `_checkpoint_messages_for_turn()` 返回的尾部助手提示与最终兜底答复同时写入历史，形成连续两条 assistant 消息。下一轮 Hermes 的 `repair_message_sequence_with_cursor()` 合并它们，导致消息数量/位置变化，`_validate_completed_transcript()` 的精确历史校验拒绝该结果。

观测文件中，下一轮期望历史有 11 条，末尾两条都是 assistant；实际返回中它们被合并，第 10 位已经变成当前用户消息。不是 Qwen 决定重新创建，也不是数据库幂等冲突。

用相同运行器和同一本地 Hermes 依赖对照旧应用源码 `e8fdaef`，也出现完全相同的异常。对照源码的 `conversation.py`、`backend/hermes_runtime/service.py`、基础恢复测试与 `git show e8fdaef` 内容哈希一致。结论限于：**旧应用基线搭配当前相同依赖已存在该缺陷，本次四项修改不是必要触发条件**；不据此断言历史线上已发生相同问题。

另用真实 Hermes 消息整理函数做离线确定性验证：当前与旧应用各 **2 passed**。第一项断言成功复现原错误；第二项只在内存里去掉重复助手尾部、保留所有工具回执，证明无需放宽历史校验即可通过。这是修复方向验证，不是业务代码已经修复。

建议下一批只处理成功兜底历史的组装：以一条合法的最终助手消息收尾，保留真实回执及不得重复写入的上下文；继续保留精确历史校验。不要通过吞异常、关闭校验或重放创建操作绕过错误。修复后重跑“连接失败 → 成功兜底 → 下一轮读取”及原有失败检查点恢复回归。

### 证据与复现

- 各轮证据目录：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/draft-recovery-qwen-20261009-{run1,run2,run3,run4,old-e8fdaef}/`。
- 汇总及数据库只读核对：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/draft-recovery-qwen-20261009-summary.json`。
- 对话历史差异：`run4/model_reply/recover.history-validation.json`；原始完整历史：`run4/model_reply/create.transcript.json`。
- 离线诊断：`/Users/ext.wanghongtao6/Documents/Codex/acceptance/test_checkpoint_history_diagnosis_20261009.py`。

```sh
PY=/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python
# --coordinator-env 指向本地已有凭据文件；--output-dir 必须是全新目录。
"$PY" -B scripts/draft_recovery_qwen_acceptance.py --coordinator-env "$ACCEPTANCE_ENV" --output-dir "$ACCEPTANCE_OUTPUT" --cases model_reply
# 旧应用对照另加 --source-root /Users/ext.wanghongtao6/Documents/Codex/acceptance/resource-fault-attribution-20261008/old-source
"$PY" -B /Users/ext.wanghongtao6/Documents/Codex/acceptance/architecture-review-20261008/run_audit.py /Users/ext.wanghongtao6/Documents/Codex/acceptance/test_checkpoint_history_diagnosis_20261009.py --tb=short
```

本次未测试资源生成模型、实际供应商内容审核拦截、真实账号采集或线上服务；没有提交、推送、部署，也没有删除或重置任何已有业务数据。

## 成功兜底后的历史续聊修复（2026-10-09，独立后续批次）

### 修改范围

- `backend/investigation_creation/conversation.py`：成功兜底保留工具调用与结果，以一条正式助手答复收尾。部分失败、仍有未完成工作的恢复说明继续保留。运行时接收历史的深拷贝，校验与本轮消息截取共用整理后的独立参考副本，防止运行时原地修改连同校验基准一起改变。
- `backend/hermes_runtime/adapter.py`：旧记录只在读取副本上处理连续、无附加元数据的纯文本助手消息，实际合并复用 Hermes 的 `repair_message_sequence`。原始工具协议先校验；用户消息、工具链、多模态或额外元数据不进入此兼容合并。不覆盖历史库，不放松返回历史的前缀校验，不新增表或幂等机制。
- `tests/test_creation_history_recovery.py`：新增恢复、冷建服务、原始记录不变、工具链不变、用户/工具内容被运行时修改时仍拒绝、当前轮记录不混入旧工具调用的测试。
- `scripts/draft_recovery_qwen_acceptance.py`：添加仅面向新建隔离数据库的 `--legacy-checkpoint-history` 验收选项；复现旧结构后重建会话服务和 Agent，从 SQLite 继续真实 Qwen 对话。
- `docs/product-baseline-r0/HERMES_RUNTIME_MANIFEST.md`：记录新增的窄范围内部 helper 依赖。已核对本地运行时源提交与 requirements 固定的 `e624e9f` 一致，未改 Hermes 源码。

本批没有修改 Prompt、censor 重试、草案幂等、资源选择面板或线上服务；工作区原有词库与 R01–R04 修改继续保留。

### 自动测试

Python 为 `/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python`。

```sh
python -m pytest -q tests/test_creation_history_recovery.py tests/test_investigation_creation_conversation.py tests/test_draft_creation_recovery.py tests/test_shared_draft_creation_recovery.py tests/test_r021_session_scoped_runtime.py tests/test_creation_prompt_isolation.py
# 150 passed，12 subtests passed。

python -m pytest -q tests/test_creation_history_recovery.py tests/test_creation_input_retry.py tests/test_public_answer_streaming.py tests/test_investigation_streaming_feature_flags.py tests/test_r02_runtime_closure.py
# 46 passed。此时新增文件把一个参考副本测试加强为用户/工具两种实际服务调用测试，共 7 项。
```

两组有重叠，不将计数相加。首次测试有 2 个测试辅助调用漏传 principal 的错误，补齐测试参数后通过，没有为此放宽产品权限。现有依赖弃用 warning 保留。`git diff --check` 通过。

### 真实 Qwen + Hermes

| 场景 | 实际结果 |
| --- | --- |
| 草案提交后注入模型连接失败 → 应用成功兜底 → 追问 | 两轮 completed；Qwen 依据已恢复的权威回执展示草案；一个草案，零任务 |
| 草案提交后确认预览失败 → 恢复 → 追问 | 两轮 completed；追问调用 get_investigation_draft；一个草案，零任务 |
| 注入旧版连续助手消息 → 关闭并重建会话服务/Agent → 追问 | completed；调用 get_investigation_draft；原历史数据库行（含 JSON/hash）逐字节不变 |

证据目录：

- `/Users/ext.wanghongtao6/Documents/Codex/acceptance/draft-history-recovery-qwen-20261009-fixed/`
- `/Users/ext.wanghongtao6/Documents/Codex/acceptance/draft-history-recovery-qwen-20261009-legacy/`

固定版本两场景共 12 次模型调用尝试，旧历史场景 6 次；其中各 2 次为请求发出前注入失败，实际共 14 次提供方调用。模型与工具参数未改写；准备阶段仍用合成资源。新历史两场景追问前后的草案、修订、运行、采用记录和创建操作表完整对比一致。

### 覆盖边界

- 旧记录恢复验证包含重建服务与 Agent，不等同于真实服务器重启或浏览器 SSE 断网验收。
- 本批验证指定的纯文本相邻助手历史，不宣称自动修复任意损坏历史；丢失用户消息、错配工具结果等仍会拒绝。
- Hermes helper 是固定依赖的内部入口，升级依赖需重跑真实整理行为测试。没有复制另一套合并算法。
- 模型故障使用可重复的连接错误注入，没有制造真实供应商 censor；最多五次模型请求的现有确定性回归通过。
- 三条实际多轮轨迹证明本次目标链路，不能推导所有自然语言交互的长期成功率。没有提交、推送或部署。
