# P0/P1 最终集成版本

日期：2026-10-15。分支：`codex/geographic-relevance-p1`。最终冻结标记：`checkpoint/geographic-relevance-p0p1-final-20261015`。

## 版本来源与合并

- 共同基线：`a4997d3969beab3d499a218e7a7aee025fea46d9`。
- 地域功能检查点：`94e05dd`，保存本轮合并前的全部 P1 代码、测试、名录、验证材料，并收录 P0 实验与测试。
- 已验收资源版本：`checkpoint/resource-tools-accepted-20261015`，提交 `31c2280f2137bcff18e89bb539026e79c2330402`。
- 合并提交：`ea5aa58`，两个父提交分别为地域检查点和已验收资源版本。随后单独提交测试归档、过时断言维护及本报告。
- 原有资源 checkpoint tags 均保留；本轮只做本地提交，未推送远端。

共处理 14 个冲突文件：6 个代码/测试文件和 8 张历史截图。资源快照包含较早的 P1 地域代码，地域卡片、识别逻辑、测试和截图保留最新 P1；资源回执的 `read_arguments` 保留已验收增量。

合并后的资源增量仍为原已验收的 24 个文件、1078 行新增和 35 行删除。12 份资源相关生产文件与已验收提交逐字一致；共用的 `store.py` 同时保留地域历史事务和资源生成重试支持。前端、地域解析/配置/上下文及相关 API 与地域检查点逐字一致。独立只读审查未发现二者事务或索引互扰。

## 本版本包含的行为

- 支持省级地域、地级市及直辖市，最细到城市。区县、街道不自动扩大范围；同名或无法核实的输入待澄清。
- 调查对象或事件名含地域时，可承接到草案，例如“调查克拉玛依大火”；不要求额外说“某地的舆情”。保留否定、引用、资源名称等来源边界。
- 后端本地名录规范地域并校验归属，包含来源、补充核验和摘要记录；不把省级 IP 推断为城市。
- 前端直接选择或修改地域，经后端校验、版本检查和事务保存；下一轮 AI 获取当前草案地域。选择记录保留问题、候选、已选项与时间；历史按钮只读。保留最新编辑 UI。
- 规则/词库生成使用已验收的精简生成契约、独立完整导入入口、程序编号及一次有界失败纠正；保留精确版本引用、局部编辑、保存/采用/启动边界。

**P1 仍保留地域执行阻断。** 本版本完成地域识别、草案、交互和资源工具集成，不代表 P2/P3 的地域采集/帖子判定已接入。即使采集服务可用，也不能绕过 `RELEVANCE_SCOPE_EXECUTION_UNAVAILABLE` 启动地域任务。

## P0 保存与复现

P0 冻结实验、提示、样本、schema、评分及历史证据保持原字节。八份旧测试原样归档到 `experiments/geographic_scope_intent/baseline_tests/`，按 `a4997d3` 基线独立运行；复现方式见该目录上级的 `BASELINE_TESTING.md`。

原因是 P0 实验通过 AST 读取所在仓库的生产配置契约；P1 新增 `scope_decision` 后，旧实验不能直接作为 P1 生产回归。没有通过更新旧样本、改分或刷新冻结快照消除差异。原工作区与重新从 Git 解出的独立基线沙箱均为 435 项通过；这不改变 P0 历史模型评测中的失败与局限，也不把 P0 宣称为整体通过。

P0 原始 `outputs/` 结果仍保存于既有独立交接包：`/Users/ext.wanghongtao6/Documents/Codex/handoffs/geographic-relevance-p0-20261015/`。Git 版本保留实验源码和已收录证据，不能代替这份含原始结果的交接包。

## 验证说明

最终结果：

- 合并后 51 份后端测试文件：1324 项通过，另 42 项子测试通过。
- P0 原基线：435 项通过；从 Git 解出的独立沙箱再次 435 项通过。
- 前端地域、草案、混合资源与提案展示：99 项通过。
- TypeScript 类型检查和 Vite 构建通过；保留已有的大包体提示和 Python 依赖弃用提示。
- 修改 Python 文件语法检查、代码 diff 空白检查通过。项目未配置全仓 Python lint/typecheck，不宣称已通过这类检查；历史原始日志的空白按原字节保存。
- 最终后端重启后，前端页面与 API 均返回 HTTP 200；3 张草案/版本/地域历史表和 19 张资源/执行表的行数及摘要前后完全一致。没有创建采集任务。

最新机器结果和命令保存在 `experiments/geographic_scope_draft/final-merge-evidence/`。本轮未追加真实模型调用；真实资源验收仍以 `docs/resource-generation-fix-results-20261015.md` 中的原批次为准，不能把本轮离线回归当成新的模型稳定性证明。

三份已有测试的过时工具目录/提示词断言已更新。六项原断言在共同基线上就失败，另一项严格工具集合新增已验收的 `import_resource`。保留冻结提示词基准，逐行核对替代约束，并新增旧入口隐藏、账号模式不暴露创建工具的负向断言；没有改生产提示词。

数据库兼容检查使用在线备份的独立副本：27 张原表数据摘要全部不变，地域历史字段和新的资源成功唯一索引初始化成功。正式运行数据库和私有配置不进入提交或 Git bundle。

## 本机查看与交接

- 工作区：`/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo`。
- 前端：`http://127.0.0.1:3993/investigation`；后端：`http://127.0.0.1:8993`。
- 本机后端启动脚本：`/Users/ext.wanghongtao6/Documents/Codex/runtime/geographic-p1-event-place-20261015/launch_backend.py`，使用 `/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python`，不带 `--seed`。
- 前端在 `Audit_assistant/` 目录设置 `VITE_API_PROXY_TARGET=http://127.0.0.1:8993` 后运行 `npm run dev -- --port 3993`。
- 运行数据：`/Users/ext.wanghongtao6/Documents/Codex/runtime/geographic-p1-event-place-20261015/data/`。启动脚本从既有私有配置读取模型凭据；不要将凭据写入代码、交接文档或提交。
- 测试专用香港出口仍为 `127.0.0.1:8996`，全局代理与 TUN 设置未改。保留其他窗口及资源验收服务。

接手时以最终 tag 和本报告为入口，先核对当前工作区，再继续后续阶段。旧实验报告中的“未提交”等表述是历史记录，本报告描述本轮最终集成状态。
