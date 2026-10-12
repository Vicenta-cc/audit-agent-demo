# 后端采集代理配置

代理由后端运维配置，账号管理页面只管理账号、登录与只读状态。现有 `audit_index.sqlite3` 的 `proxy_resources` 表仍是唯一资源存储；入口和凭据用现有 AuthStateCipher 加密，无迁移或第二套配置库。JSON 文件是一次性操作输入，不是启动时自动同步的配置源。

使用应用现有 Python 环境，在同一后端运行配置下执行。`XHS_AUDIT_DATA_DIR`、密钥、账号 profile root、资源锁目录及 scheduler 必须与后端一致；不要用另一数据目录或另一锁目录绕过占用。

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

创建资源成功后，将非秘密的资源 ID 配置到后端实际 env source：

```dotenv
CRAWLER_PROXY_ENABLED=true
CRAWLER_PROXY_DEFAULT_ID=RESOURCE_ID
```

配置在后端启动时读取，不声称动态重载。`scripts/local_audit_runtime.py --config ...` 的环境由配置指定的来源构建，单独 shell export 不保证继承。应用启用代理时，前端普通抖音新增账号请求省略 proxy_id，后端选择默认资源，再检查调用者的原有 use ACL、有效状态和容量；缺配置返回 `PROXY_CONFIGURATION_REQUIRED`，无使用权限仍隐藏资源并拒绝创建。私有账号所有权登记与原有绑定失败回滚保持不变。显式 proxy_id 的旧 API 保持兼容。

账号 API 只读字段 `collection_ready` / `collection_block_reason` 依据当前功能开关、代理使用权限、有效期/状态/冷却、绑定与配置修订、账号登录状态决定。它不代表已经持有运行 lease 或最新双通道 probe receipt；实际采集仍执行原有运行时校验。无配置、网络不可用、待路由登录验证、待登录和账号冷却分别返回固定原因码，不返回凭据。
