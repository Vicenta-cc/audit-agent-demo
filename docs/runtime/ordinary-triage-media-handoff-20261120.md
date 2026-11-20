# 普通精选媒体交付与线路生命周期修复

基线：App a7707e7f4e822e62399a9d0bd05cc52f587a5bfe；Crawler bb3f85fdf9c0460ef8f6dc9da7ddaeb1dd43c0eb。
本分支仅做本地修复与离线验证，不部署，不采用 5bfbf1e 的等待整条分析方案。

## 行为契约与实施计划

1. 先用真实文件锁和阻塞的模型分析复现普通精选跨关键词自占线路；锁定媒体校验现有回归。
2. 复用地域媒体就绪清单的完整解码、哈希、下载 intent/receipt、原子发布和本地恢复逻辑。普通精选采用独立的内容/任务绑定，不使用地域交付身份。
3. 抖音普通精选的候选采集与每条详情分别获取操作线路。详情与必要媒体准备在同一原线路完成，先持久化就绪清单与完整详情快照，退出线路后再入库、进入分析队列。固定出口观测和任务级账号锁继续保留。
4. 分析仅读取就绪媒体；暂停/恢复时仍验证清单，不隐式补下载。媒体关闭也不借用线路。其他采集入口保留原行为。
5. 验证第一条模型阻塞时下一关键词或同词第二条仍可采集；覆盖轮换、控制中断、媒体缺失/损坏与真正外部资源冲突。独立复核变更。

## 边界检查

- 保留 a7707e7 的子进程租约校验与交接；不改底层 close 的借用保护、不提并发、不清锁。
- 普通旧分析路径中的远程补下载有明确历史用途，不全局移除；本次已就绪的交付必须禁止再次平台联网。
- 普通精选强制按详情刷盘，停用该路径重复的后台定时刷盘，避免异线程接触当前线路。
- 地域现有完整流水线失败另行查明，不能以跳过或放松策略换取测试通过。

## 验证记录

主回归 268 项通过（97.87 秒）；随后新增 queued 恢复 4 项通过且媒体流水线全文件重跑 19 项通过；独立地域/固定出口 15 项和地域交付 7 项通过。上述去除重复重跑共 294 个相关用例通过。主回归只见现有 FastAPI/starlette 弃用警告。

主回归文件：

```text
tests/test_triage_route_stream_handoff.py
tests/test_triage_media_readiness.py
tests/test_triage_media_ingestion.py
tests/test_triage_media_pipeline.py
tests/test_triage_source_reuse.py
tests/test_triage_account_rotation.py
tests/test_triaged_search.py
tests/test_crawl_resume_control.py
tests/test_resume_analysis_control.py
tests/test_local_durable_resume.py
tests/test_safety_stream_local_analysis.py
tests/test_analyze_limit_ingestion.py
tests/test_geographic_media_readiness.py
tests/test_geographic_media_crash_recovery.py
```

- 真实 `ExecutionLeaseBundle` 文件锁验证：阻塞第一条模型分析时，下一关键词和同词第二条仍完成采集；第一条详情线路无 borrower、FD 已释放。其他任务真实占用仍拒绝。
- 新普通媒体流水线 19 项通过：真实 PIL 图片复用/补全、本地校验、损坏拒绝、停止、旧 completed/queued 任务及 prepared 暂停遗留恢复；包含流式和旧确认选择模式。
- source_output_id 复用 2 项通过：明确来源目录的 A→B 本地媒体复用成功；JSON 伪造来源绑定在入库前被拒绝。没有新增或声称目录解析承担所有者 ACL。
- 固定出口集成 8 项、地域线路生命周期 4 项、地域双视频交付 3 项，组合 15 项通过。
- 地域交付持久化 `test_geographic_runtime_delivery.py` 7 项通过。
- 改动的 14 个 Python 文件通过 AST/compile；`git diff --check` 通过。仓库没有发现统一 lint/typecheck 配置，本环境没有 ruff/mypy/pyright；未将语法检查宣称为类型检查。
- 独立只读审查发现的跨任务共享绑定泄漏、旧任务完成条目误校验和主动停止误报均已补测试修复。

验证环境：关闭 dotenv，Python 使用 `/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.venv/bin/python`；`MEDIACRAWLER_DIR` 指向指定 crawler 基线，`FFMPEG_PATH` 指向本地 runtime ffmpeg。常规 PYTHONPATH 包含仓库根、hermes_m0 和外部 hermes-agent；历史 `audit_agent` 导入的地域交付测试额外包含 backend。pytest 使用 `-q -p no:cacheprovider --tb=short`。

地域/固定出口集成设置 `CRAWLER_SAFETY_ENABLED=true CRAWLER_PROXY_ENABLED=false CRAWLER_DIRECT_DEGRADE_ENABLED=false`，并为 tests 命名空间显式设置本仓库目录，避开环境已安装的同名包。地域两份测试夹具修正为 pinned 模式真实的 `receipt=None, pinned_context_active=True` 契约，保留全部交付和并行断言。

## 剩余边界

- 仅离线测试，没有访问真实平台、连接服务器或修改部署配置。
- 历史未完成内容若缺少本地必要媒体/可信完成证据，会明确延期；不会在分析线程借新线路、替旧证据补下载或静默漏审。
- 外部任务占用、出口变化、账号验证和平台限流仍会按原策略拒绝；本修复针对普通精选自占资源及交付生命周期，不承诺所有采集不再报错。
- 原 worktree 仍为 5bfbf1e，保持干净；新隔离 worktree 基于 a7707e7，未采用等待整条模型分析结束的修复。
