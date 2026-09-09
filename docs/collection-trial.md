# 同一前台下的演示与正式试采集

本实现从 `cd7ce5e` 创建独立分支 `codex/ethnic-collection-trial`，原基线目录不变。旧 `dc53807` 启动说明仅作为固定环境与身份检查的参考。

前台：<http://127.0.0.1:3148/investigation>。网关 8148 按持久化会话、草案、执行和报告 ID 路由到演示 API 8147 或正式 API 8149。普通“新建调查”进入演示环境；本次正式窗口已单独登记。后台连接失败时不会把请求转发给另一套库。

正式窗口：<http://127.0.0.1:3148/investigation/investigation-session%3Aa34ce7a712db493fbb73c1c136e695d9>

## 本次范围

- 抖音公开帖子及一级评论，17 个 `ethnic_discussion_recall_v1` 启用词。
- 每关键词最多 20 个帖子，每帖最多 1,000 条一级评论，不抓次级评论。
- 最多 340 个帖子进入内容研判；同一内容 ID 去重，实际数量可能不足上限。重复关键词命中仍可能产生重复采集请求，但不会重复进入本任务研判。
- 串行采集，两个后台另用跨进程锁避免同时占用同一个采集器。审核可随已采集内容逐步推进。
- 使用冻结的 `ruleset-revision:ruleset.ethnic-content-review.v1:v1`，12 条规则。正常身份、婚恋、文化或政治讨论不能仅因主题被判风险；只做内容级分析，不推断个人民族或建立个人画像。
- 这是一次有限试采集，并非已经实现按时间无限分轮采集。原计划约一天用于观察，不保证恰好运行 24 小时，也不为展示效果预设风险数量。

演示后台保留最多抓取、研判 1 条的限制。正式资源不导入演示库，因此仍可在演示窗口演示生成词库和规则。

## 本机启动与检查

配置文件为本目录根下 `.env.collection-trial.json`，已排除 Git。它只保存路径和端口，不保存密钥。后端加载主项目 `.env`，再强制绑定各自数据、输出和账号解密文件；前端不加载后端密钥。

```bash
cd /Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-ethnic-collection-trial
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-product-baseline-r0-2-1/.venv-r0-2-1/bin/python scripts/collection_trial.py check
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-product-baseline-r0-2-1/.venv-r0-2-1/bin/python scripts/collection_trial.py start
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-product-baseline-r0-2-1/.venv-r0-2-1/bin/python scripts/collection_trial.py workers
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-product-baseline-r0-2-1/.venv-r0-2-1/bin/python scripts/collection_trial.py status
```

`start` 启动两套 API、网关及前台；`workers` 在身份检查通过后启动各一个调查 worker。启动器不抢占未知端口、不结束其他环境进程。局部服务未通过检查且占有端口时，先检查日志，使用本启动器 `stop` 卸载本次六个服务，再启动。

六个服务由 macOS launchd 独立守护，标签为 `local.xhs.collection-trial.{demo-api,production-api,gateway,frontend,demo-worker,production-worker}`。关闭本启动终端不会停止它们。服务异常退出后 launchd 会重新拉起；重启电脑后登录当前用户也会加载已保存的服务。

**进程守护不等于采集中断后无损续跑。** 原生 worker 用数据库租约和心跳恢复任务；确认尚未启动的采集可继续启动，已经启动但结果不明的采集会标记中断，避免盲目重抓。不要直接改租约、重放确认或复制一个新的任务假装恢复。

试采集启动时另加约 25 小时的临时防空闲睡眠断言，不改系统全局电源设置；合盖、关机、断网仍会影响本地运行。完成时可按 `wake-assertion.json` 中的 PID 结束该临时进程。

## 数据与长期查看

正式主库和会话库：`/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/data/`。报告与证据：同项目 `outputs/`。`data/outputs` 是指向 `../outputs` 的符号链接，保持既有输出文件的位置。

演示数据：`/Users/ext.wanghongtao6/.codex/artifacts/m3-pass-m1-m2-20260909/private-runtime/`。旧 3128 环境不属于本启动器，不会被它关闭。

路由登记、任务回执、冻结配置、运行日志：主项目 `artifacts/task-plans/ethnic-discussion/runtime/`。其中 `registry.json` 必须随数据保留。初始对话的 SQLite 一致性备份在 `initial-conversation-backup/`；它不是采集完成后的全量备份。

本次新增服务没有按时间清理任务。任务完成、关闭网页不会删除对话；只要数据、输出、代码、运行配置和路由仍在，重新启动后可继续查看。不要仅备份 `audit_index.sqlite3`：对话还依赖 `investigation.sqlite3` 与 `investigation_creation.sqlite3`。迁移时保存整个数据目录、符号链接指向的 outputs 实体目录、runtime 目录和本机配置；不要对运行中的 SQLite 文件直接复制，使用 SQLite backup API 或先停止服务。

## 验证与持续检查

M3 确认、冻结配置、worker 恢复、串行锁、网关路由与流式响应相关测试共 103 项通过，另有 23 个子测试通过；前端 TypeScript 检查通过。浏览器已验证两套任务和 A/B 报告同屏，以及正式任务确认卡中的数量。

创建了当前 Codex 对话的每 15 分钟跟进：任务正常推进时不反复通知；完成、新失败、需重新登录或超过 30 分钟无输出变化时检查并通知。跟进依赖电脑和 Codex 应用继续运行，参见[官方定时任务说明](https://learn.chatgpt.com/docs/automations?surface=app)。采集服务自身由 launchd 守护。

外部抖音会话、模型与远程语音服务的持续可用性仍须由实际执行验证。启动检查通过不代表整批内容已经完成审核或报告已经生成。
