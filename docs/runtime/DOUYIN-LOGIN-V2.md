# 抖音登录 V2 开启与回退

V2 默认关闭；只对抖音账号生效。开发入口仍为 home，self 入口、precise 资源减载和真实二次验证覆盖需要独立验收，不能凭模拟测试认定平台兼容。

## 开启

固定 runtime manifest 的 settings 可设置 `.env.example` 列出的十个 `CRAWLER_LOGIN_V2_*` 字段。至少需要 `CRAWLER_LOGIN_V2_ENABLED=true` 和明确的 `CRAWLER_LOGIN_V2_ORIGINS`，例如 `https://audit.example`。Origin 是协议、主机、端口，不能包含路径、通配符或凭据；代理需转发 WebSocket。不要将连接 ticket 或 owner token 放入 URL 或日志。

默认 headless；compatibility 仅用于新建操作，不在进行中的验证码会话重启浏览器。二维码等待阶段不连续传画面，手工区域才启用局部媒体。

配置在 `scripts/audit_runtime.py` 中明确列入白名单，秘密仍只在 secrets.env，Profile、数据库和密钥路径仍由既有 runtime_root 决定。登录成功不解除账号风险冻结，此保护是共同业务规则，不提供关闭环境变量。

## 配置回退

回退目标必须是已包含共同抖音风险状态保护的 V1 release。不能回到未经修补的旧 main 后假定冻结保护仍成立。

`login_v1_rollback_config(config, target_setting_keys=..., repository=..., commit=...)` 返回独立 manifest，精确移除十个 V2-only settings；即使 ENABLED=false 也移除。target_setting_keys 必须来自目标控制器实际白名单。任何其余未知配置拒绝转换，禁止自动删除全部未知键。repository 和完整 SHA 指向固定回退 release，其余 runtime_root、secrets_file、爬虫版本和数据引用保留。

转换函数不读取秘密、不写 manifest、不启动或停止服务。由运维将输出作为独立 runtime.json 保存并按既有配置检查/服务切换流程执行，先备份数据库和 Profile；不得删除新 operation/command 记录或覆盖现有 Profile。切换前排空活动登录会话并确认 helper 退出。生产 Linux、真实身份来源及未自然触发的验证类型继续分别记录验收缺口。

## 协议验证

`PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_crawler_login_protocol.py tests/test_audit_runtime.py`

此测试覆盖二进制半包、短写、长度边界、IPC-only 凭据、有限坐标、受限命令、Origin 校验，以及配置转换保留秘密引用与数据路径；不访问抖音或真实账号。
