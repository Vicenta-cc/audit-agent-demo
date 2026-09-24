# 创建 Agent 提示分层候选：保留行为，而非重写业务流程

状态：隔离候选，未部署。冻结点 `freeze-local-20260923-before-next-pull` 保留。
候选基线是 `1c909ad1330034fadf684b3eabfbbff54ab5485a`：包含冻结点之后已完成的统一 5–10 词和评论交付修复。
不修改原工作树、3398/8398、原数据库或密钥配置。

## 原提示如何保留

`tests/fixtures/creation_prompt_baseline_1c909ad.json` 记录原始三份 Prompt，
并逐条记录必须改变的旧语句及理由。回归测试检查未列入改写清单的每一行仍存在于以下位置之一。
这只能证明文字没有悄悄丢失，不能代替真实 Agent 行为与生成质量验收。

| 原约束 | 候选位置 | 保留方式 |
| --- | --- | --- |
| 用户意图、查询与创建区别、用户指定平台/词表/执行参数 | CREATION_SYSTEM_PROMPT | 主 Agent 继续持有 |
| 资源匹配、缺资源先征求生成授权、已选资源不擅自替换 | CREATION_SYSTEM_PROMPT | 主 Agent 继续持有 |
| 成功检查点、不能重复保存、版本冲突、规则展示后另轮采用、确认后启动 | 主提示 + 原有服务/回执 | 保留，不改审批实现 |
| 完整召回质量与 5–10 实际词、原文优先、不凑数 | RECALL_GENERATION_PROMPT | 专用词库调用完整携带 |
| 规则证据模态、融合阶段、豁免、民族关系语义自检 | authoring_guidance.py | 规则生成调用完整携带；规则编辑工具仍完整携带 |
| 字段修改、重名、删除范围、来源/版本保护 | RESOURCE_EDIT_GUIDANCE | 读取/更新编辑工具完整携带 |
| 报告问答、账号穿透、媒体审核 | 原实现 | 本候选不改它们的 Prompt 或 Provider |

没有为了缩短字数重写全部主提示。主 Agent 仍携带完整的业务行为边界，专用生成步骤不携带完整会话。

## 明确处理的矛盾

1. **临时词只列文字 vs 必须建立资源**：用户已选择“生成会话编辑稿，可修改、保存或应用”。
   临时/完整词库共用 `create_lexicon_edit`，正式保存仍是独立授权的动作；纯讨论/示例不写入。
2. **永远不能保存 vs 用户可明确保存**：生成本身不保存；明确保存调用原 `save_resource`。
3. **生成后必须结束 vs 同时授权保存**：可以完成已授权保存再结束；规则采用仍等待展示后的另轮明确授权，任务启动仍需明确确认。
4. **草案直接填扁平词 vs 会话编辑稿**：生成先得到编辑稿，再使用工具返回的完整 `recall_plan`。
   已有扁平历史数据继续兼容，不自动猜测主题归属。
5. **确认前禁止所有 Provider 调用 vs 必须调用模型生成资源**：仅明确允许获授权的资源编写调用；
   不因此允许爬虫、媒体审核或报告执行。
6. **整轮文本猜测分流**：协调 Agent 固定常规 Provider；只有真正进入生成能力时才使用资源生成 Provider。
   “不保存”“不要重新生成”等否定句不再影响供应商路由。

已有“正式规则用于任务、另生成一套比较规则”的比较场景不在本轮重新定义；
不删除其原有说明，也不借提示整理扩大临时规则采用授权。

## 单一工具、两种输入的准确含义

继续使用原工具名称及原输出/回执格式：

- 新生成：传 `generation_request`（objective、platform、requirements，必要时 requested_count / exact_terms）。
- 原样导入已有结构化内容：传原来的 `content`，不调用生成模型。
- 两种输入互斥，不是“临时词/正式库”两条产品路径。保留 content 是为了 Drawer/旧调用和“保存已有 Draft 原文”，不得在模型拒绝后改用 content 编造结果。
- 修改、正式保存、采用、启动仍使用原工具；不把它们合并进生成调用。

