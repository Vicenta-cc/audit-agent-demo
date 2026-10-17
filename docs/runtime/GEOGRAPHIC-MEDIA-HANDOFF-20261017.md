# 地域采集与审核交接：离线验证（2026-10-17）

## 范围和版本

工作区：`codex/new-media-app-integration-20261016`，基于 `57db7e7` 的未提交修改。本轮不重启 3499、不真实采集、不调用外部模型、不改线上或 MediaCrawler。其他窗口的四个前端未提交文件保持原样。

本记录替代《GEOGRAPHIC-RECOVERY-FIX-20261016》中“等待上一条审核释放线路”的交接方案：**上一条审核与下一条采集可以并行，审核不得继续占用采集线路。**

## 最终交接边界

1. 详情采集及必要媒体补齐使用原始已授权线路；校验图片可解码、视频完整解码，并将所需媒体清单绑定到精采尝试、内容身份、交付来源和实际审核策略。
2. 同目录临时清单写入并 `fsync`，以 `os.link` 原子发布，禁止旧 worker 覆盖后继者的清单；竞争时严格校验已存在记录。
3. 持久化交付成功后，释放本次采集线路，再投递异步审核。调查级账号归属仍然保留；单代理容量、物理出口互斥、执行权、风险和授权检查继续生效。
4. 审核只读取清单中绑定、哈希相符的本地文件，不再借用采集线路或延迟访问平台。缺失或损坏媒体明确延期，不能以漏审代替补齐。
5. 恢复取消投影受持久化执行权保护；取消已经完成后再次停止，由 API 在同一恢复命令锁下直接收尾，不留下无人处理的“停止中”。更新的已执行恢复仍走原有信号路径。

## 补下载媒体的进程崩溃恢复

补下载开始前，先原子、不覆盖地持久化下载意图，绑定完整任务证据、来源 URL、所需媒体列表和审核策略。每次下载调用使用全新的 nonce 目标，迟到的旧执行者不能覆盖后继者已采用的文件。实际下载继续使用原已授权线路和严格下载器；后者只有在完整响应成功后才把 `.partial` 原子替换为最终文件。

如果进程在最终文件写完后、逐资产完成凭据写入前，或在完成凭据写入后、总清单发布前退出，恢复可仅凭磁盘上的意图找到精确目标，完整解码并计算大小和哈希，校验或发布完成凭据，再发布总清单。恢复不申请线路、不重新下载，不重复详情采集、地域/风险审核、预算结算或持久化入库。执行权、暂停与停止检查仍贯穿校验及发布阶段；执行权异常原样传播。

没有意图的旧 `prepared` 文件、未完成的 `.partial`、缺少必要资产、来源或策略不匹配、文件损坏或凭据篡改仍拒绝交付。已经发布的总清单损坏或缺失时，审核消费者不会自行扫描修复。上述保证针对进程丢失；不保证在下载返回至媒体文件同步之前突然断电仍保留数据，恢复会重新验证现存文件。

图片完整校验同时归类 PIL 的损坏图片及 PNG 校验和异常，执行权检查位于解码异常处理之外。地域视频按现有 MP4 契约强制使用 MOV/MP4 输入解码器并关闭外部引用，拒绝伪装为 `.mp4` 的播放列表，避免清单只绑定列表而漏掉实际解码的其他文件。

主要代码：`backend/audit_agent/geographic_media.py`、`geographic_pipeline.py`、`pipeline.py`，以及 `backend/investigation_creation/store.py`、`worker.py` 和 `backend/main.py`。

## 证据

组合测试 `tests/test_geographic_two_video_handoff.py` 使用生产 worker、队列、入库和报告链路，两个搜索词及两条可完整解码的小视频。故意阻塞第一条模型审核，确认其线路已释放，第二条仍可进入精采；随后允许审核继续，验证两条审核持久化、来源绑定保留、报告包含两条帖子。该测试在旧交接行为下先失败；修改后通过。

另用真实文件锁验证单代理和物理出口的互斥，拒绝截断 MP4。测试禁止 socket 网络，外部爬虫、模型和探测响应使用固定替身；视频生成与解码仅执行本地 ffmpeg。

验证结果（分组有重叠，不累加）：

