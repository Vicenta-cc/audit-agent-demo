# 云端部署差异快照（2026-09-20）

分支：`codex/cloud-runtime-snapshot-20260920`。从应用 main `0eac0179ebb1db03360b803c6e3a49f3e6ea0ec6` 派生，只读提取现有部署文件，不修改 main 或线上运行环境。

## 核对结果

| 对象 | 云端 / 本地冻结版本 | 核对 |
| --- | --- | --- |
| 应用业务源码 | `509d922af4d23dec9d488e7f1e3b95042b0c4bf1` | 888 个已跟踪文件 SHA-256 与本地 Git blob 一致，工作树干净 |
| MediaCrawler | `efafe3186400b1020955c6acfdc201235b84a2d8` | 287 个已跟踪文件一致；只有未跟踪运行锁文件 |
| 已归档运行控制器/配置/入口 | `../r2-frozen-20260918/` | 9 个文件与本次云端读取结果逐字节一致 |

本次没有发现需要回收的云端独有应用或爬虫源码修改。差异主要是源码仓库之外的运行配置、服务与依赖。爬虫继续使用原冻结分支和标签，本次不另造内容相同的爬虫分支。

`source-comparison.json` 保存源码比较结果；`files.json` 保存部署文件来源、SHA-256 与仓库位置。已在 main 中归档且字节相同的文件直接引用，避免维护重复副本。

## 本次新增归档

- `edge/edge_relay.py`：Docker 私有网络 `172.19.0.1:13198` 到本机 `127.0.0.1:3198` 的转发脚本。
- `edge/content-audit.nginx.conf`：从当前 edge-proxy 挂载的 Nginx 配置中提取本项目两个 server 块（HTTP 跳转、HTTPS 代理）。不包含其他项目站点和证书私钥。
- `systemd/xhs-audit-r2-edge-relay.service`：公网转发服务。
- `systemd/xhs-audit-r2-coder-asr.service`：当前 ASR 隧道服务，保留原工作区目标。会引用私有 coder.env，文件本身未归档。
- `dependencies/application.json`、`dependencies/crawler.json`：实际 Python 3.11.6 环境中分别 198 / 162 个已安装发行包的名称和版本，不包含私有安装 URL。
- `environment.public.json`：原 environment.json 的明确允许公开的模型名、功能开关、限流/并发等配置。私有服务 URL 排除。此文件是原始配置摘录，实际生效值还会被 `serve.py` 与 runtime.json 覆盖。
- `private-config.schema.json`：需要另行准备的私有配置字段名，所有值统一替换为占位符，不是可运行配置。

主控制器、主 systemd 服务与 drop-in、三个运行入口、路径配置和指纹见 [已有启动归档](../r2-frozen-20260918/README.md)。当前 runtime.json 的 entrypoints 指向 release 下 runtime-support；runtime 根目录还留有早期同名入口和验收脚本，不能误认为当前生效文件，本次不将这些历史副本作为活动部署代码导出。

## 实际入口关系

```text
content-audit.guixucloud.cn:443
  -> edge-proxy-edge-1 / Nginx
  -> 172.19.0.1:13198 / edge_relay.py
  -> 127.0.0.1:3198 / runtime-support/frontend.py
  -> 127.0.0.1:8198 / runtime-support/serve.py
  -> worker / 固定应用 Python / 固定爬虫 Python / 持久化 Profile

xhs-audit-r2-coder-asr
  -> 本地 127.0.0.1:19001 -> 配置的 Coder 工作区 ASR 服务
```

这些地址和服务属于当前服务器快照，不是任意机器通用默认值。Nginx 文件只是本项目摘录，不能覆盖共享代理的完整配置。代码与启动文件中的绝对路径应与目标 runtime 一致。

## 未纳入 Git 的内容

- 真实数据库、报告/媒体/outputs、浏览器 Profile、Cookie、认证状态与加密密钥。
- secrets.env、coder.env、完整私有 environment.json、Hermes 凭据及证书私钥。
- Python 虚拟环境本体、Node/Coder/浏览器二进制和缓存、日志、PID 与锁文件。
- 其他业务容器、其他域名配置、旧系统和旧验收/切换脚本。

依赖清单不是完整可复现锁文件：不包含 wheel/系统库/浏览器二进制与私有包来源，迁移时仍需准备独立环境并验证。需要恢复业务状态时，使用受控私有备份，不能用这个 Git 分支替代数据库/Profile 备份。

## 使用与验证

从 GitHub 拉取该分支查看部署层，精确运行代码仍按上述应用/爬虫冻结提交检出；不要将该分支的文档提交号直接替换 runtime.json 的 application_commit。

本次验证了源码文件字节、既有归档指纹、新增文件语法与 JSON 格式。没有执行安装、采集、build、服务重启或线上配置替换。systemd 和 Nginx 文件是快照，应用到其他环境前应按目标环境重新核验，不要原样运行历史发布脚本。