专用生成没有工具权限，非流式、单次请求、禁用 SDK 自动重试；仅返回 JSON。
先过原 Schema，再过生成数量/原文保护，然后进入原编译与会话持久化。
格式、截断、拒绝、输入检查失败都不落入“已生成”状态，不自动裁剪或吞掉错误。
已有资源导入不受“默认最多 10 词”约束；数量规则仅用于新生成。

## Provider 与环境

主协调 Agent、保存后的自然语言回答、草案等使用原 `DASHSCOPE_*` / `QWEN_TEXT_MODEL`。
内部生成使用原 `RESOURCE_GENERATION_API_KEY / BASE_URL / MODEL`。缺配置明确报错，不静默回退或更换供应商。
未增加密钥来源，不把 key 写入 Git。

创建模式的 Hermes 0.20.4 实例使用产品 Prompt 构造器，不再夹带通用本机编码助手身份。
仅绑定该实例，不改模块全局或报告模式；升级 Hermes 必须重跑原生发现/转录回归。
专用生成日志记录模型、请求内容哈希、结果分类、HTTP 状态、request ID、finish_reason 和耗时，
不记录 key 或原始敏感正文。

## 验收与限制

至少覆盖：问候/只读、已有资源复用、临时生成、修改与保存、同轮生成并保存、正式资源与临时规则分别采用、
精确用户词表、多候选澄清、冲突、回执重放、失败保留、不得擅自启动，以及报告模式隔离。

运行：
```bash
python -m pytest -q tests/test_resource_generation.py tests/test_creation_prompt_isolation.py \
  tests/test_investigation_creation_conversation.py tests/test_resource_lifecycle.py \
  tests/test_r021_session_scoped_runtime.py tests/test_investigation_creation_m3.py \
  tests/test_resource_library_editor.py tests/test_ruleset_proposal_approval.py \
  tests/test_m3_discovery_guidance.py tests/test_ruleset_proposal_presentation.py \
  tests/test_ruleset_proposals.py
```

必须使用已安装 Hermes 0.20.4 的已验证解释器/运行环境，不能临时换 Python 绕过测试。

这不是内容审核绕过方案：供应商仍可能拦截生成或后续回答。
本候选的专用生成不续写；原主 Agent 流式恢复的错误分类不是本轮已解决的问题。
一次真实成功不代表生成质量稳定或所有会话不会失败。
业务正确性、真实链路、用户授权与报告可用性未验收之前，不替换运行环境。

## 本轮实测结果（2026-09-23）

- 上述 11 个文件：308 passed，35 subtests passed。
- 两组真实 Provider / 原生工具发现 / 隔离数据库试验：
  - 招聘主题：7 个词的会话编辑稿、正式保存、3 条规则、规则正式保存均完成；
    每类仅生成一次，保存阶段未重复生成。
  - 色情服务引流审核主题：7 个词的会话编辑稿及后续正式保存完成，本轮未出现供应商拦截。
  - 两组均未启动爬虫，Run 数均为 0；未写生产资源库。
- 请求证据：主 Agent 为 DashScope / qwen3.7-plus，专用生成为 DMX / qwen3.7-plus；
  词库专用 system 约 2747 字符，主 Agent 仍约 20423 字符（保留业务行为约束）。
- **不通过项**：招聘主题后续采用时，模型把保存回执的 content_hash 填入
  expected_runtime_content_hash，后端返回 RESOURCE_STALE；随后错误尝试普通建草案工具，
  被 PROPOSAL_APPROVAL_REQUIRED 拒绝。最终未生成 Draft。不能把“生成/保存成功”当作全流程通过。
- **质量待验收**：格式有效、数量正确不代表召回好；敏感主题这轮含若干直白/宽泛候选，
  不能据此宣称优于原版本。未做真实平台召回效果测试。
- **展示待验收**：模型保存答复仍出现内部资源 ID，违反原有展示要求；此类问题没有被文案迁移测试掩盖。

原工作树保持干净；本候选未提交、推送、重启或部署。后续应先收敛采用及展示问题，
不放宽真实哈希校验、presentation 授权、版本冲突或任务启动约束。
