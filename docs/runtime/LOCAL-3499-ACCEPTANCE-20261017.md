# 3499 本地整合环境：启动与实测记录

实测日期：2026-10-17；状态补核：2026-10-19。本记录仅适用于本机 3499/8499，不代表线上已经升级，也不能替代云端候选环境验收。

## 固定环境

| 项目 | 当前值 |
| --- | --- |
| 应用仓库 | `Vicenta-cc/audit-agent-demo` |
| 应用分支 | `codex/new-media-app-integration-20261016` |
| API / worker 运行配置固定提交 | `16fca47453468fe87c4bee3226eb472d2ad839f6` |
| 应用目录 | `/Users/ext.wanghongtao6/.codex/worktrees/new-media-app-integration/xhs-audit-agent-demo` |
| MediaCrawler 目录 | `/Users/ext.wanghongtao6/Documents/Codex/projects/MediaCrawler-camoufox-runtime` |
| MediaCrawler 提交 | `7bd9d117cd1b0753990ba754c58b8c12de8f4ca5` |
| 运行数据目录 | `/Users/ext.wanghongtao6/.codex/runtime/media-merge-login-smoke` |
| 前端 | `http://127.0.0.1:3499/investigation` |
| API | `http://127.0.0.1:8499` |
| 应用 Python | `/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-local/venv-app/bin/python` |
| Node / ffmpeg | `/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-local/bin/` |

普通对话与资源生成使用同一个已配置的 DeepSeek Pro 模型；内容预筛和审核使用既有 Qwen 配置，ASR 使用远端 Dolphin。现有香港代理和账号限流保持不变。密钥保存在仓库外，不随提交上传。

本环境沿用既有的本机免应用鉴权模式，因此实际前端测试只能证明查询范围行为，不能证明不同登录用户之间的数据隔离。线上鉴权配置不得照抄这个模式。

源码已继续累积 `9a0ded4` 和本次发布时间修复，但本次提交、推送不会重启服务或更新运行配置固定提交；不能据此认定 API / worker 已加载全部最新源码。前端开发服务器可热更新，也不能代替完整环境重启验收。

## 启动入口与升级顺序

本环境的唯一服务入口是运行目录中的 `run_integrated_acceptance.py`，分别接受 `api`、`worker`、`frontend` 参数。三个组件共用 `integrated-acceptance.json` / `runtime.json` 固定配置；启动器检查应用和爬虫 SHA、工作区状态，再构造服务环境。不要在原 Documents 应用目录启动另一套后端，也不要运行旧 `start-stack.sh`。

单组件启动形式（仅在该组件已停止、配置 pin 与干净源码一致时使用）：

```bash
/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-local/venv-app/bin/python \
  /Users/ext.wanghongtao6/.codex/runtime/media-merge-login-smoke/run_integrated_acceptance.py api
```

另外两个组件将末尾参数替换为 `worker`、`frontend`。此命令占用当前终端；本次服务由同一入口脱离终端运行，进程身份保存在 `integrated-processes.json` 和 `receipts/`。

更新时必须先检查活动调查、对话执行和采集子进程；待其结束后，备份 SQLite、验证备份完整性，按 PID、启动时间、进程组和工作目录确认目标，再定向停止本环境三个服务。更新源码 pin 后用相同入口启动，检查端口、API 和前端页面。不要复用历史收据脚本里写死的 PID，不要批量结束所有 Python、Node 或爬虫进程。

已有浏览器登录身份、账号保护、代理租约和任务数据库需要保留。旧失败任务缺少恢复证据时应继续拦截，不能手改数据库伪造完成或放行恢复。ASR 隧道及代理属于本机外部依赖，启动 API 本身不会自动建立它们。

## 本轮代码与验证

- `964e645`：地域媒体交接及下载后崩溃恢复，包含离线恢复测试。
- `16fca47`：恢复阻塞字段在公开 DTO 和报告入口中保持一致，修复真实重启后读取旧会话的 422。
- `9a0ded4`：正常采集中的记录不再误显示为待恢复异常；中断任务仍执行原有恢复保护。
- 本提交：实时帖子详情不再将审核、创建或更新时间冒充原帖发布时间；缺少原帖时间时显示 `--`，冻结报告仍使用自身的发布时间字段。新增十项前端回归，覆盖缺失时间、四种原帖时间字段和报告快照。

前端 11 项回归、TypeScript 检查和构建通过。公开 DTO 回归 34 项、地域恢复入口回归 12 项通过。媒体交接与恢复此前完成 58 项离线测试；上述实质代码均经过独立代码审查与架构复核。

2026-10-19 重新执行发布时间与证据时间前端回归，共 15 项通过；显式设置 `VITE_API_PROXY_TARGET=http://127.0.0.1:8499` 后执行 `npm run build`，TypeScript 检查与 Vite 构建通过，仍有大包体积提示。上述历史回归数量属于各次修复记录，不表示本次重新执行了全部后端测试；发布时间修复已单独经过代码审查与架构复核。

## 实际前端验收

旧的第三轮调查缺少历史捕获核对证据，恢复仍被正确阻止，原有一条审核结果保留。通过前端从其配置准备第四轮草案，克拉玛依市确认状态、两个检索词和审核规则均保留，没有要求重复确认地域。

第四轮任务：`m3-29e2167d7054ead17292`。配置为克拉玛依市地域精选、两个关键词各最多一条、总计两条、每帖最多十条评论。每关键词先抓十条候选；新登录账号实际保护速率为每分钟一条，因此不能用最终两条帖子的数量估计候选采集耗时。

2026-10-19 只读核对本地数据库：第四轮采集与审核均已完成，无任务错误；共记录二十条候选、两条内容完成采集并交接，两条审核结果均为 `review`，没有待交付内容或未完成关键词。各预筛计数存在交叉含义，不能简单相加。

对应调查 `investigation-run:08d9907d96204cc4b11438299967bfcf` 已为 `PUBLISHED`，报告版本为 `report-version:df90f0a87161487c852bbbe908aa146f`。这能证明本轮报告已生成发布；本记录尚没有该大火报告的完整前端问询、帖子详情和双报告联动验收证据，也没有加载本次全部提交后再次重启验收的证据。这些仍需后续验证。

已完成的旧作者主页报告检查：

- 已发布报告能读取，统计为一条作品、三条已审核评论。
- 报告正文、语音原文、中文翻译、审核结论与原帖链接可以逐项核对。
- 冻结报告的帖子详情展示正确的原帖发布时间。
- 账号活动概览区分当前报告与全部可查询调查；历史账号问询返回实际一条评论，没有补造其他发帖。
- 改写、翻译和 Base64 形式索要内部模型与提示词，被统一拒绝。
- 没有将同帖互动推断为团伙，也没有将本次内容无风险扩大为账号未来无风险。

本轮还点击了一次旧报告评论账号的“关联”，产生本地关联记录；没有向外部平台发消息或修改线上数据。
