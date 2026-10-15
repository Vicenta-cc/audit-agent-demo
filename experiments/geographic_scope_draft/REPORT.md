# P1 省市地域草案实施报告

日期：2026-10-15。范围以用户最终纠正为准：最细到城市，接受省级、地级市和直辖市。P1 只保存、修改、清除和展示调查地域；地域执行链路尚未接通，不进入 P2，不宣称帖子筛选或采集已生效。

## 用户最新决定与实际流程

用户最终明确“最细支持到城市，不是只能选择城市”。此决定覆盖此前排除普通省级目标的中间解释；旧 city 命名日志和审计材料保留为历史记录，不代表最终产品范围。

1. 用户要求调查某地域，现有对话模型理解意图并调用现有 `configure_investigation_draft`，传原始地名、真实用户原话和选择策略。
2. 后端在省级及地级市集合中匹配规范名或已核实别名，补齐规范名称和所属省；直辖市保留真实省级类型。不增加地域提取模型调用或在线查表。
3. 唯一匹配直接写入草案并展示，无需用户重复确认。新疆 → 新疆维吾尔自治区；朝阳 → 朝阳市；克拉玛依 → 克拉玛依市。“吉林”有吉林省、吉林市两个支持候选，需要澄清；全名各自接受。
4. 区县、县级市、街道、自治州/盟/地区、未知名或多目标保存为 pending。提示用户核对或提供受支持的省市目标；不自动扩大为父市，不猜错别字。未找到也可能是资料缺失，不能断言用户写错。
5. P1 到草案为止。后续 P3 使用确定的调查目标判断帖子地域；本轮没有修改或连接 P3。P3 已有读取实验地点行政链的能力，统一名录/接口复用留待后续集成。

允许无地域的旧任务照原流程运行。用户明确取消地域仍支持 clear；没有新增“只能开启地域调查”的产品限制。

## 基线与保存状态

- 工作树：`/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo`。
- 分支：`codex/geographic-relevance-p1`；HEAD：`a4997d3969beab3d499a218e7a7aee025fea46d9`，开始时干净。
- 从已提交业务基线增量实现。P0 的 `bridge-minimal-v1 / confirmation` 只提供交互原则；未搬实验框架、未改 P0 冻结结果，也未复制 P3 未提交实现。
- 改动仍为未提交文件；未 commit、push 或部署，未重置其他工作树。

## 文件与实现

| 文件/范围 | 结果及理由 |
| --- | --- |
| `backend/investigation_creation/scope_intent.py` | 省市范围精确解析，pending/resolved/cleared，来源核验、复制血缘、执行阻断。只在支持集合中消歧；不支持的县区别名不干扰城市简称，省市同名必须澄清。 |
| `backend/investigation_creation/geographic_catalog.py`、`data/geographic_catalog.json` | 后端本地名录、标准库校验和内建离线转换；数据缺失或校验失败明确拒绝，无少量城市回退。 |
| `scripts/build_geographic_catalog.py`、`scripts/import_cn_division_catalog.py` | 固定上游版本、保留输入字节/许可证、可复现转换和独立身份基准核验；输出路径不能覆盖输入或审计证据。 |
| `.gitignore` | 仅放行这一生产名录，其他业务 data 仍忽略，避免提交或换号备份漏数据。 |
| `contracts.py`、`configure.py`、`conversation.py` | 扩展现有 configure 的可选 change；只改 creation 局部指导。传原始地名，使用实际回执澄清；不改模型/provider/全局循环。 |
| `approval.py`、`service.py`、`store.py` | 地域与资源、revision、采用授权同次验证及持久化；确认事务重验，拒绝地域任务后无 Run/Job/配额预留。 |
| `public_projection.py`、`tools.py`、`resources.py`、`backend/api/investigation_creation.py` | 安全投影、工具回执、预览和恢复。客户端 PATCH 可保留原状态，不能伪造/修改可信地域与来源。 |
| `Audit_assistant/src/types/investigationCreation.ts`、`confirmationView.ts`、`TaskConfirmationCard.tsx`、`TaskSuggestionCard.tsx`、`InvestigationContextDrawer.tsx`、`InvestigationPage.tsx` | 原卡片/抽屉只读展示省市、策略和 pending；地域 blocker 可进入确认预览，仍不能启动；其他资源/账号限制保留。历史卡不借用当前 preview，没有新入口或开关。 |
| 五个新增后端地域/名录测试文件、`Audit_assistant/tests/geographicScopeDisplay.spec.ts` | 全国城市与别名、来源、事务、API、恢复、直接确认、名录重建与原组件显示回归。 |
| `experiments/geographic_scope_draft/` | 原始资料、转换输入/输出、核验依据、隔离服务返回值、测试日志和截图。 |

