# 固定版本持久服务交付

`deploy/camoufox/services.py` 只输出审核目录和定义摘要，不连接远端、不安装、不执行 systemctl、不启停服务。支持受现有 manifest 校验允许的 staging 和 production；生产地域能力仍受正式发布契约约束，生成单元不构成放行。

## 生成与核验

在目标 Linux 主机固定资产已就绪后，用固定 App Python 执行：

```sh
/PINNED/app-python -B /PINNED/app/deploy/camoufox/services.py render /RELEASE/manifest.json /PRIVATE/new-review-directory --prefix content-audit-candidate
systemd-analyze verify /PRIVATE/new-review-directory/content-audit-candidate-api.service /PRIVATE/new-review-directory/content-audit-candidate-worker.service /PRIVATE/new-review-directory/content-audit-candidate-frontend.service
```

输出目录必须不存在，父目录已存在；目录 0700、文件 0600，不可写入固定源码、资产或 systemd 实际单元目录。审核目录本身不含密钥。每次生成和 ExecStartPre 均验证完整 manifest、源码提交、工作区清洁和全部资产摘要。运行时支持必须属于该 App 的 `deploy/camoufox/runtime-support`。固定 Python 与三个入口路径来自同一 manifest。单元固定 manifest 文件字节摘要，维护开关或其他配置改变后必须重新生成并审核单元；不得悄悄修改 manifest 后沿用旧定义。

App 父进程保持 root/root，子进程的非 root 身份、权限、账号锁、出口保护和环境清洗沿用现有入口。单元不导出私有文件内容，不加载 EnvironmentFile，显式禁用 dotenv、字节码和 user-site。资源限制延续候选：API 2G、worker 4G、frontend 256M，每单元 CPU 200%、Tasks 512、FD8192、UMask0077。

## 启动、停止与恢复语义

三单元可 enable 到 multi-user.target，依赖 network-online；worker Wants/After API，frontend Wants/After API 和 worker。Type=exec 只确认启动程序成功执行，不代表 HTTP 已就绪，正式放流仍须接口健康、鉴权、配置和既有报告读取验收。API 故障不通过 Requires 连带停止 worker。

Restart=on-failure，API/frontend 延迟10秒，worker延迟310秒，1800秒内最多启动3次，失败超限保留失败状态供排查。worker延迟超过当前代码默认300秒执行租约；不修改、删除或强行释放任何任务、账号或代理执行权。重启后的恢复仍走已有 fencing、执行归属和媒体就绪保护。参数将来改变时，需同步审查延迟值。

KillMode=control-group、TimeoutStopSec=45 保持现有停止语义，进程组不遗留下载子进程。当前 runtime-support/worker.py 直接运行单进程 build_worker().run_forever，**没有接入 backend worker CLI 的信号排空监督器**；因此本交付不承诺运行任务在45秒内优雅完成，不能在有任务时依靠 restart 达成无损切换。异常停止后可能须等待租约过期，未知采集结果仍可能进入受控中断，不能以自动重启代表任务自动成功或无条件重跑。正常退出和人工 stop 不会被 on-failure 自动重启。

## 安装和回退前提

1. Linux systemd-analyze verify 无错误，并保存结果；静态核验不代表运行验收。
2. 保存实际同名单元 FragmentPath、内容和摘要、transient 状态、启动命令、PID/cgroup、启用状态、manifest 和依赖身份。存在未知持久单元、别名或同名 transient 时不能覆盖；先完成受控空闲窗口、备份，停止精确旧单元并确认其 cgroup 无残留，transient 已卸载。
3. 安装时逐个确认目标路径状态，原子写入审核过的字节，再 daemon-reload。安装责任在发布操作，不在此 renderer；它没有 overwrite、install、start、enable 选项。
4. 首先启动已审核单元并核对 FragmentPath、定义摘要、实际环境和健康；确认后才 enable，记录 enabled 状态。不能把存在 Install 段当作已开机自启验收，主机重启演练须单独记录。
5. 失败时阻止新写入并保全新数据，停止精确新单元、确认 cgroup 排空，仅恢复已备份且身份匹配的旧定义和启用状态。若旧服务是 transient，按原实际属性恢复，不与同名持久定义并存。单元回退不自动回滚数据库或 Profile；数据和账号另按已验证迁移账本恢复，禁止覆盖新版新写入。

本地离线测试覆盖私有独占输出、生产与候选定义、真实 Git 源码身份/资产、manifest 变化阻断、路径与 systemd 字符转义、资源/停止/依赖策略。Linux语法、实际安装、自动恢复及 boot 验收由真实发布收据补充，不由离线测试代替。
