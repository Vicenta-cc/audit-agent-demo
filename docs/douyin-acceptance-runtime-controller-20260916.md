# 抖音 8027 隔离验收运行控制器

这套入口只管理 `/Users/ext.wanghongtao6/Documents/Codex/acceptance/collection-reliability-live-20260915`，不会发现、接管或按端口停止 8017、3198、8198 等其他环境。

## 固定身份

- 应用基线：`bf5117ae7031b10a184773886543bbf4dd573312`，抖音生产代码提交为其父提交 `78bae84`。
- MediaCrawler 基线：`6d2c85bcb02fd7dd1e30822e0cb2103bb9b42f39`。
- 验收端口：`127.0.0.1:8027`。
- 真实数据、输出、账号加密密钥和浏览器 profile 均保留在隔离验收目录中。
- `runtime.json` 固定应用 Python、爬虫 Python、Node、源码仓库、`serve.py` 摘要、运行副本和内容指纹；不保存模型密钥或账号 Cookie。

控制器启动子进程前会丢弃调用 shell 的 `PATH`、`PYTHONPATH` 和其他覆盖项，再使用配置中的绝对路径重建最小环境。应用测试、浏览器测试和 MediaCrawler 测试分别使用各自解释器，避免 OpenCV 与 `pydantic_core` 二进制依赖串扰。

## 命令

```bash
scripts/douyin-acceptance-runtime.sh check
scripts/douyin-acceptance-runtime.sh test
scripts/douyin-acceptance-runtime.sh build
scripts/douyin-acceptance-runtime.sh status
scripts/douyin-acceptance-runtime.sh start
scripts/douyin-acceptance-runtime.sh restart
scripts/douyin-acceptance-runtime.sh stop
```

- `check`：校验固定提交、运行副本内容指纹、解释器、Node、前端构建、数据库完整性和账号密钥文件。
- `test`：从两个固定提交创建临时 detached worktree，执行应用 45 项、浏览器集成 4 项及 3 项条件跳过、爬虫 71 项和前端生产构建。成功后写入 `receipts/baseline-gate-latest.json`。
- `build`：直接调用配置中的 Node、TypeScript 和 Vite，不调用 shell 中的 npm/pnpm，也不重新安装依赖。
- `start/restart/stop`：只管理控制器自己写入的 PID。PID 缺失或不属于隔离入口时拒绝停止进程；不会按端口杀进程。
- `status`：读取 `/api/acceptance-runtime`，同时确认页面 `/saas-v2/tasks` 可用且运行身份与配置一致。

本控制器不会发起抖音采集任务。运行 `test` 只执行本地模拟测试和前端构建；真实 3/20/50 条验收结果仍以 `douyin-live-acceptance-report-20260916.md` 为准。
