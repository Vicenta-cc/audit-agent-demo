# 统一运行入口（macOS / Linux）

入口：`scripts/audit_runtime.py`。它提供通用初始化、统一环境构造、真实启动前检查和服务模板；不自动安装依赖、重启已有服务、迁移旧数据或建立 SSH/Coder 隧道。Python/Node/爬虫安装见 [CURRENT-STARTUP.md](CURRENT-STARTUP.md)。完整跨平台依赖锁定仍是后续工作。

## 配置只保留两份文件

在仓库外新建 runtime；`init` 拒绝覆盖任何现有目录。这里的 Python 必须是应用专用虚拟环境解释器。

```bash
/absolute/app/.venv/bin/python scripts/audit_runtime.py init --directory /absolute/new-runtime
```

生成：

- `runtime.json`：schema_version=1，源码与爬虫完整 SHA、解释器/Node/ffmpeg 路径、端口、Dolphin URL、业务模型设置。
- `secrets.env`：权限 600，只接受 `DASHSCOPE_API_KEY`、`RESOURCE_GENERATION_API_KEY`、`REMOTE_INFERENCE_API_KEY`，可选 `CRAWLER_AUTH_ENCRYPTION_KEY`。前三者必须填；新环境未填第四项时应用在自己 data 目录保存加密 Key，后续备份/迁移必须一并保留。

填写 `/EDIT/...` 等占位路径；默认模型仅为候选配置，需账号有访问权。修改源码或切换提交后同步更新 commit，不能用 `main` 等浮动引用。源码/爬虫均须干净；控制器也必须从 manifest 指定工作树执行。不要将 runtime 放进 Git 工作树。

API、worker、维修 Python 命令由同一个环境构造函数启动：不继承调用 shell 的 Key/PYTHONPATH，禁止 python-dotenv 自动加载仓库 `.env`。业务设置只允许白名单，不能借 settings 覆盖 PATH、数据目录、认证模式或供应商 URL。当前固定普通阶段 DashScope、资源阶段 DMX，ASR 为 Dolphin；其他供应商需显式扩展协议，不支持偷偷改 URL。

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
5. DashScope 小 JSON 请求、DMX 独立 Key 的小请求。
6. 两个 SQLite 数据库完整性。

收据：`receipts/check-latest.json`，包含 manifest 摘要而非密钥。失败返回阶段名和错误类型，隐藏第三方响应体，防止 Key 泄漏。根据阶段核对对应依赖或凭据，不绕过检查。`check` 不等价于业务问答或浏览器登录验收，首次启动后仍需实际小任务和报告问答。

## 前台试运行

三个终端分别执行以下命令；每一个 start 都强制预检，并串行保护探针文件。它们是前台命令，终端关闭会停止对应进程。

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

当前实现测试覆盖配置隔离、错误阻断、密钥权限、前端隔离、源版本限制、数据库初始化与模板生成。新控制器尚未完成全新环境的端到端启动、采集及报告问答验收，本机现有服务也尚未迁移；不能将测试通过视为 macOS 或 Linux 部署验收通过。完整依赖锁文件、镜像构建和自动迁移仍未包含在本轮。
