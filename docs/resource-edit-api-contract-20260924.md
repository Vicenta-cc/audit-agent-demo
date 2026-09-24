# 词库编辑 HTTP 输入契约修复

## 原因与范围

`POST /api/investigation-workspaces/{session_id}/resource-edits/lexicon` 是结构化编辑稿入口。它错误复用了 Agent 工具的 `CreateLexiconInput`，允许只传 `generation_request`，随后无条件读取 `body.content.model_dump()` 导致 500。

该 HTTP 入口改用独立 `CreateLexiconEditBody`：`content: LexiconContent` 必填，额外字段拒绝。原 `create_lexicon_edit` 工具仍接受 content / generation_request 二选一，模型生成、Provider、Prompt、权限和保存语义均未变。

没有新增 HTTP 模型生成路径，没有自动正式保存或启动任务。已检查前端仓库，该 URL 的 smoke 调用传入的是结构化 content，没有发现依赖 HTTP generation_request 的调用。

## 验收

最终结果：337 passed、35 subtests passed（包括本次新增 12 个 HTTP 契约用例）；`git diff --check` 通过，仅有既有依赖弃用提示。

新增 `tests/test_resource_edit_api_contract.py`。先运行测试确认原代码仍返回 500，再修复并回归：

| 请求 | 修复后 |
| --- | --- |
| 仅合法 content | 200，会话编辑稿，可继续保存和回读 |
| 仅 generation_request | 422，不调用模型，不创建编辑稿 |
| 二者都传 / 都不传 | 422 |
| content 为 null / 非法内容 / 多余字段 | 422 |
| 其他用户的会话 | 拒绝，无写入 |
| 未提供有效身份 | 401 |

另验证 OpenAPI 只声明必填 content，正常 HTTP 导入→保存→读取一致，失败不创建 Draft/Run 或保存回执。HTTP 测试明确禁止调用 ResourceGenerator；Agent 专用生成契约和生成→保存回归继续运行。

测试使用独立临时数据库和真实 FastAPI 路由的 TestClient，不调用收费模型、不启动采集。当前 3498/8498 的固定验收快照没有更新，不能据此声称该运行服务已加载修复。改动未提交或部署。
