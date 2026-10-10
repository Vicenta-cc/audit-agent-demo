# 悟空代理两小时试用准备与回退

本手册与 `scripts/wukong_proxy_trial.py` 只准备和检查试用，不购买、不注册、不启动平台请求。清单中的限额不是运行时已生效的证明。没有真实资源时状态为 `waiting_resource`；即使离线证据完整，也仍需现场维护占用检查和双通道 probe，`trial_ready` 保持 false。

## 领取前

先完成两仓 mandatory 离线测试、真实浏览器 HTTP forward / HTTPS CONNECT 鉴权、明确加载测试 CA、无直连 trap、凭据脱敏、迁移和回退验收。证据须记录仓库路径/版本、解释器、浏览器真实启动、每 suite 的 collected/passed/skipped/exit_code/test_nodes。mandatory skip、0 collected、缺依赖均 No-Go。不要在领取后的计时内安装浏览器或依赖。

离线通过后，申请**一个长效或粘性 HTTP 入口**，不要申请每请求随机出口的池。订单时长不意味着真实出口永久固定；家庭线路可能自然变化。确认订单起止时间、入口鉴权和切换语义。见[悟空 IP 说明](https://www.wukongdaili.com/jiaocheng/doc/ipshuoming.html)。本手册不替代当前订单的确认，也不测试供应商 API。

管理员在应用代理管理 UI 录入密码，密码仅写入、不可读回；不要发到聊天、CLI、环境变量或日志。试用准备文件仅接受 proxy_id、product、sticky_http、expires_at、owner_authorized、credentials_in_ui 元数据。

## 专用目录与只读检查

使用已存在的应用 `.venv/bin/python` 执行 `scripts/wukong_proxy_trial.py prepare --help`。`prepare` 默认只读：所有路径需为绝对路径，指定 app-root、crawler-root、trial-root、trial-id、scheduler-db、app-python、crawler-python、browser-binary。可加 echo-url、evidence-path、resource-path。echo 必须为明确选定的固定 HTTPS URL，不含认证信息或查询串；默认空会阻止平台实测。

仅在已授权的专用测试账号及新隔离目录下，加 `--dedicated-test-account --write` 创建 0700 目录和 0600 manifest。工具不创建数据库、不复制原 profile、不复制或清空 scheduler、不启动应用。现存 trial 目录拒绝覆盖。manifest 列出专用 storage/account/operation DB、output、profile、两仓与解释器路径，以及实际共用 scheduler 的路径。

优先专用测试账号/profile。必须复用真实 profile 时，本工具**未实现**独占维护与一致性备份，必须先停止该账号相关所有任务并由维护路径取得账号/代理独占；不可复制 profile 后并行运行，不可删 profile 或换 seed 假装修复。

scheduler 优先继承原 DB 的 global `dy`。已有冷却未结束即 No-Go，不能新建空 DB 或新 scope 绕过。专用 scheduler snapshot/恢复合并流程不由本工具实现；需要全系统停止相关调度、维护授权、一致性快照，以及结束后合并更保守 history/next_at/cooldown，原库不得并行分流。

## 运行时证据与配置

manifest 的 `runtime_environment` 使用已有设置：`CRAWLER_PROXY_ENABLED=true`、`CRAWLER_PROXY_ECHO_URL`、`MEDIACRAWLER_DIR`、`XHS_AUDIT_DATA_DIR`、`XHS_AUDIT_OUTPUTS_DIR`、`CRAWLER_BROWSER_PROFILE_ROOT`、`REQUEST_SCHEDULER_DB`。不要直接 source JSON。已有本地启动入口是 `scripts/local-audit-runtime.sh` / `scripts/local_audit_runtime.py --config <runtime.json>`；其环境仅从 runtime.json 指定 env_files/json_env_sources 构建，必须在专用 runtime 配置中明确映射上述变量，外部 shell export 不保证会继承。该入口要求 source commit 与 dirty 状态通过校验。工具没有替你生成完整启动配置或部署。

预算要求：主动 API/顶层导航/主动登录动作合计 40（含重试/URL刷新）；媒体 2 文件，累计 20MiB 或 300 秒先到即停；active=1，最多两个账号顺序使用；自动轮换、定时自动任务禁用。后台浏览器每业务页面累计 300 请求/50MiB、滚动 60 秒 120 请求、活跃 20 分钟触发停止，这不是全浏览器 40 包硬上限。

probe 总预算 96，含失败请求，原子共享；初始 HTTP/browser 各 2 次共 4；周期 48；额外 open 20；边界刷新 24。receipt TTL=5分钟，同 context 才可复用，新 context 不可复用。预算不足 2 次即停止业务，不降频继续。

证据 JSON 的 runtime_controls 必须逐一给 api_total_40、media_files_2、media_bytes_20mb、media_elapsed_300s、probe_total_96、browser_growth_stop、active_accounts_1、rotation_disabled、auto_tasks_disabled 的 verified=true、test_node、config_path。缺控制或缺测试证明时 manifest 标 `required_external_constraints` 且 No-Go，不以人工口头承诺冒充程序强制预算。证据是本地审阅输入，不是权限授予或防篡改凭证；执行前仍须核对其版本和当前运行时一致性。

## 两小时记录模板（尚未执行）

| 时间 | 有限动作 | 记录 |
|---|---|---|
| 0–10 分钟 | 录入资源并先 HTTP 后 browser 探测 | 起止时间、脱敏资源 ID、4 次初始样本、一致性 |
| 10–30 | 单账号原 profile 登录复用 | profile identity、绑定/config revision、页面 counter |
| 30–55 | 小批搜索/作者/详情/评论/短链 | 每操作请求数、scope 与 route provenance |
| 55–75 | 图片与短视频、一次补下载 | 最多 2 文件、20MiB/300 秒、所有跳记录 |
| 75–105 | 有余额才顺序测试备用账号及维护恢复 | 同出口聚合、不新增额度、probe cache/tick/open/boundary计数 |
| 105–120 | 停止新请求，等待子进程与下载退出 | 脱敏总结、pending/失败、资源置维护或到期 |

任何验证码/401/403/429、出口不可观察或不一致/变化、407/断连/到期、秘密泄漏：停止新平台请求，取消在途并标结果不确定，关闭浏览器；不转直连、不换账号继续原验证任务。429 保持 global/resource/exit 冷却，不断言 IP 被封。CDN403/410 仅在原来源账号和版本下至多一次已有 URL 刷新。出现应用回归回离线 fixture，剩余时间不能代替验证。

## 回退

先明确受影响 scope：原账号、原代理、所有仍使用该资源的任务/补下载/存活子进程和同出口调度。维护禁止新 proxy 任务；取消/等待 FD lease 和 request slots 释放，使用实际进程及 lease 检查证据，不能以父进程退出或租期到时当成全部退出。

资源置 maintenance，并确认没有 active execution 后才回退配套两仓版本；保留新增列/表/风险事件、密文、调度 history/cooldown 和原 profile。关闭 feature flag 仍应阻断 proxy_required 账号。老二进制不懂该字段，**不能恢复这些账号运行**；保持禁用/维护直至前滚验证。工具没有自动回滚和清理命令，不删除 profile，不删除调度库。只允许明确从未启用代理的旧路径按原行为工作。

两小时只能证明所测入口、账号、操作和采样窗口，不证明永久固定、多账号安全上限、目标域名全部出口或长期成功率。无真实资源交付 offline-ready / live pending，不称生产可用。


## 已实现的试用预算接口与消费者验收

爬虫新增 `tools/trial_limits.py`，`SharedTrialLimits.from_env(snapshot_scheduler_db)` 使用同一个原 scheduler DB；`MEDIACRAWLER_PROXY_TRIAL_ID` 空时关闭试用预算，不改变普通采集。非空试用 ID 的额度跨进程聚合：操作最多40次，媒体开始最多2次（失败也算），每个 chunk 在接受/保存前原子扣累计20MiB，媒体总计300秒且同一试用最多一个活跃媒体计时器。消费者必须在 finally 调用 finish_media，并把剩余 media_timeout 作为实际网络总超时；取消不退款。超过预算后 ledger 持久化 stopped，整个试用后续平台/媒体操作停止，不因重启绕过；错误沿现有 `PROBE_BUDGET_EXHAUSTED` 通路，文案是有限试用预算耗尽，不只能解释成 echo。

20MiB 是已接受/保存数据上限；网络层可能已经缓冲一个超额 chunk，不声称线上字节绝对不超过20MiB。自动浏览器资源仍按单独增长 counter 停止，不能用40次主动操作描述为40个底层包。

应用配置使用 `CRAWLER_PROXY_TRIAL_ID`，由业务桥接给子进程 trial ID；`CRAWLER_PROXY_TRIAL_JOB_IDS` 初始空，阻断自动派发。只能在隔离实例创建明确试用任务后，将该任务 ID 放入 allowlist，然后通过已有 manual run_once 路径逐次触发；不常驻自动调度。allowlist 是范围约束，不授予账号或代理使用权限，原 owner/use 授权仍必需。

prepare manifest 现在包含上述实际应用变量；共享预算模块的单元测试并不能代替消费者网络集成测试。最终 runtime_controls 证据需由主验收报告根据真实通过的 consumer 测试记录，不使用自填 stub。prepare 继续不发网络且 trial_ready=false；ready_label 为 requires_live_gate（或仍需控制证明），真实运行前需现场复核资源状态/有效期、绑定与配置 revision、同 context 双通道近期 receipt、维护占用和剩余预算。外部 JSON 只是流程审阅输入，不能伪造为安全权限凭证。

领取资源并完成真实维护/双通道 probe 后，可运行只读 `scripts/wukong_proxy_trial.py validate --manifest-path <私有manifest.json> --live-proof-path <脱敏live-proof.json>`。proof仅包含 trial/account/proxy ID、binding/config revision、received_at、maintenance_released、runtime_controls_active 与 browser/http 的 context_id/ip/observed_at/config_revision；不能含凭据。工具重新读当前隔离账号/资源状态、有效期、版本、出口、冷却，以及共享global dy冷却，要求双通道同context且样本/接收时间<=5分钟。通过返回 LIVE_EVIDENCE_CONSISTENT / manual_trial_candidate，是只读流程意见而非权限或真实执行许可，仍必须取得实际owner/use授权、lease、运行时stop/budget检查。JSON不能代替实际运行时的重新校验；不得因该结果绕过平台维护门禁。
