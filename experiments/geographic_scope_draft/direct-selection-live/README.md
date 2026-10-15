# 真实 DeepSeek 前端地域同步验证

日期：2026-10-15。P1 工作树，`codex/geographic-relevance-p1`。

结论：**修改地域后，用户只说“继续”，真实 DeepSeek 能读到新地域；规则和黑话库未改动。**

## 实际步骤

1. 使用已有前端 `http://127.0.0.1:3993`，启动同一 P1 工作树后端 `8993`，关闭 fake runtime。
2. 新建独立测试会话 `investigation-session:df20bb8a6a7346a9b4429b5c32d28cfe`。真实模型按用户消息生成“克拉玛依欠薪舆情调查”，复用现有“独立测试通用舆情规则”和“独立测试通用舆情词库”。明确不生成资源、不启动调查。
3. 在前端点“修改调查地域”，填写“吉林省”，点“验证并保存地域”。同一草案 `investigation-draft:f0ce9dcc74664fdea2f5ad45c9aa58b4` 从 revision 1 变为 2；所有非地域配置保持不变。
4. 聊天输入框只发送 `继续`，没有在聊天中发送“吉林省”。真实 DeepSeek 本轮 2 次模型调用，状态 completed。
5. 模型读取现有草案，回答新地域和 revision 2；没有回写草案。前端最新卡片显示吉林省。
6. 比对 UI 修改前、UI 修改后、模型回复后的草案，以及测试前后 19 张资源/执行相关表：全部检查通过。

用户最初的测试需求预先说明：“之后我说‘继续’也只核对当前草案配置，告诉我当前调查地域，不启动调查。”此约束用于避免测试触发执行，没有透露之后会选择的地域。

## 模型真实最终回答

> 当前草案已核对，未启动调查。
>
> 当前草案配置中，**调查地域显示为「吉林省」**，而不是你最初要求的「克拉玛依」。草案当前 revision 为 2，标题和目标仍是“克拉玛依欠薪舆情调查”，但地域范围已经变成了吉林省——这与本次“克拉玛依欠薪”的调查意图不符。
>
> 我没有启动调查。需要你确认：调查地域应以「克拉玛依」为准，还是确实要改成「吉林省」？如果要改回克拉玛依，我可以帮你修正草案地域。

这里证明模型成功获得新地域。模型因地域与原标题/目标不一致而再次询问，未把这次界面选择直接视为调查议题也已修改；本轮测试没有改提示词或修改此行为。

前端现有建议卡逻辑把真实最终回答替换为通用建议文案，因此界面可见吉林省，但看不到上述核对问题。此展示问题如实记录，未在本次验证中顺带修改。

## 资源及执行保护

- 三个阶段所有非地域配置逐字段相同，包含规则与黑话库引用和召回配置。
- “继续”后 revision 仍为 2，地域仍为吉林省。
- `audit_index.sqlite3` 中规则、词库、资源保存记录和 jobs 等 11 表，`investigation_creation.sqlite3` 中规则提案、词库编辑、资源编辑记录和 investigation_runs 等 8 表：行数和 SHA256 全部相同。
- 没有重生成/保存规则或黑话库，没有新建 Run/Job。
- 本轮仅启动和调整本地测试运行环境，没有改产品代码、commit 或推送。

## 网络及解释器

- 用户指定香港专用出口后，测试后端 `HTTPS_PROXY/https_proxy` 切换到 `http://127.0.0.1:8996`。
- 现有该代理仅有 `🇭🇰 香港 01丨1x HK`，规则 `MATCH,P1-ONLY`，`allow-lan=false`；TLS 校验启用。未关闭 TUN，未切全局节点，未修改其他应用。
- 独立无密钥 TLS 探测返回预期 401；正式“继续”验证使用真实 `deepseek-v4-pro-ga-260813` 完成。
- 初始草案在用户补充网络方案前，经旧测试中继生成；地域修改后的正式验证经香港专用代理。没有将两条路径混称为全部经香港。
- 首次误用主项目测试解释器，缺少 `openai`，零模型调用；随后使用已有 `/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python` 恢复，未安装依赖。
- 当前启动器：`/Users/ext.wanghongtao6/Documents/Codex/runtime/geographic-p1-event-place-20261015/launch_backend.py`；旧启动器另存 `launch_backend.before-hk-direct-selection.py`。后端、前端保持运行供查看；旧 8994 中继已停止。

## 证据

- `result.json`：全部检查、实际用户输入、模型最终回复、工具名、调用计数、界面选择记录。
- `draft-before.json`、`draft-after-ui.json`、`draft-after-continue.json`：三个阶段的草案。
- `resources-before.json`、`resources-after.json`：19 张表的行数及内容哈希，不含凭据。
- `after-continue.jpg`：真实前端回显。

前端会话：`http://127.0.0.1:3993/investigation/investigation-session%3Adf20bb8a6a7346a9b4429b5c32d28cfe`。
