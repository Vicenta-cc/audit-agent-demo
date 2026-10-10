# 抖音登录 V2：详细开发改造技术方案

- 日期：2026-10-09（Asia/Shanghai）
- 文档状态：技术方案，包含已开发功能及本次待落实的体验修订；真实账号及生产 Linux 完整验收尚未完成，不能以本地模拟通过代替
- 设计修订：2026-10-09 修复只读审查的六项缺口；覆盖协议、存储、恢复、状态图、风险资格和回退，未修改业务代码
- 体验契约修订：2026-10-10 根据真实使用反馈，取消面向用户的兼容模式选择；自动恢复边界、扫码后反馈与验收以根 [DESIGN.md](../DESIGN.md) 为准。本文保留原调研及设计基线，不将文档更新当作功能验收。
- 本轮修复事实：`5d76e55`/`d0f8c80` 修正手机确认优先级、准备/身份等待界限、真实 self 响应及安全准备阶段显示回退/脱敏日志；`a7a3c43` 修正用户步骤展示、移除技术模式选择；`ecd4804` 关闭默认演示归档导入并隔离历史错误。正常真实扫码、保存及重开闭环仍待用户手机确认验收；不能将模拟测试视为真实登录成功，原次手机成功但网页未观察到 Cookie 的过程仍未复原。
- 后续真实重试仍失败：已追加官方网页 QR 状态与轮换协议证据，确认监听关联、原生确认回调等待和刷新能力契约存在需要修复的缺口。以下 6.3 的补充是开发要求，尚不能据此宣布真实手机登录已成功；启动阶段浏览器失败的实际根因也仍待诊断。
- 开发实现基线：`6fda76a28963b4e10f5c150e955276bad90aa724`（最新 `origin/main`；`feature/douyin-login-v2`；以下调研基线保留原样）
- 应用基线：`94adccf8cca4dc333c666cf8eb282584f1400326`
- 本地 MediaCrawler 基线：`21f462f6210139ce25188888445f06f1624d4a07`
- 上游调研基线：`NanmiCoder/MediaCrawler@e9dd17a16338c3915f2335bc609383d316e055f2`
- 目标前端：`Audit_assistant`；目标页面：`/crawler-accounts`
- 实测证据：[research.json](evidence/douyin-login-research/20261009/research.json)

本文中“当前实现”来自代码核对，“已验证”来自官方文档或运行记录，“拟新增”是开发要求，“待验证”是实现或发布前的明确验证项。性能目标不是当前性能结论。

## 1. 目标、范围和最终体验

### 1.1 目标

正常账号通过独立二维码卡片完成扫码，过程中不持续展示或传输抖音首页。平台要求二次验证时，界面只呈现当前需要填写或操作的内容。后台保留账号浏览器身份和登录会话，避免重复下载静态资源、反复启动浏览器和串行 HTTP 操作积压。

验收结果同时覆盖：

1. 普通扫码完整可用。
2. 实际触发的验证步骤可人工处理；未触发的类型明确标记真实覆盖缺口。
3. 正确账号的登录态完成持久化，并能被采集配置读取。
4. 新界面的加载量和操作反馈有可复现的性能数据。
5. 刷新、断线、取消、超时、服务重启和提交结果不确定时，不误报成功或意外重复操作。

### 1.2 范围

本次实现抖音账号登录，包括查询已有登录态、重新扫码、二维码刷新、条件短信验证、条件图形验证、未知步骤人工入口、身份核对、凭据保存和接续检查。

采集中的验证、暂停、冻结、账号轮换、任务恢复和采集限速不接入人工登录界面。小红书、快手及旧前端 `frontend-v2` 保持原入口；可达的旧抖音登录入口也必须执行第 11.3 节的风险字段保护。MediaCrawler 的搜索、详情、评论、请求设备参数及反爬策略不在本次改造范围。

既有指纹 seed、Profile 注册表、浏览器版本不重建。不同机器、不同操作系统和不同网络出口之间迁移账号 Profile 不属于本次功能。

### 1.3 用户流程

| 场景 | 展示 | 行为 |
| --- | --- | --- |
| 新账号或已确认失效 | 获取二维码 → 二维码卡片 | 扫码后根据实际平台状态继续 |
| 已有登录态 | 正在检查 → 已登录及核对后的账号身份 | 提供“重新扫码”；单纯打开窗口不先清凭据 |
| 二维码失效 | “二维码已失效”；平台有真实可见可用刷新控件时显示刷新按钮 | 操作原控件刷新当前二维码，不重启浏览器；没有控件不提供虚假刷新动作 |
| 明确已扫码、待手机确认 | “请在手机确认登录” | 仅有可信扫码信号时展示 |
| 平台明确要求短信 | 手机号/验证码字段、发送或提交按钮 | 人工操作真实原页面中的表单 |
| 平台要求滑块、点选、绘图 | 原始验证面板的局部画面 | 人工完成，不自动解题 |
| 验证类型无法分类 | “完成安全验证”及可操作的原登录/验证区域 | 系统呈现当前步骤，不要求用户判断验证类型或兼容方式 |
| 凭据已经产生 | “正在完成登录，请稍候” | 身份、保存及接续全部通过后才成功，不暴露内部存储步骤 |
| 短暂网络断开且妨碍操作 | “正在恢复登录画面，暂时无法操作” | 系统恢复同一个浏览器和当前步骤，不重发原命令 |
| 登录成功 | “登录成功”、已核对的抖音账号身份及“完成” | 确认账号后返回列表；不以连接成功代替登录成功 |
| 登录成功但账号仍在冷却 | “登录已更新，账号仍在冷却” | 不显示为立即可采集 |

二次验证不是固定步骤，也不限于扫码之后。可能在生成二维码前发生，也可能连续出现多次。用户未触发二次验证时直接完成扫码流程。

二维码刷新和登录会话超时分别处理。拿不到平台准确二维码有效期时，仅显示“等待扫码/已失效”，不以登录会话剩余时长冒充二维码有效期。

用户只负责扫码、手机确认及实际要求的验证。运行模式、Profile、连接状态、控制版本和数据库阶段由系统处理，不作为界面操作项。二维码发出后持续观察真实页面变化；没有可信扫码信号不宣称“已扫码”，出现短信/图形/未知步骤时切换对应可用界面。观察不到下一步或画面不可用须按第 12 节有界处理，不能持续显示空白的“正在确认登录步骤”。兼容性回退只按第 12.3 节内部执行，不把“重新尝试兼容模式”作为用户自救入口。

## 2. 调研结论与设计依据

### 2.1 上游 MediaCrawler

本地 `media_platform/douyin/login.py` 与本次调研的上游文件内容相同；本地 `core.py` 已明显改造。因此不能通过上游 README 推断本地实际执行入口。

| 项目 | 上游实现 | 本方案取舍 |
| --- | --- | --- |
| 浏览器 | 默认 CDP 连接已有 Chrome/Edge；可启动持久浏览器 | 保留本地账号绑定 CloakBrowser，避免改动账号身份体系 |
| 扫码 | 打开登录面板，提取二维码，调用本机图片查看器 | 借鉴单独展示二维码，改成网页卡片及事件更新 |
| 手机登录 | 填写手机号、发送短信，从缓存获取短信码并提交 | 人工本地表单转发；不引入缓存服务接收短信 |
| 滑块 | 有自动处理代码，源码注明准确率不高 | 人工处理，不调用上游自动滑块 |
| 成功检测 | `HasUserLogin` 或 `LOGIN_STATUS` | 保留作为观察信号，增加身份和持久化检查 |
| 登录接续 | 同一浏览器上下文登录后更新客户端 Cookie | 复用现有账号 Profile，验证关闭/重开后的接续 |
| WebUI | 选择登录方式、配置 Cookie；WS 用于日志/状态 | 不能直接当作成熟登录交互组件移植 |

固定来源：

