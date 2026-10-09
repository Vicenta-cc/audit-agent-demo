# 抖音登录 V2 实现与验收记录

日期：2026-10-09（Asia/Shanghai）。开发分支：`feature/douyin-login-v2`，开发起点：`6fda76a28963b4e10f5c150e955276bad90aa724`。本文记录实现范围及本地验证，不代表生产发布、真实账号验收或独立最终审查已通过。

## 实现范围

抖音新增二维码优先登录，短信与图形验证按实际页面出现；未知步骤提供局部真实面板人工操作。小红书、快手保留原入口。采集任务中的风险仍按既有冷却和切换账号处理，不接入人工续采。

- 同一内部账号继续调用现有账号浏览器适配器，复用 Profile、注册 seed、固定版本及账号资源锁。重新扫码不会重新分配浏览器身份；不承诺 seed 能保证所有指纹值均不同，也不改变网络出口。
- 二维码单独提取，变化时更新；等待扫码不连续传整页截图。验证码区域使用 JPEG 二进制媒体包，保留题目、提示及操作区；输入映射回原浏览器，同一次会话完成短信、滑块、点选或绘制。
- 状态 phase 是兼容 status 的唯一来源。扫码、短信、图形与未知步骤允许按平台实际结果转换；只有身份核对、写盘、同 Profile 重开检查及数据库提交完成后才返回成功。
- 发送短信和提交验证码由用户触发。敏感命令先持久记录再派发，重复 command_id 不重放；派发后崩溃或平台反馈缺失返回 unknown，不把浏览器点击成功等同于平台副作用确认。
- WebSocket 通过现有应用 Cookie、明确 Origin 和一次性短期票据认证。REST/WS 共享控制 epoch、状态 revision 和验证区域版本检查；重连后旧窗口及过期题目操作失效。票据不进入 URL。
- helper 小事件走有界 NDJSON，媒体与凭据走 socketpair 二进制 IPC；凭据包不转发到前端。队列、包大小、写入超时及半包失败路径受限。

主要代码：`backend/audit_agent/crawler_login_protocol.py`、`crawler_login_operation_store.py`、`crawler_login_v2_manager.py`、`crawler_login_v2_api.py`；`scripts/crawler_douyin_login_adapter.py`、`crawler_douyin_login_v2.py`、`crawler_login_media.py`；前端 `CrawlerLoginV2View.tsx`、`CrawlerLoginChallengeCanvas.tsx` 与 `crawlerLoginV2.ts`。

## 保存与账号风险

Profile 为浏览器持久状态；数据库使用现有加密机制保存导出凭据。登录 helper 在关闭写盘后，按同一账号浏览器配置重新打开并核对登录态和本人身份，再提交加密备份。旧备份不能覆盖已建立权威标记的 Profile。不能仅凭 Cookie 存在宣称本人身份已确认。

成功保存不解除抖音风险冷却；可达旧版抖音入口也执行共同保护。已有账号绑定身份必须匹配；错误账号且本次已经改变认证存储时，按父进程授权的清理握手处理。未改变旧凭据的检查不因失败清空原状态。非抖音旧逻辑保持原范围。

## 故障恢复

- 页面刷新或短断线保留活动浏览器，重新取得单次 ticket，清空旧轨迹并恢复当前快照；不自动重发敏感动作。
- 显式关闭执行服务端取消，返回错误时不假装取消成功。已提交后的迟到取消返回已有完成结果。
- API 重启后查询持久 operation。已中断操作通过 `recover`、`source_operation_id` 和新幂等 key 显式恢复，不回放旧命令或直接清 Profile。
- 活动会话需原应用会话和 owner；中断后的恢复依据原用户及当前账号 manage 权限重新授权，管理员不能绕过另一用户归属。
- helper 断管、超时、认证失效均关闭输入、终止手势和浏览器，保留 Profile 写盘及资源锁直到清理结束。截图暂时失效可短暂重试，持续失败停止并报告，避免永远显示旧画面。

## 配置与回退

默认 `CRAWLER_LOGIN_V2_ENABLED=false`，入口 `home`、模式 `headless`、资源策略 `conservative`。开启需要显式部署 Origin；不能用通配符。self、precise 和 compatibility 应在部署环境补相应验证。详见 [运行说明](runtime/DOUYIN-LOGIN-V2.md)。

配置转换函数只移除十个 V2-only settings，保留 runtime_root、秘密文件引用、Profile 数据和爬虫版本；其他未知字段拒绝转换。此 feature 实现了共同抖音风险保护，因此回退目标应是包含同等保护且实际验证启动的固定 release，不能把原始开发起点 main 当成安全回退基线。切换前排空活动登录；新 operation 表不因回退删除。

## 验证边界

本地测试覆盖协议、身份和操作持久化、API/WS 权限、控制权替换、幂等与恢复、风险状态保留，以及模拟页面的二维码、短信、滑块和绘制。真实 helper 子进程的集成测试使用本地 HTTP fixture；固定 Python 3.11、Playwright 与 CloakBrowser 145 的浏览器测试也使用本地 fixture，能够证明操作及同 Profile 写盘/重开行为。

此前匿名抖音调研证据保存在 [research.json](evidence/douyin-login-research/20261009/research.json)：它说明匿名入口和缓存/局部截图能力，不证明真实账号已扫码成功。

尚未完成的发布验收：真实抖音登录后的可信身份响应；自然触发的短信、滑块、点选与绘制；生产 Linux/Xvfb 兼容模式；正式反代与网络条件下加载字节数、首个二维码时间、动作反馈 P95 和连续帧率。不能把本地 fixture 或匿名页面性能推算成生产效果。

最终测试数字及独立审查结果由主 agent 的新鲜质量门禁记录给出；本文件不提前宣称通过审查或已经上线。