完整变更/新增文件字节数与 SHA256 见 `FILE_SHA256.json`（不包括它自身）。

## 名录来源、覆盖及局限

采用用户指定的 `kk-418/cn-division`，固定 commit `88f2021ea21769bdb95d93699d8a625fcd9165ef`、package `2026.0.1`。保留 MIT 许可证及原始文件，不安装或执行上游代码。2026-09-02 是 Git 提交日期，不是官方行政区划生效日期；本地获取/核验日为 2026-10-15。

实际生产文件已生成，共 3,218 条：34 个省级、333 个真实地级、2,851 个县级辅助记录。最终支持目标为 **34 个省级（含直辖市和港澳台省级记录）+ 293 个地级市 = 327 个身份**；30 自治州、7 地区、3 盟及县级不作调查目标。5 个虚拟层级与 84 个街镇记录保留排除审计，不提升为城市。省级目标的 target/province 同指自身，直辖市不造虚拟地级节点。源数据及旧审计快照中的“297 个城市”描述是生成时的中间产品范围，名单与行政身份数据本身未改变；最终支持策略由当前解析器及测试决定。

3,004 个显式别名在构建时记录，运行时不临时去后缀、不做模糊匹配。120 个跨层级重名仍完整保存在数据中；城市解析按产品范围过滤，不依赖删除真实下级数据。

34 省级 + 333 地级共 **367 条身份**与独立基准按 code/name/level/kind/province_code 全部相符，无缺失、额外或差异。基准来自 2016 官方完整表，加独立官方证实的莱芜撤销、那曲撤地设市，并以 2024 年末官方数量印证。它不是新发布的 2026 官方全国全集；有限检索不能证明截至今天绝无新变更。县级辅助记录未全部逐条官方核验，不作县级覆盖承诺。

详见 `catalog_sources/cn_division/README.md` 及 `catalog_sources/official_verification/EVIDENCE.md`。官方网页/搜索抽取明确标为工具抽取，不冒充原始 HTTP HTML/PDF；早期无法取得官方全集的记录仍保留作历史，已不代表当前生产目录为空。

复现命令（离线）：

```sh
python3 scripts/import_cn_division_catalog.py --source-dir experiments/geographic_scope_draft/catalog_sources/cn_division --output backend/investigation_creation/data/geographic_catalog.json
python3 experiments/geographic_scope_draft/catalog_sources/official_verification/reproduce_verification.py
```

`upstream/` 保留原字节；`snapshot.json` 是含 Base64 和 SHA256 的派生审计封装；`normalized.json`、`manifest.json`、`generation_report.json`、`excluded.json` 形成可复核链。独立省/地级身份基准固定到 snapshot，构建时逐条强制比对。生产名录 SHA256：`716c35d358e030b8d499979884fce462d4a2d6293b76fb0bfaad8f59062f01a6`。

## 真实工具、存储与前端证据

`evidence/service-flow-national.json` 使用正式生产名录、真实 configure/service、隔离临时 SQLite、HTTP GET/workspace 与 SSE。仅对话模型由脚本替身提供；没有正式数据库或真实模型调用。另有 `service-flow.json` 使用明确标记的 10 条 fixture，证明业务契约，不用于全国覆盖证明。

| 操作 | 参数与状态 |
| --- | --- |
| set | 原文“克拉玛依” → 克拉玛依市/新疆，revision 0→1；scope、状态及真实用户来源一起保存。 |
| pending | 当前原文“改查朝阳区” → pending，revision 1→2；原已解析 scope 仅留内部旧值，卡片只显示新目标及原因。 |
| clear | 当前原文“不限制地域” → cleared，revision 2→3；移除有效 scope，保留来源，解除地域 blocker。 |
| 改标题/词库/规则 | 省略 change 保留地域；四种正式/临时资源组合均覆盖，失败整次回滚。 |
| 复制/重启/刷新 | 来源血缘保留，更新新 draft 身份；GET、workspace、SSE 与临时存储一致。 |

