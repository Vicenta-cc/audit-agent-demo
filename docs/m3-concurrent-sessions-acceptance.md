# M3 会话并发修复与验收（2026-09-09）

基线：`dc53807833cc66886bbc79b0f5cf3b2eef738c86`，冻结分支
`codex/m3-prompt-300-frozen-baseline`。修复分支：`codex/m3-concurrent-sessions`。
没有改动 Hermes 0.20.4 源码或更换模型。

## 根因与修复范围

原接入层在 `HermesRuntimeBinding.product_mode_execution` 中持有进程级
`_PRODUCT_MODE_EXECUTION_LOCK`，直到整轮 Agent 执行结束。调查创建与报告问答共用
API 进程，切换工具目录、环境变量和 discovery 指引时依赖该锁。因此生成规则会让
报告 A/B 问答等待，报告会话之间也会竞争同一把锁。此前跳过真实 runtime binding 的
并发测试不足以验证这一点。独立采集 Worker 的采集、审核及报告生成不持有该 API 锁。

正式 API executor 现在为每轮运行启动全新 Python 进程，继承部署配置，按持久化 Turn ID
加载所属服务。不同会话的 Hermes 工具目录、环境和 monkeypatch 不再相互影响。
同一会话仍只接受一个运行中的 Turn，重复 client_message_id 不会再次执行。
Hermes 配置和日志目录也按 Session 隔离。

并发不是无限制：创建与报告 executor 各自受 `HERMES_INVESTIGATION_MAX_WORKERS`
限制，默认各 2 个槽位；超出槽位的请求排队。验收配置各 6 个槽位，覆盖 6 个不同会话。
队列阶段在页面显示“已接收，等待开始…”。不按 A/B 或三个固定入口特判。

每轮超时由 `HERMES_TURN_TIMEOUT_SECONDS` 控制，默认 600 秒。正常退出、异常退出、
超时均清理子进程组；API 意外退出时，子进程监视父进程并终止自身。重启后已开始的
Turn 标记可恢复中断，不自动重放可能产生业务修改的执行。未启动的排队 Turn 可以恢复。
该实现使用 POSIX 进程组，面向当前 macOS/Linux 部署。

## 连续问答的第二个问题

首次独立进程补测发现：工具执行账本保存了结果，但账号、评论、父帖及分页引用原来
只存在内存。下一轮可能报 `unknown_ref`。不能把模型重新查询后最终回答视作稳定性通过。

修复后，每次工具返回前，将当前会话的强类型引用记录及分页状态写入账本旁的 SQLite
文件；下一轮按已授权报告、快照及内容指纹校验后恢复。仍校验会话、generation、账号
语料版本、引用种类和分页查询条件。损坏状态不会静默使用。保存操作串行提交并关闭连接。

旧基线没有引用检查点时，只回放可信已完成 transcript 内本服务的只读查询；整个工具
结果与当前授权查询结果一致（仅允许已验证的不透明引用替换）时才恢复旧别名。
不从模型自然语言推断对象身份，不重放创建或修改工具。

## 验证结果

- 后端回归：358 passed、11 skipped、43 subtests passed；5 条既有 FastAPI 生命周期
  弃用警告。跳过项依赖额外的私有报告测试输入。引用恢复最后一处修改后单独重跑 6 项全部通过。
- 真实 Hermes 工具目录 + 多进程确定性测试：6 会话、3 轮重叠执行；同会话冲突、重复请求、
  子进程崩溃/超时、两槽位排队排空、失败后恢复继续问、启动恢复不自动重放均通过。
- 真实 Hermes/Qwen 联调累计记录 55 次尝试，53 次完成，2 次是主动注入的中断；另有
  2 次浏览器提交。峰值观察到 6 个会话 worker 同时存在。
- 新调查首次生成规则约 76 秒，同时 A/B 分别约 13/19 秒返回；更新规则约 108 秒，
  同时 A/B 分别约 15/18 秒返回，证明不再等待规则生成结束。
- 4 个不同主题的创建会话与 A/B 共 6 会话并发、后续追问、服务重启后续问通过。
  杀死单个 worker 时其他会话继续完成；恢复及后续追问通过。强制结束 API 后孤儿 worker
  退出，重启将原 Turn 标记中断，显式恢复成功。
- 引用修复后的最终两轮 6 会话共 12 次请求全部完成，逐条检查本轮工具 transcript，
  没有工具错误、`unknown_ref` 或 `unknown_cursor`。修复前发现的错误仍保留在原始日志中，
  不计作“修复后零错误”的依据。
- A/B 的真实工具直接跨两个新进程复用同一账号、评论、父帖引用及分页 cursor，成功读取
  详情与下一页；该检查不依赖模型重新查询来绕开失效引用。
- 前端 TypeScript 检查、Vite 构建、5 项展示测试通过。浏览器验证切换 A/B/创建会话、
  执行中刷新后恢复、完成后输入框可继续输入。
- 最后检查：没有遗留运行中 Turn 或 turn_worker；SQLite integrity_check=ok、外键违规 0。

有限轮次验收不能保证模型/供应商永不失败；这里验证的是会话隔离、连续引用可用、
容量排队和失败后的可恢复性。本次使用隔离数据库副本，没有启动采集 Worker 或新爬取任务。

## 复核入口与证据

修复版 UI：`http://127.0.0.1:3138/investigation`；API：`http://127.0.0.1:8137`。
原 UI 3128 / API 8127 仍是冻结基线，不属于已修复入口。

私有验收目录：`~/.codex/artifacts/m3-concurrency-dc53807-20260909/`。
其中 `live-results.json` 保存逐次 Turn 与进程采样，`summary.json` 汇总终态和工具错误，
`backend-tests-final.log` 保存回归结果，`live-fixed-refs.log` 保存最终 12 次请求，
`real_reference_probe.py` 可复核跨进程引用和分页。该目录含私有业务数据，不提交到仓库。

可移植测试：`tests/test_turn_process.py`、`tests/test_reference_state.py`。
