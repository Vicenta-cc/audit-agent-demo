# 地域选择历史实现与验证

日期：2026-10-15。工作树：`codex/geographic-relevance-p1`。本轮未提交、未推送。

后续第二轮真实复测发现并修复“手动输入歧义地域后点击候选”的历史遗漏；已补快照持久化及真实 DeepSeek 验证。最新结果见 [第二轮复测报告](selection-history-retest/README.md)。以下为第一轮实现与验证记录。

## 最终行为

- 成功选择地域后，在对话时间线追加真实的系统操作记录，保留修改前的问题、原因、候选按钮、已选项、修改结果与时间。
- 历史按钮全部只读；“其他地域”若被选择，明确显示填写并验证成功的地域，不冒充原候选。
- 后续修改继续追加，不覆盖前一次记录；刷新后恢复。最新草案卡保留“修改调查地域”入口。
- 旧选择操作也可恢复：后端读取既有操作记录关联的修改前后草案版本，无需迁移或补写假消息。

## 实现位置

后端：`backend/investigation_creation/scope_selection.py` 从不可变版本投影公开历史；`conversation.py`、`backend/api/contracts.py`、`backend/api/investigation_conversation.py` 将历史加入工作区响应。按会话与所有者隔离，不公开内部来源字段。

前端：新增 `RegionSelectionHistoryCard.tsx` 和 `regionSelectionHistory.ts`，经 `workspaceRecovery.ts` 恢复并按会话消息序号插入时间线，由 `InvestigationCenterArea.tsx` 渲染。同步更新相关类型与样式。历史作为系统操作展示，不加入 AI 消息。

边界：历史记录成功提交的地域选择及其修改前草案问题与候选，不保存未提交输入或验证失败尝试。本轮没有更改模型提示词、回复展示或资源生成工具。

## 验证

- 后端原有关联回归 135 例通过；新增历史测试 9 例通过，共 144 例。覆盖连续修改、历史快照不变、刷新恢复、幂等、权限、公开字段及资源隔离。
- 前端地域、创建、混合提案与提案展示回归 98 例通过；构建及类型检查通过。最终只读“其他地域”文案调整后，聚焦 2 例重跑通过。
- 增量后端、前端独立代码审查均无确认缺陷；`git diff --check` 通过。
- 前端构建保留现有大包体提示；测试服务器出现未模拟额度接口的代理连接提示，测试全部通过。

## 真实前端验证

页面：`http://127.0.0.1:3993/investigation/investigation-session%3Adf20bb8a6a7346a9b4429b5c32d28cfe`。

1. 刷新后，原先“克拉玛依市 → 吉林省”的选择自动恢复到原消息与“继续”之间。
2. 在最新卡片将吉林省改为吉林市，再改回吉林省；新增两条记录，原记录保持不变。
3. 刷新后仍有三条历史；旧按钮全部禁用，当前草案地域为吉林省、版本 4。
4. 19 张规则、词库、资源及执行表的行数与内容哈希前后完全一致；草案非地域配置不变。
5. 本轮未新增 AI 对话轮次或模型调用；未启动采集、Run 或 Job。之前真实 DeepSeek 的地域同步验证仍见 `direct-selection-live/README.md`。

证据：`selection-history-evidence/result.json`、`resources-before.json`、`resources-after.json`、`region-history.jpg`。

前端 3993、后端 8993 保持运行。测试专用香港出口与全局网络设置均未改动。