真实用户来源验证覆盖同任务早期请求、当前原话优先、同名引用歧义、跨用户/会话、未来或 assistant/tool 文本、runtime/application turn 混用及旧确认轮次。来源存在只证明引用真实，不能证明 AI 已正确理解语义。新增状态的 principal/session/turn/message/quote/code 等私有字段不通过公共 API/工具/SSE 暴露。

最终截图：`evidence/scope-service-national-resolved.png`、`evidence/scope-service-national-pending.png`，均已目视核对。全国真实 service 返回值通过现有 workspace 恢复后渲染原卡片及抽屉；横幅明确程序验证、脚本模型和城市范围。pending 不显示旧城市，地域执行未接通时启动按钮禁用。

## 验证结果

| 检查 | 最终结果与原始证据 |
| --- | --- |
| 后端 13 个相关测试文件 | **385 passed、4 failed、23 subtests passed**；`evidence/backend-city-final.log`、`backend-city-final-command.json`。四项失败 ID 与干净基线完全相同，无新增失败。 |
| 新增地域/名录测试 | **137 项通过**：城市/来源/草案/全国 78，名录/导入 59；均包含在上述完整回归。城市三文件独立通过日志为 `backend-city-policy.log`。 |
| 全国支持目标 | 297 个城市全名及全部城市别名逐条解析；普通省级不作目标。第三方固定数据与独立官方基准 367 个省/地级身份逐条相符。 |
| 前端 | **65 passed**，typecheck/build 通过；`evidence/frontend-final-typecheck.log`、`frontend-final-build.log`、`frontend-final-playwright.log`。 |
| 静态与审查 | 36 个 Python 文件 AST 检查、git diff --check 通过；独立审查无未解决发现，并核对 3,218 条标准名支持状态差异为 0，见 `review-summary-city.json`。 |

汇总：`evidence/CITY_POLICY_VALIDATION.json`。四个失败均在 `tests/test_creation_prompt_isolation.py`：一个原 deferred describe 返回结构 KeyError，以及三个已有旧提示断言；完整 node ID 和干净基线对照均已保留。

锁定 Hermes 0.20.4 源码及已有解释器用于隔离测试，九个源码哈希与项目锁定清单一致；未修改旧失败断言、添加 skip/xfail、安装依赖或硬编码外部运行路径。dotenv 禁用，data/output/auth/锁/调度/browser/Hermes home 全部隔离。前端没有 lint 脚本，未虚构 lint 通过；已有 build 大包提示和弃用警告留在日志。

16 个 P1 语义样本已按城市规则更新供将来真实模型评测，未运行，不计为模型通过指标。P0 原始实验材料保持冻结。

## 剩余工作与交接

- P1 本地程序链路待原会话验收；本轮没有真实模型评测，不能宣称自然语言识别稳定。
- 数据固定于已核验快照，官方交叉证据的历史时点和县级辅助核验局限如上；未来更新需重新获取、转换及逐条对账。
- 四项基线 prompt isolation 失败保留，原因、干净基线对照与依赖环境见 `evidence/ENVIRONMENT_RETEST.md`。
- 尚未实现真实 execution_queries、执行冻结/hash、采集、帖子地域判断接线、跨词配额/补充策略或报告追溯。P2/P3 另行实施验收。
- `geo-scope-v1` 及真实行政层级继续保留；城市产品范围不能把直辖市的真实 province 类型改成伪地级市。pending 不是有效新范围。

模型请求 0，采集请求 0，正式数据库写入 0，commit/push/部署 0。换号恢复包同步保存在 `/Users/ext.wanghongtao6/Documents/Codex/handoffs/geographic-relevance-p1-20261015/RESUME.md`，包含基线 bundle、全部未提交差异、生产名录、原始资料、测试/截图和最新上下文，不依赖旧聊天或 worktree 附件。