- 两视频交接、媒体就绪、本地持久化恢复组合：39 passed；其中本地恢复 26 项。此后新增媒体损坏分类与发布竞争回归。
- 最终媒体就绪完整组：16 passed（0.39 秒），覆盖损坏图片补齐或延期、清单来源/策略绑定、竞争发布不覆盖、失权异常与临时文件清理。
- 最终发布加固后重跑两视频交接完整组：3 passed（39.59 秒）。
- 最终地域运行与真实线路生命周期回归：34 passed（193.12 秒），覆盖生产交付回调之前先关闭线路，同时保留调查级账号锁。
- 其他线路交接、地域前置恢复、采集暂停恢复和分析暂停恢复回归已通过；最终 11 个变更 Python 文件 AST 检查及 `git diff --check` 通过。四个前端文件 SHA-256 与修改前记录一致。
- 移除报告前强制 WAL checkpoint 后，`test_geographic_two_video_handoff.py`、`test_geographic_route_lifetime_integration.py`、`test_reporting_live_wal_source.py`、`test_published_report_runtime_snapshot.py` 离线联合重跑：12 passed（91.54 秒），仅有两个依赖弃用警告；两份变更测试 AST 与 `git diff --check` 通过。该结果覆盖无包装的生产报告生成、已提交但未 checkpoint 的 WAL 可见性及快照稳定性，不作为真实模型报告问询验收。
- 补下载恢复的完整媒体测试组：最终独立复跑 52 passed（1.16 秒，无 skip）。两个关键用例在旧实现先失败于 `SUPPLEMENT_DEFERRED`；修复后包含图片及真实本地视频的两处崩溃恢复、部分下载、篡改拒绝、迟到前驱隔离、凭据发布竞争和 `ValueError` 执行权异常原样传播。额外由独立 Python 子进程禁用网络、下载和线路授权后恢复，确认只依赖磁盘记录。审查发现的坏 PNG 异常及未哈希同目录视频依赖均先用真实文件复现，再补修和回归。
- 新增 `test_geographic_media_crash_recovery.py`：3 passed（62.55 秒）。通过生产地域执行器、媒体准备、持久化交付和隔离 SQLite，分别注入下载完成后、总清单发布前、入库后执行器交付标记前的进程丢失；新实例恢复时详情调用、预算、审核证据和已入库内容均不重复，最终恰好一份交付。消费者验证本地文件，重复恢复仍幂等。
- 最终两处审查补修后，完整重跑 `test_geographic_media_readiness.py`、`test_geographic_media_crash_recovery.py`、`test_geographic_two_video_handoff.py`：58 passed、0 skipped（103.95 秒），仅有两个第三方依赖弃用警告；包含 52 项媒体校验、3 项生产执行器/SQLite 崩溃恢复及 3 项两视频交接。最终 AST/compileall、`git diff --check` 通过，四个其他窗口前端文件哈希仍与修改前一致。
- 上一轮线路交接独立代码审查：APPROVE；独立架构审查：CLEAR。已修复当轮审查发现的损坏图片异常分类、取消后再次停止、清单发布覆盖问题。
- 本轮补下载崩溃恢复、WAL 测试清理的独立代码审查：APPROVE；独立架构审查：CLEAR。两处审查发现（PNG 校验和异常分类、自动探测播放列表导致未绑定依赖）已补修，两个审查通道分别重跑最终 52 项媒体测试通过，无遗留阻断项。

复现环境：

```sh
env \
  FFMPEG_PATH=/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-local/bin/ffmpeg \
  PYTHONPATH=.:backend \
  MEDIACRAWLER_DIR=/Users/ext.wanghongtao6/Documents/Codex/projects/MediaCrawler-camoufox-runtime \
  /Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-local/venv-app/bin/python \
  -m pytest -q \
  tests/test_geographic_two_video_handoff.py \
  tests/test_geographic_media_readiness.py \
  tests/test_geographic_media_crash_recovery.py \
  tests/test_local_durable_resume.py \
  tests/test_geographic_runtime_pipeline.py \
  tests/test_geographic_route_lifetime_integration.py \
  tests/test_reporting_live_wal_source.py \
  tests/test_published_report_runtime_snapshot.py
```

## 明确的验收边界

- 本轮证明离线交接逻辑，不证明真实网络、登录态、CDN 或模型延迟稳定；没有测试真实报告问询、帖子详情和跨报告联动。
- 实时 WAL 读取已由 `b410a82` 修复：`CanonicalReportSource` 使用只读 `mode=ro` 读取已提交 WAL，报告运行时先通过 SQLite backup 生成快照，再以 immutable 方式读取快照。本轮移除两视频与线路生命周期测试中生成报告前强制 checkpoint 的包装，直接使用生产报告适配器；专项重跑结果见证据节。
- 有完整意图和最终文件的补下载可按上文恢复；没有足够来源证据的旧文件仍保持待处理，不能凭这次修改自动放行。
- 图片来源索引依赖固定爬虫运行时的 `source_index` 文件命名契约；已核对当前 `MediaCrawler-local-runtime` 与 `MediaCrawler-camoufox-runtime`。不能随意换成旧爬虫副本。
- 完整视频解码会增加本地处理时间；单次校验最多 300 秒并持续检查执行权，取消时终止解码子进程。
- 旧失败调查是否能继续，仍须按其持久化快照和交付证据判断，不能仅凭此次测试重置任务状态或绕过报告完整性检查。
