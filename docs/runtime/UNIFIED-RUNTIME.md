# 统一运行入口（macOS / Linux）

入口：`scripts/audit_runtime.py`。它提供初始化、统一环境构造、真实预检、当前环境进程管理和服务模板。`up/down/restart` 只操作本 runtime 的进程身份记录，不接管其他窗口或线上服务。依赖安装和数据迁移单独完成；Python/Node/爬虫安装见 [CURRENT-STARTUP.md](CURRENT-STARTUP.md)。

## 配置只保留两份文件

在仓库外新建 runtime；`init` 拒绝覆盖任何现有目录。这里的 Python 必须是应用专用虚拟环境解释器。

```bash
/absolute/app/.venv/bin/python scripts/audit_runtime.py init --directory /absolute/new-runtime
```

生成：

- `runtime.json`：schema_version=1，源码与爬虫完整 SHA、解释器/Node/ffmpeg 路径、端口、Dolphin URL、业务模型设置。
- `secrets.env`：权限 600，接受 `DASHSCOPE_API_KEY`、`RESOURCE_GENERATION_API_KEY`、`CONVERSATION_API_KEY`、`REMOTE_INFERENCE_API_KEY`，以及可选 `CRAWLER_AUTH_ENCRYPTION_KEY`。前四者在新模板中必须填；未填最后一项时应用使用本 runtime 的 `data/crawler_auth.key`，迁移账户密文时必须一并保留原 Key。

填写 `/EDIT/...` 等占位路径；默认模型仅为候选配置，需账号有访问权。修改源码或切换提交后同步更新 commit，不能用 `main` 等浮动引用。源码/爬虫均须干净；控制器也必须从 manifest 指定工作树执行。不要将 runtime 放进 Git 工作树。

API、worker、维修 Python 命令由同一个环境构造函数启动：不继承调用 shell 的 Key、代理或 PYTHONPATH，禁止 python-dotenv 自动加载仓库 `.env`。`settings` 白名单允许 `CONVERSATION_MODEL/BASE_URL`、`RESOURCE_GENERATION_MODEL/BASE_URL`、`DASHSCOPE_BASE_URL`；供应商 URL 必须 HTTPS 且不得包含凭据。新模板的普通对话和资源生成均为 `deepseek-v4-pro-ga-260813`，接口 `https://api.qhose.net/v1`；审核仍用 DashScope，ASR 用 Dolphin。旧 manifest 未声明普通对话配置时仍可加载，预检收据会明确记为 `not_configured`。

数据、输出、账户 Key、浏览器 profile、调度 SQLite、资源锁和 Hermes 状态均从 `runtime_root` 派生。启动不再读取其他测试目录的 profile、账户库或 `/tmp` Key。Python 安装中的依赖必须可直接导入，不能依靠临时加入另一个 checkout 的 `PYTHONPATH`。

## 显式代理与地域运行模式

以下字段可选，未知字段仍会报错：

```json
{
  "model_proxy_url": "http://127.0.0.1:17890",
  "crawler_proxy_url": "http://127.0.0.1:17890",
  "managed_proxy": {
    "binary": "/absolute/bin/mihomo",
    "config": "/absolute/runtime/hk-proxy/config.yaml",
    "port": 17890
  },
  "geographic_runtime": true
}
```

代理 URL 仅接受无凭据的 loopback HTTP/HTTPS/SOCKS 地址。未配置表示清除 shell 代理；这不会关闭系统 TUN。模型代理通过 HTTP(S)_PROXY 注入应用，爬虫只使用独立 `CRAWLER_PROXY_URL`；两者可以相同，也可以分开。localhost 的 API、ASR 等请求通过 NO_PROXY 直连。当前已验证香港出口可下载视频，而 en0 直连抖音超时，因此不能默认把爬虫强制直连。

`managed_proxy` 的配置文件必须位于 runtime 内、权限 600。控制器先启动此代理，再做真实模型预检。代理端口已被其他进程占用时拒绝接管。配置的 mihomo 节点凭据只放该私有 YAML，不进 Git；在 TUN 下需在该 YAML 明确配置正确的物理出口，例如已验证的 `interface-name: en0`，控制器不会修改系统路由。

`geographic_runtime: true` 显式启用现有地域能力组合入口，API、worker 及后续问答进程使用相同 manifest。业务开关、容量、采集上限及 `DY_MEDIA_TIMEOUT_SECONDS` 通过 settings 白名单显式指定；本地验收上限可以小于线上。媒体超时应覆盖完整大文件下载，代理连通不代表 60 秒内一定下载完成。

## 日常统一启停

固定解释器、固定 checkout、固定 runtime 后，日常只用这一个入口：

```bash
/absolute/app/.venv/bin/python /absolute/repo/scripts/audit_runtime.py --config /absolute/runtime/runtime.json up
/absolute/app/.venv/bin/python /absolute/repo/scripts/audit_runtime.py --config /absolute/runtime/runtime.json inspect
/absolute/app/.venv/bin/python /absolute/repo/scripts/audit_runtime.py --config /absolute/runtime/runtime.json restart
/absolute/app/.venv/bin/python /absolute/repo/scripts/audit_runtime.py --config /absolute/runtime/runtime.json down
```

`up` 校验两个干净工作树的完整 SHA，启动专用代理，执行一次完整真实预检，然后依次启动 API、worker、前端。启动失败会撤回本次新启动的服务；已有服务不因另一个服务启动失败而被停止。日志位于 `logs/`，身份记录位于 `pids/`；控制器核对 PID、进程出生时间、进程组和命令后才停止，拒绝误停 PID 已复用或外部进程。`restart` 会停止本 runtime 再按同样流程启动，配置或源码变更必须更新固定 SHA。

