# R2 云端启动文件归档

本目录在 2026-09-20 从现有腾讯云 R2 环境读取归档，配套应用 `509d922`、Crawler `efafe318`。代码文件保持服务器字节内容，来源和 SHA-256 见 `SOURCE-SHA256.json`。

这是已有部署的启动源码归档，不是安装器。没有执行线上启动、停止、重启或迁移。

## 文件对应

- `candidate-runtime.sh`、`candidate_runtime.py`：原 runtime 根目录的控制入口。
- `runtime-support/{serve,worker,frontend}.py`：当前 release 的 API、worker、前端入口。
- `runtime.json.example`：核对时的非密钥路径配置，保留当时绝对路径和指纹；不是可直接启动的新机器配置。
- `systemd/`：主服务与两份 drop-in 的原文件，归档用途；relay、ASR、nginx 的完整部署不在本目录。

默认没有 `runtime.json`，避免在仓库目录误启动。控制器要求配置直接位于 runtime_root，检查源码/标签/指纹/数据和构建收据，并限制端口为 3198/8198。不要把该控制器直接用于本地旧正式环境或并行 staging。

## 已配置服务器上的操作

以下命令仅针对已经具备完整 runtime 的 Linux 主机，不在 macOS 工作树执行。检查配置和代码（`check` 不启动服务）：

```bash
export DOUYIN_CANDIDATE_CONFIG=/mnt/datadisk0/xhs-audit-agent-r2-abc/runtime/runtime.json
bash /mnt/datadisk0/xhs-audit-agent-r2-abc/runtime/candidate-runtime.sh check
systemctl status xhs-audit-r2-abc xhs-audit-r2-edge-relay xhs-audit-r2-coder-asr --no-pager
```

检查整体运行身份：

```bash
bash /mnt/datadisk0/xhs-audit-agent-r2-abc/runtime/candidate-runtime.sh status
```

`status` 会更新 `runtime/receipts/status-latest.json`，并不是完全只读。

在计划好的维护时段，确认无运行任务/登录会话后，通过 systemd 管理已部署服务：

```bash
sudo systemctl start xhs-audit-r2-abc
# 如需停止：sudo systemctl stop xhs-audit-r2-abc
# 如需重启：sudo systemctl restart xhs-audit-r2-abc
```

不要同时用控制器直接 start/stop 与 systemd 混合管理。控制器支持 `fingerprint/check/build/status/start/stop/restart`；`build` 会改写 dist 和构建收据，不应作为线上例行检查执行。

## 新机器或新 release

需单独准备 Python、Node、FFmpeg、Hermes、CloakBrowser 与浏览器缓存、Xvfb、X11 依赖、中文字体、私有环境配置、数据库和加密密钥、Profile 及 outputs。原配置中的路径、端口、提交和指纹都必须与目标环境对应；保留虚拟环境 Python 的符号链接路径，不要解析为系统 Python 后再运行。

`formal_runtime_root` 在当前云端配置中指向 R2 自己的 runtime，用于读取私有配置；不能指向本地旧正式目录。源码中的“empty data profile”和媒体初始清单为历史部署语义，不代表现有业务数据为空，也不授权清空数据。

完整版本说明见 [R2 基线文档](../../docs/runtime/R2-FROZEN-BASELINE-20260920.md)。本归档不包含数据库、Cookie、账号凭据、Profile、密钥、secrets.env 或 environment.json；不要把真实私有配置补进 Git。
