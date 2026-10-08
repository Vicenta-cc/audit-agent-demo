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
