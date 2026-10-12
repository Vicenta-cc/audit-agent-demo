# 后端采集代理配置

代理由后端运维配置，账号管理页面只管理账号、登录与只读状态，不提供代理配置 UI。现有 `audit_index.sqlite3` 的 `proxy_resources` 表仍是唯一代理资源存储；入口和凭据用现有 AuthStateCipher 加密。账号安全实现会添加状态列及出口、路由事件、admission 和 lease 表，不创建第二套代理配置库。JSON 文件是一次性操作输入，不是启动时自动同步的配置源。

使用应用现有 Python 环境，在同一后端运行配置下执行。`XHS_AUDIT_DATA_DIR`、`CRAWLER_BROWSER_PROFILE_ROOT`、`TASK_RESOURCE_LOCK_DIR`、`REQUEST_SCHEDULER_DB`、`APP_AUTH_DB`，以及原有 `CRAWLER_AUTH_KEY_FILE` 或密钥配置必须与后端一致；不要用另一数据目录、密钥或锁目录绕过占用。MediaCrawler 的实际目录、解释器也须继承后端配置；子进程的 profile root 由后端传递。

本文命令和 JSON 格式已对照源码检查，未执行这些运维命令，也未验证真实供应商或平台网络。路径及 ID 均为占位符；执行前替换为受控环境中的值。

## 代理资源运维

```sh
.venv/bin/python scripts/crawler_proxy_config.py --list
.venv/bin/python scripts/crawler_proxy_config.py --apply /absolute/private/proxy-create.json --prompt-credentials
```

`--config FILE` 是 `--apply FILE` 的别名，每次只处理一个操作。创建输入示例（不含密码；有效期未知时填写 null，保持不可采集，真实期限确认后更新）：

```json
{
  "operation": "create",
  "fields": {
    "display_name": "悟空固定入口",
    "provider": "wukong",
    "platform": "dy",
    "endpoint": {"scheme": "http", "host": "proxy.example.com", "port": 8080},
    "expires_at": null,
    "max_bound_accounts": 1,
    "max_active_accounts": 1
  }
}
```

有效期用供应商确认的 Unix 时间戳。示例入口仅说明格式，不是真实资源。新账号绑定默认资源需要真实未来有效期。`max_bound_accounts=N` 控制绑定上限，`max_active_accounts` 控制同时活跃账号且不得超过 N 与现有全局并发上限；不会自动扩容或故障切换。

用户名和密码均以 getpass 隐藏输入。自动化可使用 `--credentials-stdin`，由受控秘密管理器向 stdin 写入仅含 username/password 的 JSON；不要把秘密放进文件、命令参数、环境变量、聊天或日志。CLI 成功输出仅为资源 ID、修订、状态和容量等白名单字段，失败仅输出固定错误码。`--list` 不输出入口、显示名称或凭据。非法命令参数也不回显原值。

资源更新输入必须填写 `proxy_id` 与读取到的 `expected_revision`：

```json
{"operation":"update","proxy_id":"RESOURCE_ID","expected_revision":1,"fields":{"max_bound_accounts":2,"max_active_accounts":1}}
```

未输入新凭据时保留原密文；清除凭据需在 fields 中明确填写 `clear_credentials:true`。修改入口或凭据后须重新验证登录路由。`status:active` 使用原有维护恢复路径；不会补造有效期或自动 probe。更新和绑定复用原有实际账号/代理独占锁，使用中返回 `PROXY_BUSY`，过期修订返回 `PROXY_REVISION_MISMATCH`。

后端已有账号的绑定也可用同一运维工具：

```json
{"operation":"bind","account_id":"ACCOUNT_ID","proxy_id":"RESOURCE_ID","expected_binding_revision":0}
```

解绑输入为 `operation:unbind`、`account_id`、`expected_binding_revision`。绑定/解绑保持账号和代理的锁顺序以及容量/版本约束；解绑 required 账号不会退回直连。此 CLI 是有宿主数据库访问权的运维操作，不是用户 ACL 授权工具，不会创建账号所有权或代理 use grant。

## 后台开关与资源池

创建资源成功后，将非秘密的资源 ID 配置到后端实际 env source。保留旧严格代理配置时：

```dotenv
CRAWLER_PROXY_ENABLED=true
CRAWLER_PROXY_DEFAULT_ID=RESOURCE_ID
```

安全调度启用示例（受控直连仍关闭）：

```dotenv
CRAWLER_PROXY_ENABLED=true
CRAWLER_SAFETY_ENABLED=true
CRAWLER_DIRECT_DEGRADE_ENABLED=false
CRAWLER_PROXY_POOL_IDS=RESOURCE_ID_A,RESOURCE_ID_B
```