- [上游抖音登录](https://github.com/NanmiCoder/MediaCrawler/blob/e9dd17a16338c3915f2335bc609383d316e055f2/media_platform/douyin/login.py)
- [上游浏览器与采集入口](https://github.com/NanmiCoder/MediaCrawler/blob/e9dd17a16338c3915f2335bc609383d316e055f2/media_platform/douyin/core.py)
- [上游配置](https://github.com/NanmiCoder/MediaCrawler/blob/e9dd17a16338c3915f2335bc609383d316e055f2/config/base_config.py)
- [上游 WebSocket](https://github.com/NanmiCoder/MediaCrawler/blob/e9dd17a16338c3915f2335bc609383d316e055f2/api/routers/websocket.py)

上游关于降低风控的描述不是本项目已完成的效果测量。官方 OAuth 扫码得到的是开放平台 access_token，未确认能替代采集需要的网页登录态，因此不作为本方案的替代登录实现。

### 2.2 本轮实测

环境为 macOS、CloakBrowser 0.5.10 的缓存 Chromium 145.0.7632.109.2、独立临时 Profile、无头模式、1000×760 CSS 视口。CLI 驱动测试使用的协议栈与业务 Python 驱动版本分别记录；业务爬虫环境 Playwright 为 1.45.0。不能以最新版文档为依据直接调用较新 Playwright API。

| 验证项 | 结果 | 解释边界 |
| --- | --- | --- |
| 匿名首页采样 | 完成传输 8,882,845 bytes，其中脚本 7,437,338 bytes | 单次短窗口，不是完整登录成本 |
| 登录面板 | 726×483；二维码 178×178，source 为 data URL | 当前匿名页面结构，未来可能改变 |
| 扫码面板中的短信字段 | 与二维码同时存在 | 字段存在不代表触发二次验证 |
| `/user/self?from_nav=1` | 出现面板及二维码，采样时没有视频播放 | 已预热 Profile；扫码后身份及持久化尚未验证 |
| 本地测试表单、滑块、绘图 | 提交成功、滑块到达 100、收到 100 个绘图点 | 合成页面，不是抖音验证通过 |
| 局部捕获 | 20 次，P95 20ms，平均 17,217 bytes | 不含 IPC、网络和前端解码 |
| 操作后取得局部捕获 | 20 次，P95 28ms | 不等于生产端到端反馈 |
| 完整捕获 | 平均 287,961 bytes | 合成高复杂度背景，不代表抖音固定图片大小 |

缓存对照中，保留缓存的重复访问脚本传输约 35,619 bytes；全局 Playwright route 下约 8,347,321 bytes。观察窗口和阻断条件不同，不能算生产提速比例；它验证了缓存策略值得优先修复。CDP Document 拦截的匿名验证保留了缓存，但全部登录弹窗、iframe、跨域跳转仍需集成测试。

[Playwright 官方 route 文档](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-route)明确说明启用路由会禁用 HTTP 缓存。[截图文档](https://playwright.dev/python/docs/screenshots)支持元素和指定区域捕获。[Chrome 无头文档](https://developer.chrome.com/docs/automation-and-testing/headless)说明无头仍有页面渲染能力；不等于保证抖音真实账号的全部分支兼容。

### 2.3 已确认的现有问题

| 问题 | 当前代码依据 | V2 处理 |
| --- | --- | --- |
| 不到 2 FPS 的截图循环及取图轮询 | `scripts/crawler_interactive_login.py:77`；前端每次取图后再等 550ms | 扫码阶段不捕获整页；验证阶段按需捕获并推送 |
| 输入逐条等待 HTTP，拖动积压 | `CrawlerLoginInteractiveView.tsx:67` | WS 发送，有界输入队列 |
| 每次操作都 bring_to_front | `crawler_interactive_login.py:103` | 只在需要切换目标页面时执行 |
| 页面存在外站导航 guard，但全局 route 禁缓存 | `crawler_interactive_login.py:181` | CDP 按 Document 限制导航，保留静态缓存 |
| 打开登录即清空旧凭据 | `crawler_account_login.py:412`；`crawler_login_pages.py:25` | 查询与明确重新认证分开 |
| 只识别出非空身份时核对预期账号 | `crawler_interactive_login.py:217` | 身份未知不当作匹配 |
| 任意打开页 URL 可能被当作登录身份 | `crawler_account_login.py:384` | 排除作者页，采用可信本人信息来源 |
| 登录保存会解除冷却 | `crawler_account_store.py:293` | 所有可达抖音 V1/V2 入口统一保留风险字段，详见第 11 节 |
| 会话超时也会清除冷却 | `crawler_login_manager.py:352` → `mark_expired` | 会话终止与账号登录态失效分开 |
| HTTP 鉴权中间件不处理 WS | `application_auth/middleware.py:41` | WS 显式认证和权限校验 |
| 现有 nonblocking os.write 不检查短写 | `crawler_login_manager.py:327` | 统一 writer、完整写入和背压处理 |

## 3. 架构选择

### 3.1 主方案

采用“真实账号浏览器 + 平台步骤适配器 + 应用登录会话管理器 + 网页登录组件”。

- 账号浏览器负责原始平台页面、Cookie、存储和验证运行环境。
- 适配器负责识别步骤、提取二维码、定位表单和验证区域。
- 会话管理器负责鉴权、资源锁、输入顺序、数据持久化和生命周期。
- 前端显示结构化步骤；仅人工验证需要局部实时画面。

~~~mermaid
flowchart LR
    UI["登录弹窗：二维码 / 表单 / 验证面板"] <-->|"REST + 鉴权 WebSocket"| API["登录会话管理器"]
    API <-->|"有界控制协议"| H["独立登录子进程"]
    API <-->|"二进制图片与凭据 IPC"| H
    H --> A["抖音步骤适配器"]
    A --> B["账号绑定 CloakBrowser"]
    B <--> P["原账号 Profile"]
    API --> D["账号库 + 登录操作记录"]
~~~

### 3.2 运行边界

V2 第一版保持当前全局单个活动登录会话，不在本次扩大浏览器并发。账号锁仍与采集共享；账号正在采集时返回 409，不抢占。限制同时在 API 进程内及同运行环境的文件租约上执行，防止重启遗留进程造成第二次启动。

登录会话仍由单 API 进程持有。现有单 uvicorn 入口适用；若部署改为多个 API worker 或多副本，必须先独立建设会话路由/共享管理服务，不以轮询重试伪装已支持。预检明确报告该限制。

子进程、Playwright 事件循环与 API 异步事件循环分离。API 不直接持有页面对象；管理器原有线程模型可保留，但不可从线程跨线程使用 asyncio.Queue 或 Playwright Page。

### 3.3 未采用的方案

| 方案 | 本次不采用的原因 |
| --- | --- |
| 全屏 noVNC 主登录 | 输入成熟，但不会自动减少首页依赖，且扩展到浏览器窗口/桌面，增加部署与隔离成本 |
| 高帧率完整 CDP screencast | 本地可运行，但传输无关背景；登录不需要完整持续流 |
| WebRTC 视频远控 | 增加编码、信令和部署依赖，当前局部面板尚无证据需要此复杂度 |
| 重建抖音验证码组件 | 未验证组件可跨会话迁移，平台更新维护风险较高 |
| 接管用户个人 Chrome | 不适合当前服务器账号 Profile 体系，可能混入用户浏览器身份 |
| 官方 OAuth 替代网页 Cookie | 凭证用途不同，缺少能用于现有采集接口的证据 |

这些不是框架不可实现的结论。未来只有在本方案端到端测量未达标且定位到实际瓶颈时，再评估替换捕获/传输组件。


## 4. 模块拆分与文件责任

以下是拟修改或新增文件，不代表已实现。不新增第三方运行依赖；使用现有 FastAPI、Playwright、Python 标准库和前端 WebSocket/Canvas。

| 文件/目录 | 改造责任 |
| --- | --- |
| `backend/audit_agent/crawler_login_manager.py` | V1/V2 分派、资源租约、控制队列、IPC、状态快照、清理 |
| 新增 `backend/audit_agent/crawler_login_protocol.py` | 协议常量、数据验证、状态映射、帧包编码/解码 |
| 新增 `backend/audit_agent/crawler_login_operation_store.py` | 同账号库的操作和敏感命令记录、幂等、崩溃恢复标记 |
| 新增 `backend/audit_agent/crawler_login_v2_api.py` | REST action/ticket/operation 查询及显式 WS 鉴权 |
| `backend/main.py` | 注入已有认证/账号授权依赖，注册 V2 路由，扩展启动参数 |
| 新增 `scripts/crawler_douyin_login_v2.py` | 子进程入口、主循环、保存握手 |
| 新增 `scripts/crawler_douyin_login_adapter.py` | 页面观察、身份来源、二维码/短信/图形定位、入口策略 |
| 新增 `scripts/crawler_login_media.py` | 局部捕获、媒体 IPC、帧背压、捕获调度 |
| `scripts/crawler_account_login.py` | 增加 V2 参数分支，V1 继续走旧路径 |
| `scripts/crawler_login_pages.py` | 复用页面收尾，新增只清认证存储的 V2 reset 函数 |
| `backend/audit_agent/crawler_account_store.py` | 抖音 V1/V2 共用的风险保留保存/失效方法；其他平台旧行为保持 |
| `backend/audit_agent/crawler_browser.py` | 复用账号绑定；补启动配置一致性检查，不重新注册 seed |
| `backend/audit_agent/crawler_account_identity.py` | 增加本人来源验证；保留旧提取接口用于其他平台 |
| `Audit_assistant/src/features/crawler-accounts/CrawlerAccountLoginDialog.tsx` | V1/V2 显示分派 |
| 新增同目录 `CrawlerAccountLoginV2.tsx` | 登录步骤 UI、关闭/取消、恢复状态 |
| 新增同目录 `CrawlerLoginQrCard.tsx`、`CrawlerLoginSmsForm.tsx`、`CrawlerLoginChallengeView.tsx` | 各步骤交互 |
| 新增 `Audit_assistant/src/services/crawlerLoginV2.ts` | 票据、WS、二进制帧解析、重连、身份变化清理 |
| `Audit_assistant/src/services/crawlerAccounts.ts`、`types/crawlerAccounts.ts` | V2 公共类型和映射，兼容旧字段 |
| `Audit_assistant/src/services/apiClient.ts` | 接入认证变化事件；立即关闭 V2 连接并清凭据输入 |
| `Audit_assistant/src/features/crawler-accounts/CrawlerAccountsPage.tsx` | 登录成功与冷却同时展示，不修改调度规则 |
| `Audit_assistant/src/styles/crawler-accounts.css` | 卡片、表单、验证区域、触屏与窄屏样式 |
| `Audit_assistant/vite.config.ts` | 登录 WS 代理 |
| `backend/audit_agent/config.py`、`scripts/audit_runtime.py`、`.env.example` | V2 设置、白名单、启动预检 |
| `tests/test_crawler_login_*`、新增 V2 测试文件 | 按第 14 节验收范围覆盖 |

`scripts/crawler_interactive_login.py` 和旧 `CrawlerLoginInteractiveView.tsx` 保留作为 V1。V2 不复用其中全局 route、固定 550ms 循环和静默忽略输入异常。

协议验证与二进制帧格式有独立文件；平台选择器集中在一个适配器中。其余逻辑尽量留在现有管理器，避免引入通用远程桌面框架或无实际用途的插件系统。

## 5. 会话、操作与状态模型

### 5.1 登录意图

启动参数新增 `intent`：

- `check_or_login`：默认。先检查当前 Profile。已有可信登录及身份则进入接续检查，不重置；确认为未登录才进入扫码。观察不确定时展示当前步骤，不因超时猜测凭据失效。
- `reauth`：用户点击“重新扫码”后明确重新认证。父进程先记录重置意图，再允许子进程清理认证存储。提示“重新扫码会替换当前登录；中途取消后可能需要重新登录”。
- `recover`：仅处理服务器返回的恢复记录，启动 body 必须包含 `source_operation_id`。创建新的恢复 operation 并关联原记录，重新检查 Profile 和身份后补齐保存，不直接回放旧数据库状态；授权及去重规则见第 11.2 节。

“关闭登录窗口”执行取消；网络断开、刷新网页、前端组件因路由卸载，不自动取消浏览器。短暂断开保留当前步骤直至会话期限；显式取消必须得到服务器结果或通过查询确认。

### 5.2 状态兼容

保留旧 `status` 枚举，新增 `phase`。由单个规范化函数从 phase 推导 status，不允许两个字段独立变更。

| V2 phase | 兼容 status | 用户动作 |
| --- | --- | --- |
| `checking`、`preparing` | `starting` | 取消；checking 时可等待已有状态 |
| `qr_ready`、`qr_expired` | `waiting_scan` | 扫码、刷新；按能力可切换短信 |
| `phone_confirm` | `scanned` | 手机确认 |
| `sms_required`、`challenge_required`、`manual_required` | `interactive` | 按 `available_actions` 操作 |
| `identity_check`、`profile_flush`、`reuse_check`、`db_commit` | `finalizing` | 暂停输入；允许在提交前安全取消 |
| `done` | `success` | 关闭；可能仍有冷却说明 |
| `failed`、`expired`、`cancelled` | 同名终态 | 查询结果或适用的通用重试；需重扫时说明当前进度结束 |

新增公共快照：

~~~json
{
  "protocol_version": 2,
  "engine": "v2",
  "id": "login-session-id",
  "operation_id": "login-operation-id",
  "source_operation_id": null,
  "account_id": "crawler-account-id",
  "status": "waiting_scan",
  "phase": "qr_ready",
  "state_revision": 7,
  "controller_epoch": 0,
  "controller_state": "detached",
  "available_actions": ["cancel", "refresh_qr", "open_manual"],
  "expires_at": "2026-10-09T10:10:00Z",
  "qr": {
    "revision": 3,
    "state": "ready",
    "expires_at": null,
    "expires_at_source": "unknown",
    "image_data_url": "data:image/png;base64,..."
  },
  "sms": null,
  "challenge": null,
  "identity": {"status": "pending", "platform_account_id": ""},
  "result": null,
  "error": null
}
~~~

图形步骤 `challenge` 字段包含 `challenge_id`、`area_version`、`kind`（slider/click/draw/unknown）、`interaction_mode`、`viewport`、`clip_rect`、图像尺寸和允许的操作。类型无法可靠识别时用 `unknown` 和保序轨迹策略，不假设它是可丢弃轨迹的普通滑块。

终态快照的 result 包含 `auth_saved`、`identity_verified`、`reuse_verified`、`account_eligibility`（ready/cooling_down/disabled）及 cooldown 时间；没有这些检查结果时不能显示“登录成功、可用”。controller_state 为 detached/attached，首次尚未授权控制时 epoch 为 0。attached 时返回当前 epoch；发生断开或替换时使旧 epoch 失效，所有会产生操作的按钮据此禁用，查询和本人取消仍可使用。

### 5.3 状态转移

~~~mermaid
stateDiagram-v2
    [*] --> checking
    checking --> preparing: 确认未登录或明确 reauth
    checking --> identity_check: 已有可信登录
    checking --> manual_required: 平台步骤不明确
    preparing --> qr_ready: 二维码可用
    preparing --> sms_required: 平台明确要求短信
    preparing --> challenge_required: 先出现验证
    preparing --> manual_required: 无法分类的登录步骤
    qr_ready --> qr_expired: 平台确认过期
    qr_expired --> qr_ready: 用户刷新成功
    qr_ready --> phone_confirm: 可信扫码信号
    qr_ready --> sms_required: 平台要求或用户切换
    qr_ready --> manual_required: 用户打开当前步骤或无法分类
    qr_expired --> manual_required: 用户打开当前步骤
    phone_confirm --> sms_required: 扫码后平台要求短信
    phone_confirm --> challenge_required: 平台要求
    phone_confirm --> manual_required: 无法分类的确认步骤
    phone_confirm --> qr_ready: 平台返回扫码
    sms_required --> challenge_required: 平台要求
    sms_required --> manual_required: 短信步骤无法识别
    sms_required --> phone_confirm: 平台要求手机确认
    sms_required --> qr_ready: 平台返回扫码或用户切换
    challenge_required --> manual_required: 无法分类或适配失败
    challenge_required --> sms_required: 验证结束后回到短信步骤
    challenge_required --> phone_confirm: 验证结束后等待手机确认
    challenge_required --> qr_expired: 平台二维码已经失效
    manual_required --> qr_ready: 平台返回扫码
    manual_required --> qr_expired: 平台二维码已经失效
    manual_required --> sms_required: 重新识别为短信步骤
    manual_required --> phone_confirm: 重新识别为手机确认
    manual_required --> challenge_required: 重新识别为图形验证
    challenge_required --> qr_ready: 平台返回扫码
    qr_ready --> identity_check: 登录标记出现
    phone_confirm --> identity_check: 登录标记出现
    sms_required --> identity_check: 登录标记出现
    challenge_required --> identity_check: 登录标记出现
    manual_required --> identity_check: 登录标记出现
    identity_check --> profile_flush: 身份匹配
    profile_flush --> reuse_check: 写盘完成
    reuse_check --> db_commit: 重开验证通过
    db_commit --> done: 数据库提交确认
~~~

任意非终态可因明确失败、会话期限或用户取消结束。验证码失败只更新当前步骤错误；不必结束整个会话。没有扫码信号时可以从二维码直接到身份检查，不伪造“已扫码”。

图为主要路径，执行以以下统一规则为准：preparing、qr_ready、qr_expired、phone_confirm、sms_required、challenge_required、manual_required 都属于平台交互阶段；观察到可信新步骤时可转到对应交互阶段，同类型下一题保留 phase 并更换 challenge_id。identity_check 时若发现仍有必需验证，返回对应交互阶段；进入 profile_flush/reuse_check 后不执行人工命令，重开检查出现验证则结束为 REUSE_CHECK_FAILED，由用户另行开始登录，不在保存中隐式重新认证。

进入图形/未知步骤时，后台记录 `resume_phase` 和 `pending_command_id`，不接受前端指定返回阶段。图形验证结束后重新观察平台：返回短信则恢复短信表单，等待手机确认则展示 phone_confirm，实际已登录才进入 identity_check。若验证是发送短信或提交的前置步骤，只观察原动作是否由平台继续执行，不自动再次发送；结果不明确时保留 pending/unknown，按第 11.1 节查询记录。

观察器分辨“平台已拒绝/要求验证”和“页面尚未准备好/请求超时”。单次检测失败不得清凭据、解除冷却或自动重新发送短信。

## 6. 平台步骤识别与减载

### 6.1 页面观察器

观察范围覆盖主页面、相关新窗口和验证码 iframe。每个页面/导航有 `page_generation`；每个验证控件有 `challenge_id` 和 `area_version`。

优先级：

1. 服务端终止、权限撤销、用户取消。
2. 明确验证覆盖层及必填确认步骤。
3. 可信登录标记与本人身份。
4. 二维码可用/过期、手机确认等平台状态。
5. 人工面板兜底。

验证覆盖层仍存在时，不仅凭 Cookie 出现就宣布完成。观察采用 Playwright 页面/响应事件与有界 500–1000ms 检查结合，不注入持续改变页面行为的通用代理脚本。同一稳定状态只发送一次，避免 React 不断重渲染。

### 6.2 入口

配置使用固定策略 `home` 或 `self`，不接受前端传任意 URL。

- `home` 为已经使用的兼容入口。
- `self` 对应调研中实际观察到的 `/user/self?from_nav=1`，先用于隔离验收。
- 页面导航默认 30 秒上限；domcontentloaded 后最多观察 8 秒寻找可操作面板。
- 在没有二维码、输入或平台认证操作的准备阶段，可以在同一次浏览器内回退到 home，最多一次。
- 二维码已经发给用户、验证码已经发送或人工验证进行中时，不自动切入口或重载。
- `self` 只有通过真实扫码、本人身份、存储重开检查后才能设为部署默认。

### 6.3 二维码

优先读取已显示且确认属于登录二维码的 img data URL；支持浏览器内读取 blob URL。无法直接取图时，回退到原浏览器中的元素 PNG 截图。禁止为读取图片额外用另一套 HTTP 客户端请求带认证关系的图片地址。

保持原始像素，增加白色留白，优先无损 PNG，不平滑缩放，不在二维码中央叠加账号图标。

每次二维码内容变化生成 qr revision。刷新前旧二维码标记无效，刷新执行结果必须来自原页面新状态；检测到相同有效图片不得伪造新 revision。平台过期文本和明确响应用于 qr_expired。会话总期限单独展示。

#### 6.3.1 官方网页 QR 状态、当前二维码关联与原生完成链路

2026-10-10 匿名官方网页请求发起栈与 Debugger scriptId 映射定位到[官方登录组件](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/async/21138.497e5294.js)，SHA256 `56d797b60ea7cb021b51db7a112d706a5a18e2f34e2fd62b9589b2c9beed3114`。收据保存在仓库外 `../login-v2-preview-runtime/official-qr-protocol-evidence.json` 与 `official-qr-exact-trace-evidence.json`，不含用户 token 或二维码内容。以下为该版本事实的转述，不能当作永久协议或本应用真实登录通过的证据。

| 官方响应别名 | 已确认含义 | 本应用处理要求 |
| --- | --- | --- |
| `new` / `1` | 等待扫描，原网页继续轮询 | 当前二维码可用时展示扫码卡片 |
| `scanned` / `2` | 观察到扫描，继续原网页轮询 | 提示手机确认；必须属于当前二维码 |
| `confirmed` / `3` | 原网页调用登录完成回调并停止 QR 轮询 | 等待原生完成/认证落地，不能直接报 SQL 保存成功 |
| `refused` / `expired` / `4` / `5` | 共用轮换分支，可接受响应中新二维码及 token；该组件最多五次自动换码后失效 | 不把 `4` 独立解释成取消、`5` 独立解释成过期；核对替换图片与当前 ticket，再更新卡片 |

观察器监听原网页 `get_qrcode`、`check_qrconnect` 的真实请求/响应，不另行发送登录请求。必须关联当前 QR ticket、页面 generation 和 DOM 二维码内容摘要；get 响应和 check 返回的替换二维码都更新这一关联。API 图片与当前 DOM 图片不对应时，不把 API 的 scanned/confirmed/expired 应用到屏幕上的另一张码；旧 ticket 的晚到响应不得覆盖新二维码或当前验证步骤。ticket/二维码内容只留运行内存，脱敏日志不记录原值。

官方确认会继续调用其配置的成功回调或导航；内部 accountSDK 的完整跨域 SSO 和最终 Cookie 链路未由该组件证据证明。因此 `confirmed` 但还没有认证凭据时，保留原页面及回调，让原网页完成自己的流程；禁止主动 `goto(self)`、reload、reset 或新建登录打断原生链路。观察到可信认证证据后，才进入本人身份验证；仅在仍缺少可信本人信息时执行一次既有受控 self 核对，不循环导航，也不把确认状态当作身份或保存成功。明确的短信/图形要求仍优先展示，同一二维码 confirmed 的旧响应不得抹掉新验证。

刷新能力只来自原平台真实可见、启用的控件，动作派发前重新检查；没有能力时不将 `refresh_qr` 写入 available_actions，不伪造固定按钮、DOM 控件或新二维码。页面自身更换二维码与本应用重启登录不同：跟随原页面原生轮换并同步卡片可继续同一步，不允许借此主动重载/重置。匿名图片比对已证明 API 和 DOM 二维码字节相同；其编码可为动态 `v.douyin.com` 短链接，是原网页实际登录码，不能仅因域名或“像下载链接”就判为占位图或过滤。该事实不证明用户已扫码成功。

必须回归：当前/旧 ticket 响应交错、check 返回新码并更换 token、原生轮换上限、4/5 不单独误判、confirmed→原生回调→认证落地且无主动导航、确认期间出现验证、认证落地后一次受控本人检查、刷新控件隐藏/禁用/消失。上述测试之外，仍需真实手机扫描→确认→本人→保存→重开的独立收据；未通过前不关闭正式发布门禁。

### 6.4 短信

二维码页面中同时存在手机号字段是正常情况，不构成 sms_required。仅以下情形打开短信表单：

- 平台明确阻断扫码并要求手机号/短信；
- 用户主动选择平台提供的短信登录；
- 短信步骤适配器已经确认当前原页面的字段、发送和提交控件。

sms 快照包含需要的字段、平台实际允许的长度/格式、发送状态、masked_phone、resend_available_at 及来源。普通手机号登录与二次确认区分，不自动填入采集账号 ID。

发送、重发和提交为人工命令。原页面发生前置图形验证时切到图形面板，不能当作短信已发送。失败后根据平台响应展示错误；平台结果未知时显示“正在确认发送结果”，不自动重发。

前端不把手机号、验证码存入 localStorage/sessionStorage。退出、换应用账号、取消后清空输入。后台诊断不记录输入内容。

### 6.5 导航限制及缓存

V2 取消当前 `context.route("**/*")` 做导航 guard 的路径。使用现有 Chromium 支持的 CDP Fetch Document 拦截：

- 顶层导航仅允许已确认的抖音登录域名及 HTTPS。
- iframe 导航按观察到的验证来源进行独立允许列表验证；允许列表不从任意前端参数生成。
- popup 创建时先保持不可操作状态，安装 guard、确认 URL 和用途后才选择为目标。
- 未确认的域名不静默放开，也不循环刷新；显示“当前验证暂时无法完成”及适用动作，保存脱敏诊断来源，不要求用户选择兼容模式。
- 所有 CDP 暂停请求在 bounded handler 中继续或明确拒绝；异常路径不得长期挂住请求。
- 不调用禁用缓存的 Playwright 路由；CDP 版本和 guard 行为在业务 Python 1.45.0 环境实测。

本轮记录中的 `lf-zt.douyin.com` 是存储相关 iframe，不能当作验证码；未知 `cc` frame 也不因出现就加入白名单。

静态缓存保留，包括按站点正常管理的脚本、样式、字体和静态图片。不离线冻结登录脚本版本，不用 HAR 回放认证响应。

### 6.6 无关资源

分两级减载：

1. 默认保守级：停止无关视频播放，并只阻断已经确认的视频请求模式。
2. 实验精准级：增加真实请求清单中确认与登录无关的推荐、媒体、非登录封面；需要独立 A/B 验证才能启用。

不按“所有图片/所有 XHR/所有非 douyin.com”粗暴阻断。媒体可能通过 Fetch、XHR 或动态 CDN URL 获取；`*.mp4` 规则不足以宣称视频完全停止下载。验证码图片、音视频验证资源、指纹/认证依赖属于保留项。

将原有登录导航放进共享 request gate，二维码刷新和短信发送尊重平台限制；减少 UI 延迟不改变业务请求限速。

## 7. 图形验证与未知步骤的人工交互

### 7.1 验证区域选择

顺序为已确认的验证容器/iframe → 活动登录面板 → 同页面有界登录区域。未知步骤由系统定位并呈现可操作画面；二维码仍可正常扫描时，可按平台能力保留“查看登录页面”作为辅助动作，不把它当作用户判断运行模式的入口。

区域要同时包含提示、题目、滑块/绘图区、错误、刷新及提交按钮。不裁掉题目后只留下图片。截图裁剪作用于显示，不把原验证码 DOM 搬到其他文档，不缩小浏览器 viewport 以强行改变平台布局。

无法可靠识别图形类型时按 draw 轨迹保序处理。区域识别失败时一次扩大到完整登录面板；必要时呈现同页面有界的原登录区域，不清理认证、不重开浏览器、不重发短信。区域首帧不可用时显示具体加载失败及适用重试，不能只留空白或无限加载。有界原页面画面不会成为正常扫码默认，也不要求用户主动开启技术诊断入口才看得到必需步骤。

浏览器原生提示不属于页面截图。V2 headless 的普通 JS dialog 按已确认用途处理；外部应用提示保留 compatibility 路径，使用受限 Escape，不能通过页面裁剪声称已经覆盖原生提示。

### 7.2 捕获策略

- 二维码和手机等待阶段：不运行验证截图循环。
- 验证区域刚出现：发送首帧。
- 人工活动时：初始上限 15 FPS，quality 70；最多一个在途捕获。
- 无输入且状态稳定：降到约 2 FPS 观察可能的倒计时和异步结果，相同内容不传输。
- 前端无 controller、页隐藏或已经断线：停发连续帧，浏览器会话仍保留。
- 验证结束、finalizing 和所有终态：立即停止捕获并丢弃晚到帧。
- 截图失败：有界重定位，返回显式诊断；不无限静默吞异常。

选用直接 clip capture，而不是先完整 screencast 后裁剪。是否能达到 15 FPS 由生产端到端测量确认。若捕获或编码成为瓶颈，再替换此模块，不影响状态和输入协议。

### 7.3 坐标与版本

frame 原子包含 CSS clip_rect、viewport、图像尺寸、frame_sequence 和 area_version。前端绘制可缩放，但输入先转为图像区域归一化坐标，再由后台映射到原页面 CSS 坐标：

~~~text
page_x = clip_x + normalized_x * clip_width
page_y = clip_y + normalized_y * clip_height
~~~

DPR 只影响图像像素，不能乘进页面 mouse 坐标。触屏、缩放和页面滚动后必须重新计算边界。浏览器 viewport 在登录期间固定，不跟随弹窗 resize 改变。

area_version 在目标、布局、滚动映射或题目刷新变化时递增。携带旧版本的输入拒绝执行，返回 `STALE_AREA`；前端显示最新帧后重试，不能把旧点击落到新题目。

### 7.4 手势

pointer down/up、点击、键盘和提交保持顺序。前端捕获 pointer，处理 pointercancel、窗口失焦、hidden、断线和组件卸载。

滑块移动可合并未发送的过时位置，但必须保留 gesture 开始、最后位置和抬起。draw/unknown 使用 16–33ms 小批次，保留原始点序号和相对时间；不能像滑块一样只发送终点。不得生成用户没有操作过的解题轨迹。

初始限制：每批最多 64 点，最老尚未执行轨迹年龄 250ms；WS 文本命令不超过 16KiB。超过限制中断当前手势，可靠释放鼠标，并提示重试，不静默丢绘图点。helper 有不依赖普通输入队列的 gesture watchdog，disconnect/abort 可抢先终止未执行移动，防止 mouse up 被堵在队尾。

用户松手、验证刷新或 controller epoch 改变时结束 gesture。人工验证会话恢复后从新手势开始，不重放旧轨迹。


## 8. REST 与 WebSocket 接口

### 8.1 兼容与能力协商

原启动/查询/取消 URL 保留。V2 返回 protocol_version=2；旧字段保留但旧前端不发送 V2 参数时可协商选择已加入风险保护的 V1，避免旧前端收到无法显示的 interactive 状态。未完成风险保护的原始 V1 不作为可降级引擎；开启或关闭 V2 都不能绕过第 11.3 节。

由服务端配置决定是否允许 V2。前端只声明协议能力，不决定部署 engine。V2 活动会话的 engine 在整个生命周期固定，不能在刷新后切成 V1。

启动命令：

~~~http
POST /api/crawler-accounts/{account_id}/login-sessions
X-Login-Token: <本浏览器会话随机 token>
X-CSRF-Token: <现有应用 CSRF>
X-Application-User: <当前应用用户>
Idempotency-Key: <本次启动意图 ID>
Content-Type: application/json
~~~

~~~json
{
  "supported_protocols": [2, 1],
  "intent": "check_or_login",
  "browser_mode": "default"
}
~~~

browser_mode 可为 default 或 compatibility，服务端根据配置和 OS 决定实际模式；不可接受任意浏览器参数。

启动返回 201 新建，200 复用同一活动会话；相同 Idempotency-Key、账号、意图及 owner 返回同一结果。相同 key 不同请求返回 409。已有活动会话必须先由本人恢复或显式结束，不能因为请求 reauth 就悄悄清掉。

恢复请求使用同一启动 URL，示例：

~~~json
{
  "supported_protocols": [2],
  "intent": "recover",
  "source_operation_id": "interrupted-login-operation-id",
  "browser_mode": "default"
}
~~~

recover 必须携带新的 Idempotency-Key，必须支持 V2，不协商降级到 V1；check_or_login/reauth 不允许夹带 source_operation_id。恢复创建新 operation，不更换账号或 Profile，原操作的敏感命令不重放。

### 8.2 接口表

| 方法与路径 | 作用 |
| --- | --- |
| `POST /api/crawler-accounts/{account_id}/login-sessions` | 启动或恢复 |
| `GET /api/crawler-account-login-sessions/{session_id}` | 当前快照；断线后恢复源 |
| `DELETE /api/crawler-account-login-sessions/{session_id}` | 显式取消，保持幂等 |
| `POST /api/crawler-account-login-sessions/{session_id}/actions` | 当前控制权下的低频动作；不能作为高频轨迹通道 |
| `POST /api/crawler-account-login-sessions/{session_id}/ws-tickets` | 取得短期单次连接票据 |
| `WS /api/crawler-account-login-sessions/{session_id}/stream` | 状态、局部媒体、输入及执行回执 |
| `GET /api/crawler-accounts/{account_id}/login-operations/{operation_id}` | API 重启、提交结果不确定时查询持久结果 |
| `GET /api/crawler-accounts/{account_id}/login-operations?recoverable=true` | 原操作用户重新认证后发现可恢复记录，只返回必要状态摘要 |
| `GET /api/crawler-accounts/{account_id}/login-operations/{operation_id}/commands/{command_id}` | 查询本地持久命令结果，不能凭此保证平台实际副作用已确认 |
| 原 `/frame` 与 `/input` | 仅 V1；V2 返回明确不支持，不悄悄降级到慢链路 |

所有 REST 继续使用现有 apiRequest，带 Cookie、CSRF 和预期应用用户。活动会话的快照、ticket、取消、动作检查当前 manage 权限、原应用会话与浏览器 owner。已终止/中断 operation 和命令记录按第 11.2 节校验原用户与当前 manage 权限，不要求已经丢失的旧 owner token；管理员不能绕过另一用户的归属条件。

actions 初版：

~~~text
refresh_qr
switch_to_sms
request_sms
submit_sms
open_manual
refresh_challenge
close_manual
abort_gesture
~~~

REST 和 WS 动作使用同一 command envelope、校验器与单一执行队列。每个动作包含 command_id、operation_id、controller_epoch、sequence、expected_state_revision；作用于验证控件时还包含 challenge_id、area_version。前端只能发送 available_actions 中的操作。REST 返回 202 表示已接收，不代表平台执行成功；通过 WS command_result 或命令 GET 查询确认。

只有已认证的当前 WS controller 才能签发会产生操作的 REST 或 WS 命令。REST 带既有 X-Login-Token，由后台核对 owner、应用 session 与当前 epoch；服务端不因为 REST 来源有 Cookie 就跳过控制权校验。收到命令、送入 helper 前、helper 执行前都检查同一 epoch；断线、替换连接和 terminal 使旧 epoch 的排队命令失效。没有连接时只能查询、重新取得 ticket 或按本人权限取消，不能用 REST 悄悄抢回控制权。

例如：REST 的 submit_sms 已到达队列但尚未执行，此时新的 WS controller 取得 epoch=5，旧 epoch=4 的请求必须返回 CONTROLLER_REPLACED，不能触发平台提交。已经 dispatching 的敏感动作可能产生副作用，不假装撤回成功；其结果按命令记录确认或标为 unknown。

取消是独立的会话终止操作，不要求 attached epoch，但必须核对当前应用认证、原会话 owner 和 manage 权限，不能代替 controller 发其他命令。已提交 operation 的取消返回原完成结果。命令查询为无副作用 GET：先授权再返回记录，不校验当前 epoch，也不向执行队列发送 query。

幂等记录先按同一 operation/command_id 查询并比较请求摘要；相同请求返回原结果，改动 payload 返回 COMMAND_ID_CONFLICT。未记录的新请求必须通过当前控制权和步骤版本校验才能 accepted。已经派发的命令不因重连重试执行，持久记录与崩溃处理见第 11.1 节。

### 8.3 WebSocket 鉴权

现有 ApplicationAuthMiddleware 明确跳过非 HTTP scope，RequestPrincipalProvider 依赖 HTTP ContextVar；WS 不能直接 Depends(principal_provider) 后就声称已鉴权。

流程：

1. 用已鉴权 REST 签发 30 秒有效、单次可消费的随机票据。
2. 服务端只存 ticket hash，绑定 login_session_id、application_user_id、application_session_id、owner_token_hash、过期时间。
3. WS 握手校验应用 Cookie 和准确 Origin 白名单。
4. accept 后 5 秒内只允许一条 auth 消息，大小最多 4KiB。认证前不发送账号状态、二维码或画面。
5. 在 manager 锁下原子消费票据，重新核对 Cookie principal.session_id、用户、manage 权限、owner、会话非终态。
6. 分配 controller_epoch，关闭旧 controller，发送完整 snapshot。
7. 每 2 秒重查应用会话和 manage 权限；敏感动作在执行前重新核查。权限变化的普通操作最大撤销窗口为 2 秒，不宣传即时撤销。

第一条消息：

~~~json
{"type":"auth","protocol_version":2,"ticket":"opaque-one-use-ticket"}
~~~

票据不放 URL、日志、localStorage 或 sessionStorage。浏览器 WS 无法使用原有自定义 X-Login-Token header，因此不能以“加同样 header”代替票据设计。Ticket 绑定 owner 后，WS 不需要再发送 owner 原文。

Origin 从部署显式配置读取，不信任任意 Host 或未经验证的 X-Forwarded-*。required 模式缺少 Cookie/Origin 拒绝；本地 disabled 模式仍检查 Origin、票据及 owner，用 LocalPrincipalProvider 明确身份。

用户退出、应用账号切换和认证清理事件主动关闭前端 WS，清图片、输入及待发命令。后台认证撤销后封禁输入，撤销所有 ticket，终止当前手势并取消登录。前端重新登录应用后不能复用旧 application_session_id 票据。

controller_epoch 由 manager 分配并在 disconnect、replace 或 terminal 时失效，不由客户端生成。发给 helper 的新 epoch 授权与排队清理有执行屏障：先阻断旧 epoch 的未执行命令并 abort 手势，确认 helper 已接受新 epoch 后才显示 attached。浏览器已开始执行的动作无法回溯撤销，敏感结果按第 11.1 节记录。REST/WS 两条通道共用客户端的递增 sequence，重复重试保持原 sequence；后台只保证已 accepted 序列的执行顺序，不为永未送达的序号无限等待。

### 8.4 消息协议

服务端 JSON 消息：snapshot、state_changed、command_ack、command_result、notice、error、ping。状态消息可合并为最新 snapshot；可能产生平台副作用的低频命令结果按第 11.1 节持久保存并可查询，不因帧背压丢失。

客户端 JSON 消息：auth、action、pointer_batch、scroll、key、text、edit_input、abort_gesture、frame_rendered、pong。客户端不能传脚本、任意 URL、任意 selector、文件路径或 CDP method。`text` 保留旧协议兼容；验证画面的新增原位输入使用绑定真实字段的 `edit_input`，不再要求用户使用画面下方的独立输入框。

示例：

~~~json
{
  "type": "pointer_batch",
  "command_id": "cmd-uuid",
  "controller_epoch": 4,
  "challenge_id": "challenge-uuid",
  "area_version": 6,
  "gesture_id": "gesture-uuid",
  "sequence": 18,
  "mode": "draw",
  "points": [
    {"point_sequence": 36, "x": 0.42, "y": 0.31, "dt_ms": 0, "event": "move"},
    {"point_sequence": 37, "x": 0.43, "y": 0.34, "dt_ms": 16, "event": "up"}
  ]
}
~~~

scroll 携带相同 controller_epoch、challenge_id、area_version 和区域内归一化目标坐标；delta_x/delta_y 必须为有限数且各次绝对值≤760 CSS px。后台只在当前允许操作的区域内执行滚轮；实际滚动后终止当前手势、递增 area_version 并推送新映射与新帧。禁止任意 selector 或脚本滚动。滚动没有产生布局或坐标变化时可以保持版本，仍返回执行结果。

键盘仅使用现有受限按键集合和已确认需要的扩展，不开放地址栏、开发工具快捷键。text 限 256 字符且目标为当前可操作区域内的真实输入；不得以任意 selector 聚焦区域外控件。

ack 区分 accepted 与 executed/failed/rejected；accepted 只证明进入队列。平台提交结果可能比 browser action 执行更晚，因此 command_result 包含 `execution_status` 和 `platform_status`（confirmed/pending/unknown）。UI 不把 click 执行完成等同于短信发送或登录成功。

每个 controller 只有一个当前 epoch、递增输入 sequence。REST/WS 收到命令、manager 实际发送到 helper、helper 实际执行前均检查 epoch；图形输入另检查 area_version。旧 epoch 不能借队列延迟继续执行。重复 command_id 返回已有结果，不重复平台副作用。可靠点击或 Enter 在已知发送/提交控件上转为结构化敏感动作；未知面板中的低频点击/Enter 按 manual_action 记执行结果，重启不重放。逐点移动、原始文字和绘图数据不写入命令表。

关闭码约定：4401 应用认证失效、4403 权限/owner 拒绝、4408 认证超时、4409 controller 已替换、4410 会话终止、1009 超长数据；正常完成 1000。前端映射为用户能理解的状态，不原样显示内部码。

### 8.4.1 验证画面的原位输入与延迟处理

仅已授权 owner 的二进制画面元数据包含 `editable_targets`，最多 4 个当前真实可见、启用、可编辑且完整位于验证裁剪区域内的字段。必须检查字段与 iframe/open Shadow 祖先的实际命中层级，被验证码遮挡的主页字段、透明或隐藏字段不纳入。每项包含 `field_id`、相对 clip 的归一化 `rect`、`input_type`、`input_mode`、`max_length`、瞬态 `value`、`focused`、`applied_edit_revision` 及真实光标 `selection_start/selection_end/selection_direction`（不支持选择的原生类型允许 null）。值不超过 256 UTF-16 单元，字段 ID 绑定真实 DOM 节点生命周期；不将任意 selector 或前端传来的字段 ID 当作权限证明。密码等不适合同步已有明文值的字段不纳入这次编辑通道。

前端不新增可见表单，不在投屏上覆盖 input/textarea。用户点击投屏内的原生字段，现有坐标事件仍作用于真实网页；单个离屏键盘/IME relay 接收随后输入，元数据只用于目标命中、焦点及编辑安全。只有用户点击已确认暴露的字段时，才在同一用户手势内聚焦不可见 relay，支持移动键盘；没有输入框的页面不自动选中任何后台字段。`pointer_batch(mode=click, focus_field_id=…)` 执行回执仅在浏览器证实跨 iframe/open Shadow 的焦点链后返回 `focused_field_id`。前端取得焦点回执及带相应 `applied_input_sequence` 的新画面之前，只暂存输入，不向未知目标派发文字。画面中的文字和光标由抖音原网页实际渲染与截图回传，不绘制另一套本地输入框或输入值。

`edit_input` 携带现有命令公共字段、`challenge_id`、`area_version` 及 `field_id/value/client_edit_revision/selection_start/selection_end/selection_direction`。使用绝对值而非每键追加，允许空串删除；选择位置按 UTF-16 校验。后端执行前再次校验 controller、区域、可信 frame、真实节点、可编辑性和焦点，revision 严格递增，使用原生输入事件填写及设置选择。ACK 仅含字段 ID、revision 与执行状态，不带验证码。填写可能触发平台自动提交，因此此命令纳入现有 `manual_action` 的 HMAC 指纹日记，未知结果或进程重启均不重放。

客户端只保留一个执行中的编辑和最新待发绝对值，合并快速改动；输入法组合未结束时不派发中间文字。旧帧不覆盖不可见 relay 中尚未同步的草稿。编辑 ACK 与对应新画面同时确认 revision/sequence 后，才放行一次暂存的验证点击或 Enter；仅“accepted”不能放行。Enter 再绑定真实字段 ID，节点或焦点已变化则拒绝旧提交。平台异步验证是否成功继续由平台步骤与可信身份观察确认，不能以编辑执行成功替代。

输入按序发送并合并待发编辑，保持截图新帧的快速反馈；真实同步持续时给简短等待提示，不额外画文字。超时绑定每条实际发送的命令/新帧等待，不以整个输入过程或输入法选词时长计时。断线、controller/区域/节点变化、未知执行结果会清待发编辑和提交意图，禁止重连后自动发送旧验证码。保持二进制 metadata 8KiB、图片 1MiB 上限，画面捕获与字段状态在短执行锁中保持一致，前后快照变化时丢弃该帧但保留 FPS 节奏和有界失败提示；去重包含焦点和编辑 revision，防止像素未变化造成确认元数据不更新。输入值只留在授权传输与短暂内存中，不写数据库、日志、浏览器存储或持久化命令结果。

回归分开证明：真实嵌套 iframe/open Shadow 字段坐标与焦点、验证码覆盖主页背景字段、无输入的方式选择页、删除/粘贴/Unicode 选择、替换节点拒绝、明文不落盘；浏览器测试证明没有可见叠加框、Canvas 点选后键盘/粘贴/IME 直接进入原生目标、延迟时合并、旧帧不回滚草稿、提交等新帧和断线不重放。真实抖音 SMS 场景由用户人工输入验收，不用模拟通过率代替。

### 8.5 重连

重连初始间隔 0.5s、1s、2s，之后最大 5s，带随机抖动。每次取得新票据，不复用已消费 ticket。

恢复成功后以服务器 snapshot 覆盖旧前端状态，清待发轨迹并可靠抬起鼠标。短信/提交按钮结果未知时查询 command_id。页面 reload 恢复只使用 session_id/operation_id，不保存敏感输入。

WS 尚未恢复时停止新的平台动作，REST 命令也禁用；结果查询和明确取消可继续。命令 GET 返回的是记录结果：dispatching/executed 但缺少可信平台确认时只能是 unknown，不能显示“查询成功，所以短信已发送”。用户只有在平台实际允许并明确选择后才能创建一次新的重试意图。

GET 404 才检查持久 operation；401/403 不触发自动新建登录。服务器重启后的 interrupted 操作需显式 recover 或重新扫码，不自动清原 Profile。

## 9. 子进程协议、背压和媒体显示

### 9.1 控制通道

保留 stdout/stdin NDJSON 用于小状态、动作、执行回执和重置/提交握手，stderr 单独记录脱敏诊断。

所有 stdin 消息统一进入一个有界 writer，包括 reset_ready、save_committed 和普通输入。处理 partial write、EAGAIN、超时及断管，检查实际写入字节数；不能混用 TextIO.write/flush 与原始 os.write。

控制行上限 64KiB，输入 action 上限 16KiB，helper reader 同步调整。不能把多 MB auth_state 放进受此上限约束的 NDJSON。所有消息版本化，带 operation_id；legacy 的原文 reauth_ready 仅在 V1 保留。

低频可靠动作队列初值 32 条，不能静默丢失。高频轨迹队列另按点数、年龄和字节数限制；abort、shutdown、终态和 reset 授权具有保留容量。

### 9.2 二进制 IPC

V2 使用标准库 socketpair(SOCK_STREAM)，通过 pass_fds 传给 helper，不开放额外网络监听端口。适用于本次目标 macOS/Linux；Windows 不能沿用此部署保证，需单独验证或保持 V1。

包格式：

~~~text
IPC: uint32_be(packet_length) + packet

packet:
  magic[4]              = "DLV2"
  protocol_version:u8   = 2
  kind:u8               = 1(frame) | 2(auth_candidate，仅 IPC)
  metadata_length:u16_be
  payload_length:u32_be
  sequence:u32_be
  area_version:u32_be
  capture_mono_ns:u64_be
  metadata_utf8_json[metadata_length]
  payload[payload_length]

固定头长：28 bytes
~~~

metadata 最多 8KiB；frame payload 最多 1MiB；auth_candidate payload 最多 8MiB。长度必须与实际包长度完全一致。帧 metadata 含 codec、图像尺寸、CSS clip_rect、viewport、controller_epoch 和 applied_input_sequence。

Socket 是字节流：需 read_exact、write_all，处理截断、异常 EOF、长度超限。已经部分写出的包必须完整发送或关闭通道，不能中途被另一帧替换。

auth_candidate 为后端专用 JSON storage_state，包含预期操作、身份和接续结果元数据。API 读取并加密，不向 WS 转发 kind=2，不记录原始载荷，清理内存引用。保存阶段先停帧，使大凭据包不阻塞实时交互。

### 9.3 最新帧与事件桥接

单槽策略贯穿以下链路：

~~~text
helper capture → helper media writer → manager reader → WS sender → frontend decoder
~~~

每阶段最多一个在途 frame 和一个可替换待发最新 frame。旧状态/终态后的帧直接丢弃。

reader thread 只更新 latest_frame。使用 wake_pending 标记合并 loop.call_soon_threadsafe 唤醒；不能每帧安排一个无限积压回调。一个 WS sender 顺序发数据，不能每帧 create_task。

控制结果优先于下一帧，但当前在途 WS 包需要完成或因 send timeout 断开。单帧发送超过 2 秒断开连接，触发重连与停捕获，避免慢客户端持续占用内存。

客户端最多一个在途解码和一个最新待解码 frame。通过 Canvas 或 createImageBitmap 绘制，及时关闭 ImageBitmap；不每帧 setState 导致整个弹窗渲染。二维码/状态才进入 React state。JPEG 不再经过“base64→JSON→bytes→HTTP blob→object URL”全链路。

WS 二进制 frame 使用相同 packet 格式但不含 IPC 的外层 length 前缀。图片与 metadata 原子发送，避免状态与图片错配。收到 kind 非 frame 的网络包视为协议错误。

### 9.4 执行恢复与终态

V2 不沿用“见 terminal 就立即 break stdout 并 wait”的旧消费逻辑。需先完成相应终态握手，禁止再接受命令，停止媒体发送，然后等待 helper 正常退出；超时按精确进程组结束并清理。

资源锁覆盖浏览器关闭/重开、数据提交和 helper 退出。父进程异常退出时，子进程继承的账号与全局登录锁防止重复启动；helper 控制 EOF 或心跳缺失触发退出。不能删除锁文件强制解锁。

显式取消、权限失效、会话期限和应用关闭均调用同一个幂等清理入口：封禁输入 → 撤销票据/controller → abort 手势 → 停捕获 → 关闭浏览器并等待写盘 → 关闭 IPC → 终止仍在运行的子进程组 → 释放自身句柄。

数据库提交已经完成时，晚到 cancel 返回/查询成功结果，不能改写为 cancelled。提交临界区是 manager 锁和 SQLite 事务下的线性化边界。


## 10. 账号身份、凭据重置与持久化

### 10.1 启动配置一致性

登录继续通过 `load_account_browser()` 加载 MediaCrawler 的 `tools/cloak_browser.py`，使用同一个 internal account_id 和 Profile root。

启动收据仅在后台记录：应用/爬虫 SHA、解释器与浏览器版本、Profile key hash、fingerprint seed、headless 模式、viewport、实际网络出口配置引用。前端不展示这些实现细节。

保持已有 Profile 的 seed 和版本；重新扫码不创建新 seed、不删注册表。V2 默认无头，系统仅按第 12.3 节的有界安全策略内部选择运行显示方式，不暴露用户模式选择；进行中的验证不从无头重启为有界面。内部显示回退在 Linux 需要 Xvfb，macOS 需要实际可用的图形会话，不能把 Linux 显示脚本直接套用。

采集若配置代理，登录与接续检查必须能读取相同的受控配置；配置来源尚未共用时视为部署验证缺口，不私自新增或更换代理。本次不修复爬虫 HTTP 参数中写死的设备描述；“同 Profile、seed、版本”不能等同于整条采集请求身份完全一致。

### 10.2 身份来源

登录标记只触发 identity_check：

- `LOGIN_STATUS`、`sessionid` 等和 `HasUserLogin` 是观察信号。
- 当前任意打开页 `/user/<id>` 不是可信本人身份来源；作者页必须排除。
- `/user/self` 是入口别名，不是 platform_account_id；现有正则实际上不会匹配短字符串 self，不能误写为现有 self 绑定 bug。

适配器优先读取本次登录原页面已产生且经确认的“本人资料”响应，其次使用已验证属于本人菜单的链接。解析器需要记录来源规则、最小脱敏样本和明确的“当前用户”判据；不在方案里臆造尚未观察到的接口路径和 JSON 字段。

P0 必须补齐该来源。主来源失败时，可在同一 Profile 中打开已确认本人入口并观察响应；最多一次，不搜索其他作者。用户名/昵称和用户手填 ID 不作为身份匹配证明。

身份结果：

| 结果 | 行为 |
| --- | --- |
| 已绑定账号且实际 ID 一致 | 继续 |
| 已绑定账号但实际 ID 不同 | 失败，不覆盖绑定；已有错误身份与本次新扫码的错误身份分别按下述矩阵处理 |
| ID 空、多个候选、来源不可信 | identity_check 显示待核对；超时 `IDENTITY_UNCONFIRMED`，不写 active |
| 新账号得到可信真实 ID | 提交时检查平台内重复绑定 |
| 实际 ID 已绑定到另一采集账号 | `IDENTITY_DUPLICATE`，不导出可用状态 |

身份不匹配的处理矩阵：

| 来源 | 账号记录 | Profile 凭据 |
| --- | --- | --- |
| 默认 check 发现已有 Profile 是另一身份 | 确认不匹配后设置 login_required、保留风险字段，返回 IDENTITY_MISMATCH | 保留已有凭据；等待用户明确重新扫码，不把检查当作清理授权 |
| 本次新扫码得到错误身份 | 设置 login_required、保留风险字段，并记录 cleanup_authorized | 父进程记录并回复 cleanup_ready 后才清新认证；保持导入保护 |
| 身份未知或歧义 | 记录操作未确认；不保存 active | 不因未知结果自动清理 |
| 已明确 reauth 的失败/取消 | 保持 login_required 和操作结果 | 不恢复旧数据库凭据 |

cleanup_authorized/cleanup_ready 使用第 10.3 节相同的父子握手和资源锁原则。子进程不能在父进程更新账号状态前直接 clear_cookies。对应已有不匹配 Profile 也要确认并建立导入保护，使后续采集不能隐式回放旧数据库 Cookie。

本人信息响应的响应内容仅用于解析，禁止把原始资料日志化。公开结果只返回现有账号页面允许展示的身份字段。

### 10.3 凭据重置

V2 不直接复用总会清凭据的旧 login_context。新增“启动当前账号 context”和“明确 reset”的分离路径。

明确 reauth 的顺序：

1. 已取得账号及全局登录租约，创建 operation。
2. 父进程事务记录 reset_authorized，账号认证状态变为 login_required，但保留风险字段。
3. 父进程发送带 operation_id 的 reset_ready。
4. 子进程先保持 `auth-imported` 导入保护，再清确认与认证有关的存储。
5. 返回 reset_done 后开始扫码。

保留静态 HTTP 缓存、fingerprint seed、Profile 注册表和浏览器版本。不删除 Profile 目录。

当前 reset 只涉及 Cookie、localStorage、sessionStorage，不能称为已覆盖全部认证存储。P0 核对 IndexedDB、service worker/cache storage 是否承载认证；仅清理证实需要清理的认证存储。Playwright 1.45.0 的 storage_state 不能被当作完整 Profile 备份；持久 Profile 才是此设计的状态主体。

reauth 取消后不承诺恢复旧登录。导入保护仍保留，账号保持需登录；不恢复旧数据库 Cookie 到已经清理/更新的 Profile。默认 check 的取消则不主动清认证状态。

#### Profile 导入保护的完整路径

导入保护不只属于 reauth。父子进程在应用账号租约内进行 authority 握手，操作记录包含 profile_authority_established：

1. check 已确认 Profile 的本人身份和有效登录：承认当前 Profile 为主体；缺 marker 时在子进程持有 adapter lock 下创建 marker，父进程记录 guard_ready。
2. check 确认未登录并准备新扫码：先将认证状态置 login_required、保留风险并记录意图；创建 marker 且 guard_ready 后才展示二维码/允许产生新凭据。
3. 明确 reauth：按上面的 reset 握手先创建 marker 再清认证。
4. recover：在检查和修复任何保存记录前确认 marker，操作结果重新验证，不凭旧 journal 宣告认证有效。
5. 已确认已有 Profile 身份不匹配：不清旧凭据，但先阻止账号被采集使用，建立 marker，等待明确重新认证。

guard_ready 未确认、权限已撤销或建立 marker 失败时，不进入可保存成功路径。marker 在失败、取消、保存失败和服务重启后仍保留，不自动删除。

历史账号缺 marker 且 Profile 无可信登录、数据库却有备份时，不能把备份和 Profile 任意一方视为已核验主体。第一版返回 PROFILE_MIGRATION_REQUIRED，并在账号锁内置 login_required、保留风险；用户明确重新扫码可继续。需要恢复备份的历史迁移只作为单独受控迁移任务，经身份验证后建立 marker，本次不自动导入。这样避免由后续采集隐式覆盖本次新登录。


### 10.4 成功标准和提交顺序

`success` 必须同时满足：

~~~text
可信平台登录标记
AND 无尚未完成的必需验证步骤
AND 本人身份匹配且唯一
AND Profile 写盘完成
AND 按采集启动配置重开后标记及本人身份一致
AND 账号数据库保存提交完成
~~~

连续两次观察可作为稳定性初值，每次间隔约 500ms；不能用固定等待 3 秒代替实际状态确认。频率和平台请求都遵守已有 gate，不循环额外调用用户资料 API。

提交顺序：

1. 切 finalizing，禁止人工输入。
2. 得到稳定登录和可信本人 ID。
3. 关闭活动页面和 context，等待 Profile flush 完成。
4. 保持应用账号资源锁，按采集配置重开相同 Profile；不执行 reset。
5. 核对真实本人 ID及登录标记，导出最终 storage_state 备份。
6. 关闭检查 context。
7. 子进程发送 save_ready 控制事件及二进制 auth_candidate；父进程检查二者 operation/identity/generation 匹配。
8. 父进程重新验证应用用户授权、账号存在且未停用、绑定 ID 未变化、auth_state_revision 未变及无重复账号。
9. 同一个 SQLite 事务更新账号凭据、revision 和 operation committed。
10. 父进程发送 save_committed，再将前端快照设置为 done/success；helper 收到确认后退出。

storage_state 是加密备份，不覆盖标记为 auth-imported 的最新 Profile。接续检查只读取认证状态与本人信息，不运行采集任务、搜索或消耗任务额度；仍会产生必要的网页登录请求。

只检查已有登录的操作，也走身份及接续验证。没有重新认证且凭据未变化时只更新 last_validated_at，不刷新 auth_state_updated_at，避免把单纯检查当作新登录而重新计算慢速窗口。

### 10.5 保存失败与取消

文件写盘与 SQLite 不能组成全局事务。

- 浏览器写盘或重开失败：不提交账号 active，返回 PROFILE_FLUSH_FAILED/REUSE_CHECK_FAILED。
- Profile 已有效但 DB 失败：返回 SAVE_FAILED，标明登录尚未完成保存；保留新 Profile 和导入保护，operation 指示 recovery_required。
- DB 已 committed 但 save_committed/前端连接丢失：按 operation_id 查询已提交结果，不能重新 reset。
- cancel 先于提交临界区：不提交成功；已 reset 的账号需重新登录。
- commit 已完成后 cancel：成功结果优先，不将 committed 回退为 cancelled。
- 服务意外退出时未知写盘结果：下次在锁内重新核对 Profile，不从 journal 推断平台认证有效。

## 11. 登录记录、账号资格及风险边界

### 11.1 持久记录

在现有账号数据库中新建 `crawler_login_operations` 和 `crawler_login_commands`，不引入外部 Redis 或完整会话恢复服务。operation 记录一次登录/恢复的结果；command 记录可能产生副作用的低频操作，不保存连续图形轨迹。

最小 schema：

~~~sql
CREATE TABLE crawler_login_operations (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  application_session_id TEXT NOT NULL,
  request_key_hash TEXT NOT NULL,
  owner_token_hash TEXT NOT NULL,
  engine TEXT NOT NULL,
  intent TEXT NOT NULL,
  source_operation_id TEXT REFERENCES crawler_login_operations(id),
  recovered_by_operation_id TEXT REFERENCES crawler_login_operations(id),
  expected_platform_account_id TEXT NOT NULL DEFAULT '',
  expected_auth_revision INTEGER NOT NULL DEFAULT 0,
  stage TEXT NOT NULL,
  credentials_reset INTEGER NOT NULL DEFAULT 0,
  profile_authority_established INTEGER NOT NULL DEFAULT 0,
  request_fingerprint TEXT NOT NULL,
  verified_platform_account_id TEXT NOT NULL DEFAULT '',
  result_json TEXT,
  error_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE UNIQUE INDEX idx_login_operation_request
  ON crawler_login_operations(owner_user_id, request_key_hash);
CREATE INDEX idx_login_operation_account
  ON crawler_login_operations(account_id, created_at);
CREATE INDEX idx_login_operation_source
  ON crawler_login_operations(source_operation_id, stage);

CREATE TABLE crawler_login_commands (
  operation_id TEXT NOT NULL REFERENCES crawler_login_operations(id),
  command_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  request_fingerprint TEXT NOT NULL,
  fingerprint_key_id TEXT NOT NULL,
  application_session_id TEXT NOT NULL,
  controller_epoch INTEGER NOT NULL,
  sequence INTEGER NOT NULL,
  execution_status TEXT NOT NULL,
  platform_status TEXT NOT NULL,
  result_json TEXT,
  error_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  dispatched_at TEXT,
  completed_at TEXT,
  PRIMARY KEY (operation_id, command_id)
);
CREATE INDEX idx_login_command_pending
  ON crawler_login_commands(operation_id, execution_status, updated_at);
~~~

stage 枚举：created、checking、reset_authorized、reset_done、cleanup_authorized、profile_ready、committed、failed、cancelled、expired、recovery_required、recovered、superseded。Browser phase 与持久阶段各司其职，不把每一张截图写数据库。

账号表加 `auth_state_revision INTEGER NOT NULL DEFAULT 0`。所有可达抖音 V1/V2 的新认证提交、受控凭据导入/重置、平台绑定 ID变更和认证资格的启用/停用变更都在对应事务中递增同一 revision；同值重复写入、显示名称修改、纯登录检查和风险冷却更新不递增。受保护 V1回退 release 也必须携带该列的兼容迁移和递增规则，不能保留旧写入旁路。operation 在写盘/提交前对比原 expected_revision，明确重置由同一 operation 造成的递增则记录新的 expected_revision，再继续；后续他人的写入仍使恢复源失效。迁移采用现有 _ensure_columns 风格，旧代码可读取新增列。operation 记录不保存手机、验证码、Cookie、QR 内容或完整 Profile 路径。

账号提交、operation committed 和 result_json 必须使用同一 DB connection/transaction。request_fingerprint 保存规范化启动请求的 hash，用于检测相同幂等 key 的账号、意图或运行模式变化。新的 operation store 接口不能在提交中另开连接造成“账号已更新、操作未提交”的双事务问题。

#### 敏感命令的持久幂等与结果不确定

request_sms、submit_sms、refresh_qr、refresh_challenge、switch_to_sms 及未知原始面板可能提交表单的 manual_action 使用命令表；显式 reauth 的重置仍使用 operation 握手。open_manual/close_manual、查询和逐点图形/文字输入不要求持久 command 记录。

command 的 request_fingerprint 对规范化种类和 payload 计算 HMAC；从既有登录态加密密钥派生独立用途密钥，并记录 fingerprint_key_id。不保存手机号/验证码原文，也不保存这些低熵字段的普通无密钥 hash。重复请求需要原 HMAC key；密钥轮换后缺少旧用途 key 时仅允许查询原记录，返回 COMMAND_FINGERPRINT_UNAVAILABLE，不把无法比较当作新命令执行。

execution_status 为 accepted、dispatching、executed、not_executed、failed、unknown；platform_status 为 pending、confirmed、rejected、unknown、not_applicable。关键顺序：

1. 当前授权、epoch 和步骤有效后，事务插入 accepted 记录，主键保证重复 command_id 不重复 accepted。
2. writer 实际发命令前再次核对，事务持久置 dispatching；该写入失败时不向 helper 发送。
3. helper 用内存 command_id 去重，执行后返回 ACK；父进程持久置 executed。平台实际结果另更新 confirmed/rejected，不能以浏览器 click ACK 确认短信发送。
4. accepted 尚未派发就断线/重启/更换 epoch：置 not_executed，不自动恢复派发；用户可创建新的明确意图。
5. dispatching 或 executed 后进程崩溃、超时或回执丢失，且没有可信平台结果：置 unknown，禁止按原 ID重放。此状态既可能尚未发送，也可能已产生副作用。
6. 原 ID相同请求返回持久结果；不同请求摘要返回 COMMAND_ID_CONFLICT。可靠 GET 结果查询先验证归属，不把旧 epoch/版本错误阻断历史结果查询。

平台副作用和本地 SQLite 不存在联合事务。本方案保证本地同一 command_id 不自动重复派发，不声称跨故障“恰好执行一次”。unknown 不根据时间推断为成功或失败；只有平台可观测的可信状态可纠正结果，否则保留未知并提示。新的人工重试需要平台当前允许且用户明确选择，不能在重连或恢复脚本里自动生成新 ID规避去重。

未知验证面板的普通点击/Enter 可以触发平台自己的提交，需按 manual_action 记录低频执行意图；点击命中已识别短信发送/提交按钮时使用相同结构化敏感动作，不走第二条未记录的旁路。图形移动/绘制保持原始人工操作，服务重启不回放。命令记录只存脱敏结果和请求摘要；不凭记录重建含验证码的 payload。

check_or_login 开始前的权限、绑定 ID及 revision 形成预期值；提交时再次比较。身份重复约束复用现有 `_ensure_account_id_available`，防并发创建另一同平台账号。

记录默认保留 7 天；recovery_required 及仍关联其命令的源记录在处理完成前保留。清理先确认不存在活动/恢复依赖，再在同一事务中删除命令和终止操作；不删除锁、Profile 或凭据。过期记录查询返回 OPERATION_EXPIRED，不把消失的记录当作未执行并自动重试。

### 11.2 重启恢复

恢复分为活动会话重连、终止记录查询和 Profile 保存恢复，不能共用一个“有权限就接管”的规则：

| 场景 | 权限和凭证 | 处理 |
| --- | --- | --- |
| 浏览器刷新/短断线，原应用 session 和 owner token 仍在 | 原应用 session、原 owner、当前 manage | 查询 session，重新签发 ticket，取得新 epoch |
| 活动会话仍在，但浏览器 owner 丢失或应用退出重登 | 不授予新窗口接管活动浏览器 | 返回 OWNER_MISMATCH/SESSION_REPLACED；旧应用 session 被撤销时后台取消，否则等待会话清理/期限 |
| 查询已经终止、中断或 committed 的操作/命令 | 当前有效应用 session，principal.id 等于原 owner_user_id，仍有该账号 manage | 允许重新认证后查询；不要求旧应用 session 或已丢失 owner token；不返回二维码或验证码原文 |
| 恢复已无活动 child 的 recovery_required | 上述原用户条件、新 owner token、新 Idempotency-Key、source_operation_id | 获取锁后创建独立新恢复 operation；重新核对 Profile，不能接管旧活动浏览器 |

管理员有公共账号 manage 权限也不能恢复另一原操作用户的记录；账号管理授权与原操作归属必须同时满足。local disabled 模式用明确本地用户 ID，仍检查锁和活动 owner。

可恢复记录 GET 列表仅返回当前用户有 manage 权限的同账号记录 ID、stage、错误、更新时间及恢复能力。active 记录不能通过此列表转化成可接管记录；保存 key 丢失的用户可借此发现源操作，不查询其他用户历史。新应用 session 在新的恢复 operation 中绑定，旧 ticket 和旧 epoch 永不复用。

API 启动时扫描本环境未终止 operation：

1. 不依据记录年龄删除锁；先核对资源租约。
2. 原 child 仍持锁时不启动第二浏览器，显示 ACCOUNT_BUSY。
3. 无活动进程但阶段未 committed：标记 recovery_required。
4. credentials_reset=1 时保留 login_required 和导入保护。
5. 未 reset 的 check 失败，不自动将原认证改为 expired。
6. accepted 尚未派发的命令置 not_executed；dispatching/executed 但未确认平台结果的命令置 unknown；原始动作一律不重放。
7. 用户发 recover 后，按下述关联、锁和版本规则重开 Profile，重复身份及接续检查，再提交。
8. 对 committed/recovered 操作仅查询原结果或关联完成结果；若需要确认现在是否有效，另发 check，不重放认证。

创建恢复 operation 的规则：

1. source_operation_id 必须指向同账号、同原用户且 stage=recovery_required 的记录。committed/recovered 返回原已完成结果；cancelled/expired/failed 不能隐式恢复，只有已明确标为 recovery_required 的保存失败可进入。
2. 同时取得全局登录和账号租约；有活 child 返回 ACCOUNT_BUSY。事务再次核对来源和当前账号绑定 ID、auth_state_revision。
3. 若来源期望 revision/绑定已被后续登录或管理操作改变，源记录置 superseded，返回 RECOVERY_SOURCE_SUPERSEDED，不覆盖较新账号状态。
4. 新恢复操作带 source_operation_id，复制原凭据来源及预期身份/版本，绑定当前应用 session/new owner；不修改旧记录的原用户和 owner。来源已有活动恢复 child 时只对相同新 owner 复用，否则返回占用。
5. 按原 Profile 完成本人身份、关闭重开及保存验证；不清认证存储，不导入旧 DB备份，不再次发送短信。
6. 同一事务提交账号、新操作 committed/result_json，并将源记录置 recovered/recovered_by_operation_id。保存 ACK丢失时任何已授权查询均能得到同一最终结果。
7. 恢复 child 失败/取消时，保留来源 recovery_required 及明确最新错误；若产生新的权限/版本冲突则不允许继续恢复。保留风险字段，下一次仍必须显式发起。

当前 browser 会话不承诺跨 API 重启无缝存活。网页刷新/短断线与服务器重启是不同恢复场景。

### 11.3 风险冷却的明确行为差异

当前 `save_auth_state()` 会清 `cooldown_until`、`failure_kind`、`last_error`，已有 `test_successful_login_clears_previous_cooldown` 专门锁定此行为。现有会话超时调用 `mark_expired()` 也会清 cooldown。

本次设计修订确定“抖音登录不解冻”为所有可达登录入口的共同契约，属于明确的旧行为修订，当前代码尚未实现。保护不依赖 V2 开关、协议能力或客户端版本；采集冻结、期限及切换规则不变：

- 新增 `save_douyin_login_auth_state` 及风险保留的认证失效方法，抖音 V1/V2 manager 成功、失败和超时处理共同调用；不改小红书、快手的原保存语义。
- 抖音保存成功更新凭据、身份、认证状态和认证时间；保留 cooldown_until、risk_count、last_risk_at 及风险原因。
- failure_kind='auth' 且没有其他风险原因时，可在已确认登录成功后清认证错误。
- 属于 verify/rate_limit 等风险原因时保留 failure_kind 与风险 last_error。
- 抖音 V1/V2 的会话超时、网络错误、取消不调用会清冷却的旧 mark_expired。
- 确认原认证失效时，抖音专用方法设置 expired，但保留风险冷却和风险原因。
- 抖音登录错误优先存在 operation/session，不覆盖账号已有风险错误。
- 可降级的 V1 必须带以上保护；直接调用通用旧存储方法的历史单元测试可保留，但不能作为抖音入口继续解冻的发布契约。增加 V1/V2 及旧客户端降级的所有终态回归。
- V2关闭仍使用受保护 V1；不支持保护的原始后端不能与新版发布混用。发布/回退预检检查服务构建收据和入口风险保护测试，客户端不能指定 raw_legacy 绕过。

available_accounts 当前已要求 active、非空凭据和冷却到期；无需改变采集调度即可让“登录成功但冷却中”的账号继续不可选。

界面冷却字段优先于 active 标签：显示“已登录 · 冷却中”，不能因为 active 而显示“可用”。compatibility 浏览器模式和受保护 V1 都执行共同风险保存契约，不借切模式解除冷却。

## 12. 错误、超时和兼容策略

### 12.1 错误分类

| code | 说明 | 允许恢复 |
| --- | --- | --- |
| `ACCOUNT_BUSY` | 账号或全局登录资源占用 | 等待，不抢占 |
| `AUTH_REQUIRED` / `OWNER_MISMATCH` / `SESSION_REPLACED` | 应用认证、权限或活动会话 owner 失败 | 不接管活动浏览器；终止记录按原用户重新授权查询/恢复 |
| `LOGIN_ENTRY_UNAVAILABLE` | 当前入口无法取得面板 | 准备阶段一次回退；之后显式重试 |
| `QR_EXPIRED` | 平台二维码失效 | 刷新原二维码 |
| `SMS_REJECTED` / `SMS_RESULT_UNKNOWN` | 平台拒绝或发送结果未知 | 已拒绝可人工重试；未知先查询 |
| `COMMAND_ID_CONFLICT` / `COMMAND_FINGERPRINT_UNAVAILABLE` | 原命令请求被更改或无法核对摘要 | 拒绝再次执行，仅查询已授权原结果 |
| `STALE_AREA` / `CONTROLLER_REPLACED` | 目标或控制权变化 | 新帧/新控制权后重做操作 |
| `INPUT_BACKPRESSURE` | 轨迹滞后、队列满 | 中断手势并提示重试 |
| `MANUAL_STEP_UNSUPPORTED` | 无法定位或显示当前原生步骤 | 同页面有界扩大区域；仍失败则明确提示，按 12.3 边界恢复或通用重试 |
| `IDENTITY_MISMATCH` / `IDENTITY_UNCONFIRMED` / `IDENTITY_DUPLICATE` | 本人身份检查失败 | 不保存 active |
| `PROFILE_MIGRATION_REQUIRED` | 历史缺 marker，Profile 与备份尚无可信主体 | 明确重新扫码或单独受控迁移 |
| `RECOVERY_SOURCE_SUPERSEDED` / `OPERATION_EXPIRED` | 源操作已被新版本取代或已过记录保留期 | 不覆盖新登录、不重放旧命令；必要时显式重新开始 |
| `PROFILE_FLUSH_FAILED` / `REUSE_CHECK_FAILED` | 文件持久化或接续失败 | 原 Profile 恢复检查 |
| `SAVE_FAILED` / `SAVE_RESULT_UNKNOWN` | DB 保存失败或结果未确认 | 按 operation 查/恢复，不重新 reset |
| `SESSION_EXPIRED` | 登录总期限到达 | 结束会话；不等价账号认证失效 |
| `RUNTIME_UNAVAILABLE` | 解释器、CDP、显示或协议不满足 | 修复部署环境 |

错误提示引用实际步骤，避免所有失败都显示“重新获取二维码”。后台保留异常类型与关联 ID，前端不暴露路径、命令或内部浏览器配置。准备、身份核对、保存及画面恢复都有对应等待界限；达到界限时明确当前影响与下一步。正常等待扫码/手机操作不能被短的页面加载超时中断。结果未知先查询，不能自动重发短信或把保存失败变成重新扫码。产品中不展示普通“已连接”，连接可用性仅用于内部控制和异常时的操作说明。

### 12.2 超时初值

| 项目 | 初值 | 行为 |
| --- | --- | --- |
| 登录会话总期限 | 600s，允许配置 180–1800s | 会话结束，不自动解除风险 |
| WS auth | 5s | 未认证关闭 |
| ticket | 30s | 单次消费，过期重新签发 |
| 页面导航 | 30s | 有界回退/提示 |
| 面板观察 | 8s | 尚未出现不等于凭据失效 |
| 本人身份核对 | 15s | 显式未确认，不保存 |
| 单个浏览器动作 | 3s | 执行失败回执；副作用未知需查询 |
| Profile close / storage export | 各 15s | 保存失败进入恢复 |
| 重开验证 | 总 30s，包含导航和观察 | 未确认不伪造失效原因 |
| DB save 握手 | 10s | 先查 operation committed 状态 |
| WS 单包 send | 2s | 断开并停连续捕获 |
| 输入轨迹最大滞后 | 250ms | 可靠抬起鼠标并重试 |

配置参数是初始开发值，允许依据验收调整；平台验证码有效期和重发限制来自平台，不使用这些本地超时替代。

### 12.3 内部兼容性与自动恢复

default 优先无头。运行显示方式是系统内部策略，产品界面禁止“兼容模式”“有头/无头浏览器”及对应选择按钮。用户只看到准备登录、当前必要步骤、明确失败与适用的“重新尝试/重新扫码”。

仅在第一次二维码尚未发出、没有可信扫码信号、未派发短信/提交且没有人工验证进度的准备阶段，系统可对已验证的运行兼容性故障执行一次自动回退。必须保持同账号绑定、指纹 seed、浏览器版本和授权约束，确认原子进程清理完成并取得账号锁；不能与旧浏览器并行。是否可复用 operation 按实际事务边界处理；新 operation 只能发生在该准备阶段，并保留来源及失败原因供诊断。入口 home/self 的既有一次回退也适用上述边界，禁止两种回退叠加成循环重启。

二维码已经发给用户、已扫描、短信派发或图形操作开始后，不自动切入口、清理认证、重载、新建 operation 或重发任何命令。只有运行时确实支持同页面、同认证进度、无副作用重放的显示切换，才可无损继续；不能假设重新启动有界面浏览器是同页面切换。没有该能力时先呈现同页面可操作区域，仍无法完成则明确失败。用户通过通用动作重新开始前，说明“这会结束当前登录，需要重新扫码”；由系统选择经验证的运行方式，用户不选择引擎。

Linux 的内部显示回退依赖 Xvfb 及既有 X11 环境，macOS 依赖本地显示能力；能力缺失或一次回退失败即停止并记录部署错误，不循环启动。认证/权限失败、账号占用、身份不符、保存结果未知和风险状态不是兼容性失败，不得触发自动回退、解冻或接管。

未知验证码首先呈现原验证区域，不因为未分类就重启。平台尚未自然触发的验证类型保留真实验收缺口；本地 fixture 通过不等于真实扫码及全类型兼容验收。


## 13. 性能测量与可观测性

### 13.1 测量分层

所有测量记录 source SHA、解释器、Playwright/CloakBrowser/Chromium 版本、OS、部署模式、入口、减载策略和网络条件。使用脱敏 run_id；不记录验证码或图片原文。

| 指标 | 定义 |
| --- | --- |
| `time_to_qr_ms` | 用户启动意图到前端绘制首个有效二维码；既有登录 check 另计 |
| `prepare_browser_ms` | 子进程启动到浏览器 context 可用 |
| `entry_ready_ms` | 导航开始到适配器确认首个可操作步骤 |
| `platform_download_bytes` | 平台侧各目标累计 encodedDataLength；覆盖弹窗/iframe，未覆盖请求标明 |
| `cache_hit_count`、`script_download_bytes` | 静态缓存命中及脚本传输量 |
| `ui_transport_bytes` | 二维码、状态和验证画面的前端实际接收字节数 |
| `capture_ms` | 局部捕获单次耗时 |
| `ipc_ms` | 子进程捕获完成到 manager 可用 |
| `command_execution_ms` | helper 收到到浏览器动作执行完毕 |
| `input_to_render_ms` | 客户端输入到带相同 epoch/area 和 applied_input_sequence 的相关帧完成显示 |
| `effective_fps` | 连续操作期间真正显示的新帧数/活动时长 |
| `input_queue_age_ms`、`ws_buffered_bytes` | 输入与传输背压 |
| `profile_flush_ms`、`reuse_check_ms`、`db_commit_ms` | 保存阶段各段耗时 |

各进程用 monotonic 测量本进程耗时；服务器和浏览器墙钟不保证同步，不能直接相减计算单向网络延迟。客户端 `frame_rendered` 回执只包含帧与已执行 sequence 和本地耗时，用于同客户端 input→render 统计。

带 applied_input_sequence 的帧证明与动作相关，不必然证明平台异步提交已成功。真实短信/验证结果通过平台状态另行确认。本地 fixture 可用可见状态变化作为确切反馈；真实平台记录“相关帧”和“结果确认”两个延迟。

### 13.2 基线和对照方法

1. 旧入口/旧链路、V2 home、V2 self 分别测量，记录顺序和样本数。
2. 分开空临时 Profile 冷启动与已有静态缓存预热启动。
3. 对照相同 browser 版本、机器、出口、时间窗口和等待状态；改变一个变量。
4. 前端等待扫码固定 60 秒，比较 UI 传输量；扫码用户用时不混入 time_to_qr。
5. 匿名性能采样只请求登录页面，不发短信、不触发人工解题。
6.真实账号实验避免频繁强制重新认证；首次扫码和已有登录检查分别统计。
7. 对实际触发的验证码统计输入反馈，不把未出现类型补成零耗时。
8. 每组写入 samples.json 与 summary.json，保留失败样本和覆盖范围。P95 小样本注明样本数，不能描述为稳定生产分位数。

CDP 整幅 screencast 的 50.6 FPS 是本地能力测试，不是 V2 用户体验承诺；合成页面 17KB/288KB 也不能直接当生产带宽节省比例。

### 13.3 初始发布门槛

测试网络为 RTT≤100ms、可用带宽≥10Mbps，另增加 200ms RTT、丢包及带宽受限场景验证恢复。

| 项目 | 门槛 |
| --- | --- |
| 等待扫码 | 零连续整页/验证帧；仅二维码变化和状态/心跳 |
| 静态缓存 | 预热重开同入口的同资源集保留命中，不能因全局 route 再次全部下载 |
| UI 等待传输量 | 同样 60s 窗口比旧完整截图链路至少降低 90%；实际数值入收据 |
| 输入本地反馈 | 短信输入无网络阻塞，UI 按下一帧显示；发送/提交结果另等平台 |
| 正常网络 input→render | 人工连续场景 P95≤350ms，至少 100 次输入样本 |
| 连续图形操作 | 目标 10–15 FPS；实际显示≥10 FPS，不能靠重复帧计数 |
| 手势正确性 | 不丢 down/up，不跨题目映射，不静默丢绘图点 |
| Profile 保存 | 关闭重开认证与本人身份一致，DB 成功后才报告完成 |
| 清理 | 会话结束后无本操作遗留浏览器/显示进程、IPC 和锁占用 |
| 风险资格 | V2 成功、失败、取消、超时均不意外解除已有风险冷却 |

time_to_qr 的绝对门槛需由目标 Linux 的 baseline 决定。本轮候选入口数据未证明冷启动降低多少，不提前承诺“首屏两秒”或平台侧下载固定降低比例。V2 首二维码 P95 至少不能劣于同条件旧版；无法达成时先定位入口/缓存/网络，再决定发布。

指标存于后台收据及日志，不把技术参数和风险实现暴露到登录产品页面。

## 14. 验收矩阵与测试策略

### 14.1 单元和协议测试

| 模块 | 必测行为 |
| --- | --- |
| 状态机 | 二次验证前/后/连续发生；二维码直接成功；终态拒绝晚帧 |
| QR 适配器 | data/blob/元素捕获；过期；刷新未变化；source 变化 |
| SMS 识别 | 普通扫码面板有短信字段不进入 sms_required；真实阻断才切换 |
| 身份 | 本人可信来源；作者页误识别拒绝；self 等别名排除；空/歧义 ID 不保存 |
| 输入 | NaN/越界/超长、REST/WS 共同旧 epoch 拒绝、旧 area、重复 command、排序 |
| draw | 批次顺序、跨批次时间、过载 abort、可靠 mouse up |
| IPC | 包长、半包、短写、EAGAIN、EOF、auth 包不进入网络媒体 |
| 线程桥接 | 慢 sender、wake 合并、单槽、终态清理、无 controller 停捕获 |
| ticket | 单次、过期、跨用户、跨应用 session、owner 错误、错误 Origin |
| WS 授权 | HTTP middleware 不覆盖的路径有独立认证；停用/过期/权限撤销 |
| 操作记录 | 启动幂等、source 恢复关联、DB/源记录/new operation/result_json 同事务、版本冲突拒绝 |
| 命令记录 | accepted 前持久化、dispatching 前持久化、同 ID摘要冲突、已派发未知结果不重放、密钥不可用只查询 |
| 风险字段 | 抖音 V1/V2 成功/失败/取消/超时及旧客户端降级保留；其他平台和通用历史存储测试保持 |

现有 `tests/test_crawler_account_store.py::test_successful_login_clears_previous_cooldown` 仅作为通用旧方法的历史断言保留，新增抖音专用保存及所有真实入口的风险保护测试。开发收据明确列出旧抖音入口语义已修订，不能以“该旧测试通过”证明旧客户端路径风险安全。

已发现 `tests/test_crawler_browser_integration.py::LoginCompletionTest::test_success_is_emitted_after_profile_is_closed` 的模拟 Page 缺少 on()，前轮整组结果为 9 通过、1 失败。开发时补模拟对象以匹配现有接口；不能把这次失败表述为已经验证生产登录失败，也不能忽略后宣称全组通过。

### 14.2 本地浏览器集成

使用临时 Profile、本地测试站点和业务实际 Python/Playwright，不需要真实抖音账号：

- 验证正常扫码模拟、短信表单、点选、滑块及绘图面板。
- 验证 iframe 跨域坐标、多层 iframe、popup、受限 scroll、页面滚动后的版本变化、布局变化和 CSS 缩放。
- 验证 synthetic 图形通过只证明协议/输入，不证明真实风控接受。
- 验证 check 不 reset；check 已有身份不匹配不清凭据但阻止采集；本次新扫码错误身份需 cleanup 握手；reauth 取消不回放旧凭据。
- 验证首次扫码、已有缺 marker Profile、recover、保存失败的导入保护及 PROFILE_MIGRATION_REQUIRED 分支。
- 验证首个 context 关闭后重开保留 Cookie/localStorage，隔离账号不能读取。
- 如 IndexedDB 被证实参与认证，增加真实持久 Profile 的存储用例。
- 父进程崩溃、child EOF、保存 ACK 丢失、SQLite 故障、退出与提交竞态。
- 保留资源锁直到重开与提交完成；第二登录/采集不能并发进入同 Profile。
- 权限撤销后已排队命令也拒绝执行。
- REST submit_sms 已 accepted 尚未派发时切换 controller，旧 epoch 请求必须 not_executed；WS未恢复时 REST 不能旁路执行。
- 敏感命令在 accepted、dispatching 前后、浏览器已执行但 ACK前、平台确认后分别注入崩溃；unknown 状态不能自动发短信/提交。
- 短信被图形验证阻断后返回短信；验证失败再来一题；扫码后手机号验证；unknown 重新识别。验证返回时不重复 pending_command_id 的原动作。
- 丢失 owner token、应用退出重登、公共账号管理员变更、另一原用户请求恢复、来源已有活 child、新旧 auth revision 冲突；不允许未授权活动会话接管。
- 源操作创建恢复 child、child提交失败/取消、源与 child原子提交、ACK丢失和重复 recover；原始敏感命令保持不重放。
- V2中断后用同绑定账号完成受保护 V1新登录，或修改绑定/启用停用；revision 推进，旧 recover拒绝覆盖。自身 reset递增更新本operation的预期值，纯名称/冷却更新不错误阻断恢复。

### 14.3 前端与端到端

- 二维码卡片仅状态变化更新；图片无模糊、留白正确。
- 普通路径只要求扫码和手机确认，真实步骤变化能呈现；无法观察时有明确有界失败和适用出口，不留无画面、无动作的空白等待。
- 产品默认不展示“已连接”、兼容模式或 Profile/接续/数据库内部阶段；检查、完成与异常文案以根 DESIGN.md 为准。
- 无验证码时没有人工区域；普通短信字段存在不抢占扫码。
- 短信粘贴、删除、中文输入法事件和错误恢复。
- 桌面鼠标、触屏、pointercancel、失焦、横竖屏及窄屏。
- 关闭明确取消；页面刷新恢复；身份切换关闭 WS 和清输入。
- 200ms RTT 时不出现 40 条旧轨迹积压；过载反馈明确。
- WS 票据过期、旧连接替换、代理断线、重连事件不重复副作用。
- API 404/401/403 分别处理；不能因为认证失败自动重建登录。
- operation committed 但 UI 断线时能查询完成结果。
- 成功但 cooling_down 显示准确，不误导“立即可用”。
- 成功展示已核对抖音账号；没有真实昵称/头像时使用已核对 ID，不虚构数据。
- 准备阶段一次内部兼容回退有独立回归；首 QR 发出、可信扫码、短信派发、图形进度后禁止自动新 operation/reset；保存及副作用 unknown 只查询不重放。
- 必需未知验证自动呈现同页面可操作区域，首帧失败有反馈；正常扫码等待不会被短加载时限打断。
- 旧客户端没有 supported_protocols、V2开关关闭、compatibility 模式和正式回退构建均保持冻结；端到端核对实际数据库风险字段，而不只看标签。
- 本人重新认证应用后能发现自己的 recovery_required，不要求旧 owner；他人记录不泄露，活动会话仍不可接管。

实施校验至少运行现有 frontend typecheck/build 及对应 browser QA，测试用生产构建也经过实际反代。

### 14.4 真实平台覆盖记录

| 项目 | 发布要求 | 当前证据 |
| --- | --- | --- |
| 正常真实扫码 | 必须通过 | 新 V2 未验收 |
| home 入口保存接续 | 必须通过 | 新 V2 未验收 |
| self 默认入口 | 设置默认前必须真实通过 | 仅匿名面板/二维码已验证 |
| 已绑定账号身份核对 | 必须通过 | 新可信解析源待补 |
| Profile 关闭重开及 DB 保存 | 必须通过 | 临时模拟站点已验证基础能力 |
| 实际短信二次验证 | 自然触发时验；如未触发明确列缺口 | 尚未实测 |
| 实际滑块/点选/绘图 | 按自然触发类型验，不要求同账号全出现 | 尚未实测 |
| 未知原生步骤兜底 | 有完整人工入口；已出现类型必须通过 | 平台样本待积累 |
| Linux 的 WS 全链路性能 | 正式替换前必验 | 仅 macOS 本地捕获数据 |

验收表分别填写“本地模拟通过”“真实平台通过”“未触发”“失败”。未触发二次验证不阻塞普通扫码的隔离灰度；也不能把“未触发”宣传为已经兼容。真实普通扫码、身份及保存链路必须独立验证；遇到真实不可完成的步骤，保留可操作原区域或说明通用重试，不让用户选择技术兼容方式，也不直接覆盖所有用户。

## 15. 开发任务与交付顺序

### P0：锁定平台适配事实和基线

交付：

- 冷/热 home 与 self 匿名基线、面板/iframe 结构脱敏样本。
- 确认本人身份来源和解析判据；不能以任意 user URL 顶替。
- 已有认证检查、重置所需存储、真实扫码后 Profile 重开证据。
- 在业务 Playwright 1.45.0 下验证 CDP guard、clip capture 和 socketpair。

通过标准：关键来源有实测记录；self 未通过完整登录时仍以 home 作为发布默认。本任务不要求账号人为触发每种验证码。

### P1：协议、记录与持久化边界

交付：

- protocol schemas、status/phase 映射、命令结果和二进制格式。
- operation/command 表、auth_state_revision 迁移与原子保存；敏感命令持久摘要及崩溃未知结果规则。
- V2 check/reset/recover、源操作关联与重新授权；抖音 V1/V2 共用风险保留方法及受保护回退基线。
- reset/save 握手、精确清理、崩溃恢复。

通过标准：幂等、短写、半包、提交/取消竞态、原身份拒绝及风险字段测试通过。

### P2：扫码和页面减载

交付：

- 新入口策略及有界回退。
- CDP Document guard、静态缓存保留、精准资源策略。
- QR 提取、有效状态、刷新、平台步骤观察。
- 普通扫码结束与持久化验证链路。

通过标准：普通扫码无连续帧，已有 check 不清凭据，重开后认证/本人一致。

### P3：人工交互与连接

交付：

- 票据和 WS 显式鉴权。
- REST/WS 共同控制权、执行屏障、无副作用持久命令查询；所有敏感动作与 manual_action 统一去重。
- latest-frame 全链路及统一 stdin writer。
- SMS 表单、challenge 定位、unknown 人工面板。
- pointer/轨迹、键盘、执行 ACK、背压和重连。

通过标准：本地全类型协议通过；实际出现的步骤能处理；最慢客户端不拖住全局状态。

### P4：正式前端、运行配置和验收

交付：

- V2 弹窗、当前步骤与有界等待反馈、可识别账号结果、状态恢复、冷却展示及内部兼容恢复；不得发布技术模式选择。
- Vite及正式反代 WS 配置。
- runtime 设置白名单、单 API 进程预检、能力收据。
- 配套回退 manifest 的转换/白名单验证、受保护 V1 风险契约与反代回退启动演练。
- 真实扫码、账号接续、端到端性能报告及覆盖缺口。

通过标准：第 13、14 节门槛及关键真实验收通过，build/typecheck/目标测试通过。文档中的开发阶段名称不是已完成报告。

### P5：隔离灰度与正式切换

交付：

- 配置开关、已确认默认入口、切换/回退记录。
- 验证当前 runtime 新参数实际生效，而不是只改仓库 .env。
- 用户体验与失败分支观察，保留可用回退方式。

通过标准：无新身份错误、凭据丢失或风险资格越权变化；已出现的真实分支完整可用。V1 移除不在此轮，待实际覆盖足够后另行决定。

## 16. 配置、部署和回退

### 16.1 配置初稿

以下为拟新增变量，不是当前已支持的配置：

| 配置 | 初始值 | 约束 |
| --- | --- | --- |
| `CRAWLER_LOGIN_V2_ENABLED` | false | 灰度开启；不覆盖非抖音 |
| `CRAWLER_LOGIN_V2_ENTRY` | home | self 完整验收后再切 |
| `CRAWLER_LOGIN_V2_BROWSER_MODE` | headless | compatibility 由系统能力决定 |
| `CRAWLER_LOGIN_V2_TIMEOUT_SECONDS` | 600 | 180–1800 |
| `CRAWLER_LOGIN_V2_ACTIVE_FPS` | 15 | 5–20，受背压自适应 |
| `CRAWLER_LOGIN_V2_IDLE_FPS` | 2 | 仅人工面板；二维码阶段无连续帧 |
| `CRAWLER_LOGIN_V2_JPEG_QUALITY` | 70 | 50–90；QR 使用原始图/PNG |
| `CRAWLER_LOGIN_V2_RESOURCE_POLICY` | conservative | precise 仅验收后开启 |
| `CRAWLER_LOGIN_V2_ORIGINS` | 显式部署 origin | required 模式不允许空或 * |
| `CRAWLER_LOGIN_V2_OPERATION_RETENTION_DAYS` | 7 | recovery_required 不自动清 |

仅暴露有实际用途且需要部署调节的变量；packet 上限、鉴权超时、ticket TTL 等协议约束集中在 protocol 常量，不把每个内部常量变成环境变量。“抖音登录不解冻”是共同契约而非 V2环境开关，不提供 preserve/legacy 清冷却切换参数。

保留 MEDIACRAWLER_DIR、CRAWLER_LOGIN_PYTHON、CRAWLER_BROWSER_PROFILE_ROOT、账号注册表/浏览器版本和共享 request gate 作为同源配置。

scripts/audit_runtime.py 当前 SETTING_KEYS 不包含新变量，必须同步扩展；.env.example、runtime 文档和预检一起更新。前端只获得能力配置，不获得秘密、Profile 路径和指纹参数。

### 16.2 预检

- Python/Playwright/CDP 包版本和协议方法能使用。
- 配套浏览器适配器路径正确，Profile root 及锁目录一致。
- 当前 API 单进程/单副本；不启用自动 reload 影响活动会话。
- socketpair/pass_fds 可用；POSIX 文件锁支持目标 filesystem。
- V2 Origin 明确配置；应用 Cookie/CSRF 正常。
- 正式 WSS 反代能完成 101 upgrade、长连接和取消；不缓存 ticket/snapshot。
- compatibility Linux 依赖 Xvfb、中文字体和既有 X11 能力。
- 同一环境 API/worker 读取同源 Profile 配置；不共用异环境活动 SQLite/Profile。

Vite /api 配置加 ws: true。生产反代需明确 Upgrade、Connection 和长连接超时，不沿用只用 urllib 转发 HTTP 的历史 frontend.py。实际采用哪个反代，以目标 runtime 已确认的部署拓扑为准；本方案不运行归档控制器改生产。

### 16.3 升级

1. 保存应用/爬虫完整 SHA、依赖与构建收据。
2. 在隔离环境完成迁移和目标测试。
3. 等活动登录正常结束；禁止中途切 engine 或部署新协议给旧 child。
4. 部署 API、前端和 runtime 配置，V2 初始关闭。
5. 验证能力、鉴权、代理及账号库后开启限定灰度。
6. 先 home，self 完整验收后再切默认。
7. 只使用已有已授权账号进行真实扫码，不跑采集任务作为登录验收。

迁移只新增 operation/command 表和 revision 列，不批量改现有账号状态或 Profile。所有现有账号保留内部 ID、绑定 ID、seed、版本及登录目录。上线前必须已经产出下节所述受保护回退 release 和配套配置，不能等上线出问题后再准备。

### 16.4 回退

回退目标固定为“受保护 V1 release”：保留原 V1 登录 UI/协议，并已回补抖音成功、失败、取消、超时风险保护。未经回补的原始旧 SHA 不列为可用回退目标。V2 compatibility 是运行显示模式切换，继续执行共同风险契约。

上线前保存 release receipt：新/回退应用 SHA、爬虫 SHA、Node/Python/CloakBrowser/Playwright 版本、前端 build receipt、源和目标 runtime manifest 摘要、目标 SETTING_KEYS 及反代配置引用。runtime.env 或 secrets.env 内容不进入收据。

配套回退配置在隔离环境预生成，不能把 V2 manifest 原样交给旧控制器：

1. 从当前生效 manifest 复制到独立 rollback manifest；设置 repository/commit 指向受保护 V1 release，前端构建引用也匹配该 release。
2. 显式移除下表的所有 V2-only settings；即使 `CRAWLER_LOGIN_V2_ENABLED=false` 仍属未知 key，也要移除。
3. 保留环境 ID、runtime_root、账号/认证数据库、Profile root、crawler_repository/crawler_commit、受控解释器、锁目录、秘密文件引用、端口和已有模型设置；爬虫版本仅在已验证配套需求下改变。
4. 将剩余 settings 与回退控制器实际 SETTING_KEYS 做集合比较；任何不在白名单的非 V2字段拒绝生成。不得直接丢弃全部未知配置。
5. 目标源校验、配置解析和启动预检通过；秘密文件仍使用原引用，不复制原文密钥到报告。

| 必须移除的 V2-only settings |
| --- |
| `CRAWLER_LOGIN_V2_ENABLED` |
| `CRAWLER_LOGIN_V2_ENTRY` |
| `CRAWLER_LOGIN_V2_BROWSER_MODE` |
| `CRAWLER_LOGIN_V2_TIMEOUT_SECONDS` |
| `CRAWLER_LOGIN_V2_ACTIVE_FPS` |
| `CRAWLER_LOGIN_V2_IDLE_FPS` |
| `CRAWLER_LOGIN_V2_JPEG_QUALITY` |
| `CRAWLER_LOGIN_V2_RESOURCE_POLICY` |
| `CRAWLER_LOGIN_V2_ORIGINS` |
| `CRAWLER_LOGIN_V2_OPERATION_RETENTION_DAYS` |

实际回退步骤：停止接收新登录 → 等待或按原会话安全取消 V2 → 撤销 ticket/controller，确认 helper、显示组件、IPC及全局租约已收尾 → 同时切换 API、前端、runtime manifest 和必要反代配置 → 预检及启动 → 健康、应用鉴权及受保护 V1真实登录入口冒烟。

账号数据库和 Profile不回滚；新增表/列及已提交的新登录态保留。V2的 recovery_required/命令 unknown 记录保留但不由 V1处理；显示恢复功能暂不可用，重新上线 V2再经原用户授权恢复。V1仍可人工明确重新扫码，不自动回放 V2命令或旧 Cookie。

回退演练必验：含 V2配置的原 manifest 应被旧白名单拒绝；转换后可以启动；非 V2配置和数据路径完全保留；已有Profile可继续读取；旧客户端扫码及超时不清风险字段；V1重新扫码推进 auth_state_revision，使旧 V2恢复源返回 RECOVERY_SOURCE_SUPERSEDED；V2的中断记录、unknown 命令和新数据库列不导致回退 release 崩溃。没有演练收据不允许正式切换。

## 17. 故障预演与发布阻断项

| 故障 | 用户影响 | 防护/验证 |
| --- | --- | --- |
| 扫码面板结构改版 | 无法取二维码或字段误分类 | 选择器集中，unknown 面板，状态来源版本化 |
| self 扫码后身份来源不同 | 错误绑定或核对失败 | self 默认发布门禁；不能以页面作者 URL 判断 |
| 减载误阻验证资源 | 验证一直加载 | conservative 默认；收据记录阻断类型；有界切策略 |
| 缓存不经意再次关闭 | 首屏反复下载脚本 | 缓存对照测试和 script_bytes 监测 |
| 媒体/回调无限积压 | 卡顿、API 内存升高 | 每层单槽、合并唤醒、send timeout |
| 绘图移动被合并 | 图形验证失败 | draw/unknown 保序，过载 abort 可见 |
| 断线重放发送短信 | 重复发送或次数耗尽 | command_id、平台结果查询，不自动重放 |
| 旧 controller 输入迟到 | 操作落到新题目 | 三层 epoch/area 校验及 watchdog |
| 浏览器写盘但 DB 未保存 | 新 Profile 与旧账号记录不一致 | operation 恢复检查；禁止旧状态回放 |
| cancel 与 commit 竞争 | 显示取消但已保存，或反之 | SQLite/manager 临界区，committed 结果唯一 |
| API 新增 WS 无认证 | 会话被他人控制 | 专用 ticket/cookie/Origin/owner 权限测试 |
| 成功或超时意外清冷却 | 风险账号重新参与采集 | V2 独立保存/失效方法，四类终态回归 |
| 服务重启但孤儿 child 存活 | 同 Profile 被二次打开 | FD 继承租约、EOF/心跳清理、无删除锁操作 |

以下任一未解决，阻止正式替换：真实扫码与身份检查失败、Profile 重开不一致、鉴权绕过、重复短信副作用、保存结果误报、风险冷却意外改变、精确清理失败。

图形验证类型未自然触发不是“验证通过”，也不要求为了覆盖反复诱发账号风险。灰度记录缺口并保留同页面人工验证与适用重试；真实出现而无法完成的步骤必须先修复，不能以用户自行切换兼容模式代替。

## 18. 完成定义与评审清单

本次文档完成不代表功能开发完成。实施完成时需提交：

1. 文件改动、接口 schemas、迁移与版本收据。
2. 普通真实扫码、正确本人身份、Profile 重开和 DB 保存证据。
3. 短信/图形分支的真实与模拟覆盖表。
4. 目标 Linux 的冷/热加载量、UI 传输量和端到端反馈数据。
5. 故障、幂等、鉴权、背压、取消/提交和风险字段回归结果。
6. 反代/配置实际生效证据及明确回退基线。

重点评审决定：

- 认可默认 check_or_login 不破坏旧凭据；明确 reauth 取消不恢复旧认证。
- 认可“身份未知不能成功”，并要求先补可信本人信息来源。
- 认可所有可达抖音 V1/V2 登录保存/超时保留风险冷却，明确修订旧抖音解冻行为；其他平台保持。
- 认可第一版保持全局单活动登录及单 API 进程。
- self 入口、精确资源减载和 compatibility 默认值按真实验收确定，不仅凭匿名调研决定。
- 性能目标按生产端到端证据验收；不以本地截图耗时替代。

### 18.1 六项审查问题的设计修复映射

| 原问题 | 修订位置 | 需要实现和验证的契约 |
| --- | --- | --- |
| REST控制权旁路 | 8.2–8.5、14.2 | 两通道共享epoch/版本/队列；旧窗口未执行命令失效，查询/取消分开 |
| 敏感命令无持久结果 | 11.1、14.1–14.3 | command 表、HMAC请求摘要、派发前记录、unknown 不重播、本地/平台结果分开 |
| V1降级可能解冻 | 8.1、11.3、16.4 | 所有抖音入口共同风险保存规则，受保护V1基线及禁用开关测试 |
| recover权限和来源未定义 | 5.1、8.1–8.2、11.2、14.2 | source关联、新operation、新会话授权；仅原用户恢复、不接管活动窗口、共同revision防旧源覆盖 |
| 验证状态转移缺失 | 5.3、14.1–14.2 | 扫码后短信、图形后返短信、未知步骤重新识别；pending动作不重复发送 |
| 回退配置不完整 | 15.P4、16.3–16.4、14.3 | 固定受保护V1、精确移除V2字段、保留数据/秘密引用、白名单及实际启动演练 |

“设计修复”表示已消除本轮文档定义缺口，仍需按表开发并提供测试证据。本人身份来源、真实二次验证及目标Linux性能继续按原计划实测，不因文档修订而标为通过。

## 附录 A：关键代码证据

路径相对于应用仓库；行号为本方案基线参考，开发后应以符号定位。

| 文件 | 关键符号/位置 |
| --- | --- |
| [登录管理器](../backend/audit_agent/crawler_login_manager.py) | start/_start、_check_owner、send_input、_expire_if_needed、_handle_event |
| [账号存储](../backend/audit_agent/crawler_account_store.py) | save_auth_state、mark_expired、available_accounts |
| [共享浏览器绑定](../backend/audit_agent/crawler_browser.py) | account_profile_directory、account_browser_env、load_account_browser |
| [本人身份提取](../backend/audit_agent/crawler_account_identity.py) | extract_platform_account_id、candidate_is_personal_link |
| [现有扫码脚本](../scripts/crawler_account_login.py) | first_valid_qr、identify_logged_in_account、login_context、request_login_reset |
| [现有交互脚本](../scripts/crawler_interactive_login.py) | frames、inputs、has_auth、headless_state、interactive_flow |
| [页面清理](../scripts/crawler_login_pages.py) | prepare_headed_login、close_rendered_pages |
| [HTTP 鉴权](../backend/application_auth/middleware.py) | 非 HTTP scope 跳过 |
| [应用身份认证](../backend/application_auth/store.py) | authenticate_session |
| [现有前端交互](../Audit_assistant/src/features/crawler-accounts/CrawlerLoginInteractiveView.tsx) | poll、send、pointer 处理 |
| [旧冷却行为测试](../tests/test_crawler_account_store.py) | test_successful_login_clears_previous_cooldown |
| [接续测试](../tests/test_crawler_browser_integration.py) | RealLoginCrawlProfileTest，临时模拟站点，非真实抖音验收 |
| [统一运行入口](../scripts/audit_runtime.py) | SETTING_KEYS、environment、uvicorn 单进程 |
| [调研原始收据](evidence/douyin-login-research/20261009/research.json) | 完整基线、采样范围、数值和未验证项 |

配套爬虫事实来自其同版本 `tools/cloak_browser.py` 的 Profile/seed/版本绑定及 `media_platform/douyin/core.py::_launch_browser_context`。实施不直接把上游 CDP 默认配置替换到本地 CloakBrowser 入口。