这是后台本地日常入口，关闭当前终端后进程仍运行；重启电脑后需再次 `up`，不会擅自安装自启动。`inspect` 不调用模型，返回进程、端口、配置摘要及两个模型名称；认证后的身份核对仍使用下文 `status`。

远端 Dolphin 必须 HTTPS，或使用 loopback SSH/Coder 转发。不要把凭据放 URL。中文/多语种任务核对语种配置，不照搬旧维语配置。

## 首次启动顺序

用完整路径替换下列示例。先准备依赖、`Audit_assistant/node_modules`、一个本地正常短视频和持久 Dolphin 隧道。不要拷贝其他环境的浏览器 Profile 或数据库作为默认初始化方式。

```bash
# 只用于无数据库的新 runtime；不会创建默认账号。
/absolute/app/.venv/bin/python scripts/audit_runtime.py --config /absolute/new-runtime/runtime.json init-db

# 真实调用 Dolphin、普通模型和资源模型，有少量网络请求和模型费用。
/absolute/app/.venv/bin/python scripts/audit_runtime.py --config /absolute/new-runtime/runtime.json check

# 同一个干净环境内，交互输入管理员密码。
/absolute/app/.venv/bin/python scripts/audit_runtime.py --config /absolute/new-runtime/runtime.json run scripts/manage_app_users.py create-admin admin
```

`check` 强制验证：

1. 源码/爬虫 SHA 与干净工作树、固定可执行文件、敏感文件权限。
2. Hermes 0.20.4、问答模块、OpenCV、pydantic_core 导入和安装依赖一致性（无需 pip）。
3. 爬虫及浏览器 Python 模块、前端 Vite 依赖。
4. ffmpeg 实际抽取 3 秒音频，真实 Dolphin 转写并校验返回身份，不能用 health 成功替代。
5. DashScope 小 JSON 请求、资源生成独立 Key 请求、普通对话独立 Key 请求（已配置时）。
6. 两个 SQLite 数据库完整性。

收据：`receipts/check-latest.json`，包含 manifest 摘要而非密钥。失败返回阶段名和错误类型，隐藏第三方响应体，防止 Key 泄漏。根据阶段核对对应依赖或凭据，不绕过检查。`check` 不等价于业务问答或浏览器登录验收，首次启动后仍需实际小任务和报告问答。

## 前台试运行

日常使用上面的 `up`。诊断单个角色时，也可以在三个终端分别执行以下前台命令；每个 start 都会完整预检，终端关闭会停止对应进程。不要与 `up` 在相同端口同时运行。

```bash
/absolute/app/.venv/bin/python scripts/audit_runtime.py --config /absolute/new-runtime/runtime.json start api
/absolute/app/.venv/bin/python scripts/audit_runtime.py --config /absolute/new-runtime/runtime.json start worker
/absolute/app/.venv/bin/python scripts/audit_runtime.py --config /absolute/new-runtime/runtime.json start frontend
```

前端为本地 Vite 开发服务，只接收代理地址、不接收后端密钥；默认打开 `http://127.0.0.1:3398/investigation`。公网生产应单独构建静态前端并配置 HTTPS/同源 `/api` 网关，不能暴露此开发服务。

登录后，用私有 Netscape Cookie 文件检查身份：

```bash
/absolute/app/.venv/bin/python scripts/audit_runtime.py --config /absolute/new-runtime/runtime.json status --cookie /absolute/private-cookie.txt
```

此检查核对 API/前端代理的认证运行身份，以及 worker 清单摘要与进程命令。Cookie 不进 Git，登录过期需重新登录，不能关闭鉴权。内部 `_probe`、`_serve`、`_init-db` 是控制器子进程入口，不是运维启动命令。

## 持久服务模板

```bash
/absolute/app/.venv/bin/python scripts/audit_runtime.py --config /absolute/new-runtime/runtime.json render-services --output /absolute/new-runtime/service-files
```

输出 macOS LaunchAgent `.plist` 与 Linux systemd **用户级** `.service`，不自动安装。审核后只安装当前 OS 的三个文件，其他环境不动。所有模板都调用统一 `start`，每次托管重启也会执行真实检查；有失败退避，避免高频重启。

- macOS：模板放到自己的 `Library/LaunchAgents` 后，用准确标签执行 launchctl bootstrap；不要按端口批量杀进程。
- Linux：模板放到自己的 `.config/systemd/user`，daemon-reload 后按准确名称 enable/start；退出登录后仍需运行时，由管理员确认是否配置用户 linger。
- 隧道需单独持久托管；模板不会获取远端凭据、安装 Dolphin 或自动创建 SSH 转发。
- 预检要求普通模型与资源模型都健康，因此任一供应商不可用会阻止所有新启动。这是当前完整能力部署的严格策略，不是按阶段降级模式。

## 现有 3398/8398 不自动迁移

旧 `local-audit-runtime.sh` 保留原运行环境，不与新控制器混用。切换时先安排无任务窗口：备份数据/加密 Key，明确迁移目录和端口，将三组私有凭据合到新 secrets.env；干净新提交配置好并 check 通过后，再按旧服务标签停止并启动新服务。不能直接在相同端口叠加启动，也不能复制空库覆盖旧数据。

实现测试覆盖配置隔离、三类模型配置、代理隔离、错误阻断、密钥权限、前端隔离、源版本限制、数据库初始化、启动回滚、进程身份拒停和模板生成。测试不能代替实际登录→小任务采集→审核→报告→普通对话→资源生成→重启后再次问答验收；每次交付记录其真实结果和缺口。完整依赖锁文件、镜像构建和自动迁移仍未包含。