| 配置 | 默认值 | 作用 |
| --- | --- | --- |
| `CRAWLER_SAFETY_ENABLED` | `false` | 启用抖音账号/共享出口安全调度、派发时刷新与恢复检查 |
| `CRAWLER_DIRECT_DEGRADE_ENABLED` | `false` | 允许满足完整门槛的 `proxy_preferred` 账号建立新受控直连路由；单独打开无效 |
| `CRAWLER_PROXY_POOL_IDS` | 空 | 安全模式下的后台候选池，逗号分隔、去重；空值使用数据库内 active 抖音资源候选，仍检查 ACL/状态/容量 |
| `CRAWLER_EXIT_OBSERVATION_TTL` | `300` 秒 | 出口观测的新鲜度；过期只阻止准入，不构成代理故障证据 |
| `CRAWLER_SHARED_EXIT_ACTIVE_LIMIT` | `1` | 同一规范化物理出口的活跃槽位上限 |
| `CRAWLER_FAIR_SKIP_LIMIT` | `3` | 合格账号的公平选择跳过阈值，不能越过 ACL、冷却或容量 |

布尔开关只接受 `true` / `1` 为开启。资源池元素只能含字母、数字、下划线、点或连字符，长度 1–128；非法列表解析为空，因此配置前应校验，不能把错误字符串当成“禁止所有资源”的设置。安全开关关闭时使用 `CRAWLER_PROXY_DEFAULT_ID`，不会读取新池进行默认选择。

配置在后端启动时读取，不支持动态重载承诺。`scripts/local_audit_runtime.py` 的环境由其配置指定的来源构建，单独 shell export 不保证继承。应用启用代理时，前端普通抖音新增账号请求省略 `proxy_id`，后端先过滤调用者的原有 use ACL，再选择状态/期限/冷却/绑定容量合格的资源；绑定时重新检查锁和容量，发生竞争会尝试池内其他候选。没有授权候选返回 `PROXY_CONFIGURATION_REQUIRED`；池不可用返回 `PROXY_UNAVAILABLE`。私有账号所有权登记与绑定失败回滚保持不变。显式 `proxy_id` 的旧 API 保持兼容。

未观测出口的资源可分配给新账号完成登录引导，但不能直接进入业务采集；未知或已暂停的物理出口受到准入隔离。池选择只为新绑定选资源，不自动重绑已有账号。供应商“固定入口”不能证明永久固定出口 IP；IPv4/IPv6 按实际 echo 返回地址规范化，共享出口容量与风险暂停按该地址协调，不按供应商资源名分隔。

## 账号路由策略

旧 `proxy_required=true` 账号继续严格代理。安全模式下普通新增账号的后台默认绑定采用 `proxy_preferred`；显式代理绑定与 CLI `bind` 保持严格策略。改变已有账号策略须由运维明确执行 `safety-policy`，不能靠打开安全或直连开关隐式放宽。

将以下非秘密元数据保存为 `/absolute/private/account-policy.json`；`expected_binding_revision` 使用当前账号只读响应中的真实版本：

```json
{"operation":"safety-policy","account_id":"ACCOUNT_ID","expected_binding_revision":1,"route_mode":"proxy_preferred"}
```

```sh
.venv/bin/python scripts/crawler_proxy_config.py --apply /absolute/private/account-policy.json
```

策略只允许 `proxy_required` / `proxy_preferred`；改回严格代理时把 `route_mode` 改为 `proxy_required`。操作持有原有账号/Profile lease，检查绑定版本，并在事务中拒绝存在 active route admission 的账号。成功 stdout 为 `item` 下的 `account_id`、`proxy_id`、`binding_revision`；失败仅为 `code` 固定码，进程返回 1。该操作不增加绑定版本，不清除风险、冷却、人工处理状态或登录验证，不授予 ACL；不要重复使用旧版本来绕过重新读取。

恢复代理资源可继续使用上文的 `update` / `bind` 操作，随后在原 Profile 上完成可信登录验证；修改策略或 `status:active` 本身不是恢复采集证明。风险人工恢复复用账号页面现有登录操作，V2 与旧登录路径均绑定已授权用户、原账号身份、Profile、出口和原冷却期限；可信 self 身份验证与 auth 保存成功后，仍须等待冷却结束。详见下述安全运维文档。当前 CLI 没有专门的强制 drain、强制风险清除、出口注入或直接进入 `direct_degraded` 操作。不要手改 SQLite 状态或把内部 probe 子进程当作公开运维 CLI。

## 只读状态与运行边界

账号 API 的 `collection_ready` / `collection_block_reason` 及路由模式、暂停/恢复状态、出口版本等字段只读。它们依据当前开关、ACL、状态/期限/冷却、绑定与配置修订、登录及已存出口观测决定；查询不 probe、不派发恢复，也不取得运行 lease。实际采集仍须执行 ACK、双通道出口验证及准入检查。前端不提供代理配置或安全阈值编辑。

安全门槛、恢复与回滚见 [抖音账号安全运维](douyin-account-safety.md)。这里的阈值是待运行验证的保守实验配置，不是平台认可的安全额度；本文不证明已部署、生产可用或真实账号无风险。系统不自动执行供应商购买、平台业务操作或删除 Profile/cookie。
