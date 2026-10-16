# Design

## Source of truth

本文件保留两个独立界面的设计契约：采集账号与抖音登录，以及调查草稿地域编辑。两者沿用现有工作台风格，各自的组件与验收要求按下面的范围适用。各节中的早期调研、旧提交与未决项保留为历史记录，不能覆盖后续用户明确修正。

2026-10-16 的登录修正以原输入框内实际 CaretPosition 光标投影为准；禁止额外可见输入框或焦点蓝框。地域编辑继续遵循草稿冻结、历史与资源隔离的契约。代码与隔离/真实验收结果另见对应证据，不以本设计文档代替验证。

## 采集账号与抖音登录

### Source of truth

- Status: Active；日期：2026-10-10，Asia/Shanghai。
- 范围：`Audit_assistant` 的 `/crawler-accounts` 账号列表及抖音登录弹窗；不重新设计全站，也不接入采集中触发的风控验证。
- 本文件记录本次用户纠正后的产品契约。它是实现与验收要求，不能作为功能已完成、真实平台验证通过的证据。
- 已检查：[技术方案](docs/douyin-login-redesign-technical-plan-20261009.md)、[登录弹窗](Audit_assistant/src/features/crawler-accounts/CrawlerAccountLoginDialog.tsx)、[V2 登录视图](Audit_assistant/src/features/crawler-accounts/CrawlerLoginV2View.tsx)、[账号页](Audit_assistant/src/features/crawler-accounts/CrawlerAccountsPage.tsx)、[登录协议类型](Audit_assistant/src/services/crawlerLoginV2.ts)、[前端测试](Audit_assistant/tests/crawlerLoginV2.spec.ts)、[样式](Audit_assistant/src/styles/crawler-accounts.css)、[tokens](Audit_assistant/src/styles/tokens.css)、[全局样式](Audit_assistant/src/styles/global.css)、[Button](Audit_assistant/src/components/common/Button.tsx)、[package.json](Audit_assistant/package.json)。此前没有根 `DESIGN.md`。
- 用户截图直接证明：弹窗出现大块空白、“正在确认登录步骤 / 已连接”及要求用户选择兼容模式的按钮。用户报告扫码后无反应；仅凭截图不能判断真实扫码响应、身份接口、浏览器或传输中的哪个环节失败。

### Brand

沿用现有运营后台的克制、清晰、可信风格。信任来自可辨识的账号、真实步骤和可靠结果；少用安全装饰或技术解释。禁止用“已连接”暗示登录成功，禁止让用户选择无头/有头/兼容模式。不会为本功能引入品牌、字体、图标库或设计系统。

### Product goals

让用户用抖音完成一次必要的登录，只在平台要求时介入验证，并明确知道是否成功、登录了谁、账号是否暂时不能使用。减少等待不确定性和重复输入；保持现有账号绑定、登录保存与风险限制。

非目标：验证码自动求解、降低或绕过平台风控、解除采集冷却、把账号详情页变成浏览器控制台。二维码轻传输不是终点；真实扫码后能完成登录才是首要成功信号。

### Personas and jobs

- 账号操作人员：将自己有权使用的抖音账号登录到系统，通常只需打开抖音扫码、手机确认，然后完成。
- 被平台要求验证的人员：理解当前题目，在同一弹窗输入验证码或操作图形，不判断验证码内部类型。
- 等待或遇错的人员：知道现在应操作还是等待；必要时执行一次清楚的重试，避免误触导致重复短信或丢失进度。

用户关注平台、目标账号、当前必要动作、等待原因和最终结果。内部浏览器身份、连接票据、运行方式、控制版本及存储阶段由系统负责。

### Information architecture

账号列表保留当前导航和管理动作。点击登录后原地打开一个有标题的弹窗；弹窗只有账号上下文、当前步骤、对应操作和结果。与当前登录无关的全局历史报告错误不得占据或覆盖登录区域；其原因和修复归属需单独核查。

内容顺序：平台和目标账号 → 当前步骤标题 → 二维码/表单/验证画面/结果 → 必要说明 → 主操作及关闭。没有实际验证时不预展示短信、图形或浏览器画面。只显示当前平台允许的选择。

### Design principles

1. 用户只完成平台要求的动作，系统承担技术恢复。
2. 一次只解释当前动作，不陈列未来可能出现的全部验证。
3. 信息真实性优先：没有可信扫码信号，不宣称“已扫码”；凭 Cookie 或连接成功不能宣称登录完成。
4. 快慢都要有反馈：短等待明确正在做什么，超过界限给出真实错误及可执行出口。
5. 保护已完成进度优先于自动重开；短信结果不明时不得用重试制造重复副作用。
6. 结果说明账号身份；冷却或停用只在影响使用时展示，不把登录成功写成采集已可用。

### Visual language

- 颜色：使用现有 `--color-primary`、`--color-text-primary`、`--color-text-secondary`、`--color-border`、surface 和 success/warning/danger tokens；状态不能只靠颜色。
- 字体：沿用全局系统中文字体。弹窗标题约 17px、步骤约 16px、正文 14px、次要说明 12–13px；文字紧邻对应二维码、字段和操作。
- 布局：二维码与表单是紧凑的任务卡；准备阶段不保留整页投屏大小的空白。验证才根据实际内容扩大弹窗。使用现有 6/8/12px 圆角和 8/12/16/24px 间距节奏。
- 图标与动效：使用已有 lucide 图标。等待可用小型 spinner，禁止无证据的百分比或假步骤进度。状态切换轻量、无持续布局抖动；减少动态偏好下移除装饰动画。

### Components

复用 `CrawlerAccountLoginDialog`、`CrawlerLoginV2View`、`CrawlerLoginChallengeCanvas`、`Button` 和 `IconButton`，不另造弹窗或按钮体系。账号列表使用原页面组件。

弹窗拥有关闭/重试/完成及账号上下文；步骤组件拥有当前二维码、短信和验证画面。传输状态在组件内部保留，不默认呈现给用户。断线只在妨碍操作时以“正在恢复登录画面，暂时无法操作”提示。成功组件显示目标名称及已确认的平台账号身份；如果只有可信 ID，标明“已核对抖音账号”及该 ID，不虚构昵称或头像。

用户直接点击投屏中的抖音原生输入框并打字。画面只显示抖音自己的字段、文字和光标；禁止在画面下方、画面内或原字段上叠加任何可见输入框。前端只用一个不可见的键盘/输入法转发通道，输入送到当前已经确认获得焦点的真实字段，截图回传显示平台实际内容。粘贴、删除及中文输入法同样转发；后台按顺序同步并合并待发的最新编辑。点击平台验证按钮或按 Enter 时，先等待最新输入执行及对应新画面，再执行这一次提交意图；连接、题目或字段变化则停止旧输入，不自动重放验证码。只有真实网络同步持续超过短暂等待时才显示“正在同步输入，请稍候…”，不能把用户输入法选词和思考算成超时。

### Accessibility

目标 WCAG 2.2 AA。沿用有标题的 `role=dialog`、`aria-modal`，实现并验证焦点进入、弹窗内循环、关闭后回到触发按钮。所有文本输入有 label；状态用礼貌的 live region，错误与字段关联，避免每帧或每秒打断屏幕阅读器。

主操作键盘可达，焦点可见；不要为了变灰削弱正文对比度。触控目标至少 44px。图形题保留平台指令并支持已有键盘控制入口；不得声称绘图验证码本身具有完整键盘可访问性，若平台提供其他验证方式，保留该选择。尚未运行检查不得写成可访问性已通过。

### Responsive behavior

2026-10-16 用户后续确认的光标契约：保留平台原输入框，删除额外蓝色焦点框和可见输入覆盖层。Camoufox/Firefox 原生截图不绘制 caret；实现使用与实际折叠选区匹配的原生 CaretPosition 几何，在当前同帧画面内投影细光标。只传几何与颜色，不新增验证码或字段值载荷；失焦、旧帧、旧控制 epoch、编辑未确认或几何不可靠时隐藏。`native_caret=False` 保持真实；不将投影声称为原生像素、原生笔画宽度或闪烁完全一致。

沿用现有 1120/720/540px 断点。桌面普通扫码保持约 440px 任务弹窗，验证按内容扩大；720px 以下采用原底部弹窗，限制高度到可视区域、正文可滚动，关闭与关键操作可达。二维码保持完整清晰及安静留白，不裁剪；短信按钮在窄屏必要时堆叠。图形区域保持比例并同步坐标，不以 CSS 裁掉题目或提交按钮。验证至少覆盖 390px 和 1280px 视口、短屏及触屏取消手势。

### Interaction states

| 状态 | 用户看到 | 用户需要做什么 |
| --- | --- | --- |
| 准备/检查已有登录 | “正在准备登录”或“正在检查登录状态”及短等待说明 | 等待或取消；不选择引擎 |
| 二维码可用 | “打开抖音扫一扫”及“扫码后请在手机上确认登录” | 扫码；仅平台存在真实可用刷新控件时才可刷新 |
| 有可信扫码但等待确认 | “请在手机上确认登录” | 操作手机；不重复扫码 |
| 当前 QR 的官方确认已到达，原网页仍在完成认证 | “正在完成登录，请稍候” | 系统保留原页面完成回调；不要求重扫，也不提前宣布成功 |
| 二维码仍在、暂无可信扫码信号 | 保留可用二维码及扫码说明 | 不伪造已扫码；可使用系统实际支持的“查看登录页面” |
| 平台要求短信 | “完成短信验证”及实际字段、脱敏号码 | 仅人工发送/填写/提交；等待时显示实际倒计时或处理结果 |
| 平台要求图形或无法分类的可操作步骤 | “完成安全验证”及原平台说明、局部画面 | 直接在这里操作；不空白等待也不判断技术类型 |
| 核对及保存 | “正在完成登录，请稍候” | 等待；不暴露 Profile/reuse/db_commit |
| 短暂断线 | “正在恢复登录画面，暂时无法操作” | 系统恢复当前步骤，用户不用重复提交 |
| 持续失败/等待到达边界 | 当前步骤无法完成的明确描述及适用操作 | 能原地恢复则重试；必须重新扫码则说明进度会结束 |
| 保存未确认 | “正在确认登录结果”或“登录结果暂未确认” | 查询/检查已有登录信息，不强制重扫或重发短信 |
| 成功 | “登录成功”、已核对账号、完成按钮 | 确认账号正确并完成；必要时看到冷却/停用限制 |
| 取消/到期 | “本次登录已结束”及原因 | 明确重新开始；不会解冻账号 |

等待界限分离：平台二维码期限、平台短信重发限制、登录总期限、加载/传输/身份核对超时。未知二维码有效期不编造倒计时。用户正常思考或等待手机不能被短的加载超时打断。

### Content voice

短、具体、面向动作。默认只说明“在哪里做什么”；错误说明当前影响及下一步。允许：“打开抖音扫一扫”“请在手机上确认登录”“正在完成登录，请稍候”“登录成功”“重新尝试”“重新扫码”“恢复登录信息”。

产品界面禁止：“兼容模式”“无头/有头浏览器”“已连接”“epoch”“Profile”“接续检查”“服务器需要支持有界面浏览器”。这些可出现在技术文档和脱敏诊断日志。重新开始需要说明“这会结束当前登录，需要重新扫码”，而不是解释引擎切换。任何安全存储承诺必须与实际数据保护范围一致；不把 SQL 凭据加密泛化为整个浏览器目录加密。

### Implementation constraints

已确认框架为 React 18、TypeScript、Vite 6、React Router；既有 CSS/token 和 Playwright 测试。复用当前服务及类型，禁止加依赖或借本次任务改全站。

平台识别、自动回退、权限/存储不是设计 lane 实现范围；由对应后端负责人落实。前端不能靠假定扫码时长或成功 Cookie 填补后端观察缺口。UI 只消费已验证状态、可用动作与结果；连接可用性仍参与禁用判断但不能作为用户完成状态。

### Accepted user flow

1. 在目标账号点击登录；系统先安全检查已有状态，已有有效身份则直接显示已核对结果。
2. 需要登录时呈现二维码；用户打开抖音扫码并在手机确认。系统持续观察该页面，不要求用户点击“我已扫码”。
3. 只有真实要求验证时切换到短信或原图形画面；连续验证可继续，结束后恢复真实当前步骤。刷新验证不会重发上一条短信。
4. 身份、保存及重开验证全部完成后显示登录成功和已核对账号；信息尚未确认不显示成功。
5. 用户完成返回列表，列表与最终保存结果一致；风险冷却保持原值，登录成功不解除冻结。

### Automatic fallback boundaries

运行兼容性是系统内部策略，不是用户选择项。只在首次二维码尚未发出、没有可信扫码、短信派发或人工验证进度的准备阶段，系统可进行一次经安全条件验证的自动回退；保持账号绑定、指纹 seed、版本约束及操作授权，必须确认原浏览器清理完成并取得账号锁后再启动。缺显示能力或第二次失败直接有界失败，不循环重启。

首个二维码发出后，禁止自主新建 operation、切入口、重载或清理认证来“试一下”；不得丢弃扫码/手机/短信/图形进度。只有运行时确实支持同页面、同认证进度、不重发副作用的显示切换才能继续；否则保留当前可操作页面或显示明确失败。用户如需重新开始，点击通用动作并明确了解重新扫码的后果，仍不承担选择浏览器模式的责任。

认证失败、身份不符、账号占用、保存结果未知及风险状态不是兼容性信号。它们不得触发自动引擎降级、解锁接管或重置。上述约束不扩大既有 V1 发布回滚边界。

### Acceptance criteria

- 产品中不出现技术兼容选择与普通连接成功文案；前端测试不再以技术文案存在作为成功标准。
- 普通路径只要求扫码/手机确认；无验证时不出现短信或图形控件。
- 扫码后：真实手机确认、条件验证或完成结果能在有界平台观察内呈现；检测不到时不得持续停在无二维码、无画面、无下一步的“正在确认登录步骤”。
- 未分类的当前步骤可显示同页面人工画面；首帧加载失败有明确错误和适用重试，不留下空白题目。
- 无真实输入框的验证方式选择页不得生成任何表单；有真实输入框时只操作投屏中的平台原生字段，不新增或叠加输入框。支持键盘、删除、粘贴、中文输入法和移动键盘，截图显示平台实际文字。最新编辑尚未执行及呈现在新画面时不能提交，断线、题目或字段改变后不重放旧输入；被验证码遮挡的后台字段不能成为输入目标。模拟延迟测试和真实验证码输入验收分别记录。
- 一次安全自动回退有回归证据；QR 发出、已扫描、已请求短信或图形进度后不会自动新建/重置；未知副作用不重发。
- 等待阶段显示真实任务文案，达到对应超时有出口；二维码有效期与会话期不混用，短信倒计时能到期且不不断推后。
- QR 响应必须关联当前 ticket 与屏幕二维码：旧响应不能改变新卡片；原生返回替换码时跟随同页面更新，不误把共用 4/5 轮换分支分别当作取消/过期。confirmed 仅表示原生 QR 授权链路推进，认证、本人、保存及重开未通过前不成功。
- 没有原平台真实可见且启用的刷新控件，不展示或派发刷新按钮；动态短链接不能仅凭域名被当成占位二维码。
- 已核对本人身份、保存成功及重开结果是成功前提；结果可识别账号并反映风险资格，完成后列表同步。
- 390/1280px、短屏、键盘/触屏验证、错误/断线/取消/恢复/成功截图与交互检查；typecheck/build 及相关测试通过。
- 必须单独记录真实抖音扫码验收；模拟 SMS/图形/接口 fixture 只能证明对应实现逻辑，不能替代真实扫码完成与自然触发验证证据。

### Evidence and verification

本次设计分析已直接查看两张用户截图及上述源码。修订前测试明确断言“已连接”和“重新尝试兼容模式”，说明此前测试契约本身偏离用户目标；没有因为测试通过就确认真实用户体验可用。前端提交 `a7a3c43` 已移除技术模式按钮和普通连接提示，将保存内部阶段合并为完成登录提示，并展示可信账号 ID；这证明实现已修改，不等于真实扫码闭环已验收。

浏览器修复 `5d76e55` 已确认并处理：二维码仍可见时平台手机确认提示被原 QR 分支遮盖；缺少真实新阶段时等待无明确截止；本人响应来源需限定为真实第一方 self 响应。现有[观察器](scripts/crawler_douyin_login_adapter.py)优先处理明确手机确认信号；[登录执行器](scripts/crawler_douyin_login_v2.py)限制准备、身份核对及首次无进度内部显示回退；[管理器](backend/audit_agent/crawler_login_v2_manager.py)仅记录脱敏观察诊断，`d0f8c80` 覆盖默认日志配置可见性。匿名实际页面仍曾出现二维码图片存在但未提取为 QR 状态的间歇情况，原因未定；不能据此推断手机扫码已失败或已成功。

历史报告截图的根因已由主负责人核实：本地无持久历史和演示归档时，auth disabled 分支仍尝试自动导入演示归档，错误提示又泄露到其他页面。提交 `ecd4804` 将导入改为显式默认关闭的配置，普通请求读取真实持久历史，空列表正常；[历史 API](backend/api/historical_reports.py)、[配置](backend/audit_agent/config.py)和[历史页面](Audit_assistant/src/features/investigation/InvestigationPage.tsx)负责请求与错误作用域，另有[隔离测试](Audit_assistant/tests/historyLoadIsolation.spec.ts)。主负责人刷新真实 runtime 验证 GET 200 且历史为空、账号页不再显示该错误；本 lane 核对源码及提交，未重复实际浏览器验收。

后续真实二维码重试仍失败，扫码、本人身份、保存及重开闭环尚未证明。新取得的[官方公开登录组件](https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/ies/douyin_web/async/21138.497e5294.js)及请求发起栈收据，明确了等待、扫描、确认以及共用轮换分支；API/DOM 图片字节比对确认动态短链接也是实际登录码。技术方案 6.3.1 已据此修正当前 ticket/图片关联、原生确认回调等待和真实刷新能力契约。新发现的原生轮换/SSO 等待缺口仍在修复，不能描述为全部完成。

原次用户手机显示成功但网页未观察到对应 Cookie 的过程尚未完整复原；初始浏览器启动失败的根因仍待安全诊断，不推断为平台限制。新增脱敏阶段/异常类型记录供定位，不把源码协议证据、代码修复或模拟测试写成真实手机成功。对文档修改执行章节、引用和冲突校验；运行/视觉验收归属实现 lane。

2026-10-10 的真实用户扫码已观察到当前 QR 的 scanned 响应，随后进入原平台短信二次验证画面；这证明本次扫码后的实际验证步骤已经呈现，不代表短信通过、可信身份核对或保存成功。随后用户截图直接证明叠加输入框方案违反诉求，且将被验证码页面覆盖的后台字段错误显示在验证方式选择页。该方案撤回，改为不可见键盘转发，只展示和操作抖音自己的输入框；须以当前用户截图及真实输入验证重新验收，不沿用覆盖框存在的测试结论。

当前键盘转发改造的源码提交：`d2c5916`（真实字段/焦点/遮挡/光标与瞬态编辑）、`1c726c7`（画面字段不一致时保留帧率并在 8 秒内明确失败）、`dae26a4`（撤回可见覆盖框、直接 Canvas 点选和离屏键盘转发）。前端 typecheck/build 及 53 项相关测试通过；固定爬虫环境 121 项目标测试通过，包含 6 个真实浏览器 fixture；主环境 API/管理器/协议/子进程集成 127 项通过、3 项因缺浏览器环境跳过，这些浏览器行为在固定环境另验。上述证明改造代码与隔离行为，不证明真实短信提交、可信本人身份和登录保存成功。

### Open questions

本次真实验证码提交后的缺陷已定位：18:14:24.914 平台在原账号 Profile 写入新登录 Cookie，约 0.8 秒后本应用因旧 `2046` 和验证区域消失提前失败，尚未执行本人核对和保存。`b27f575` 将区域消失转入既有有界登录接续，保留全部身份、重开及保存门槛。18:32:09—18:32:25 主负责人使用原 Profile 执行 `check_or_login`，真实完成可信账号核对、关闭重开验证及数据库提交；页面显示登录成功，账号已保存且状态可用，指纹 seed/版本摘要及风险限制一致，`credentials_reset=0`，没有重新扫码或重发短信。仓库外收据为 `../login-v2-preview-runtime/login-continuation-result.json`；这关闭本次接续缺陷，仍不代替未自然触发的图形验证、其他部署环境及此前间歇启动失败的验收。

成功页当前展示的是可信 `profile/self` 响应的 `user.sec_uid`（或明确个人主页链接中的相同标识），用作后台账号绑定；当前实现只接入该标识，平台昵称和头像尚未提取并保存。用户询问整串标识说明结果辨认仍有体验缺口；后续需以可信本人数据展示昵称/头像，将完整内部标识收进账号详情，不能用管理员填写的备注冒充平台昵称，也不能声称当前已取得这些展示数据。

- [x] 平台手机确认被 QR 分支遮盖、身份等待缺少界限已核实并修复（`5d76e55`）；Owner：浏览器/运行时负责人。仍不等于复原原次实际扫码全过程。
- [ ] 原次手机显示登录成功但网页未观察到 Cookie 的环节，以及匿名间歇 QR 提取停滞原因仍待复原。Owner：浏览器/运行时负责人；影响：必须用真实扫码及脱敏步骤记录完成闭环验收。
- [ ] 官方 QR ticket/DOM 关联、替换码轮换与 confirmed 后原生回调等待的已确认设计缺口尚在修复。Owner：浏览器/运行时负责人；影响：需独立审查及真实闭环验证，不能因为本地测试通过提前关闭发布门禁。
- [ ] 初始浏览器启动失败实际根因仍未知。Owner：运行时负责人；影响：依安全阶段及异常类型证据定位，不向用户暴露内部堆栈。
- [x] 一次内部显示回退已限定在无 QR/扫码/短信/验证进度、非 recover 的准备阶段；Owner：后端负责人；源码：`DouyinLoginV2.display_fallback`，`5d76e55`。
- [ ] 当前引擎是否支持真实同页面、同进度无损显示切换？Owner：后端负责人；影响：尚未证明，QR 发出后仍禁止自动重启，不假设切换无损。
- [ ] 当前可信身份数据是否提供昵称/头像，还是只有平台 ID？Owner：身份负责人；影响：结果展示只使用真实已核对字段。
- [x] 本机无演示归档导致自动导入失败，错误作用域影响账号页面；Owner：主负责人；`ecd4804` 默认关闭演示自动导入并隔离页面错误，主负责人 runtime 检查真实空列表 GET 200、账号页无该 banner。
- [ ] 真实抖音自然触发二次验证、Linux/反代与平台验证码可访问性尚缺哪些证据？Owner：验收负责人；影响：明确发布范围，不宣称全类型完成。

## 调查草稿地域编辑

### Source of truth
Active, 2026-10-15. Scope: investigation draft geography editor. Evidence: user screenshot, `RelevanceScopeEditor.tsx`, `investigation-workspace.css`, geographic UI regression tests. No existing repository design brief was found.

### Brand
Calm, clear, practical. Reuse the workspace blue and slate palette. Avoid oversized panels, browser-default buttons and decorative effects.

### Product goals
Make the current region easy to identify and a region change easy to complete. Preserve catalog validation, immutable history and resource isolation. Starting collection is outside this change.

### Personas and jobs
An investigator reviewing a draft needs to confirm or correct its province/city without reading implementation details.

### Information architecture
Current region and edit action form the header. Selection policy is secondary. An inline edit panel contains candidates, a manual entry field and save/cancel actions. Readiness information follows the panel.

### Design principles
Show one clear primary action. Distinguish editing a resolved region from resolving ambiguity. Preserve the current value until a successful save. Avoid repeating the same error.

### Visual language
Reuse existing slate text and blue interactive colors. Use 12–14px supporting text, 16px region value, 8/12/16px spacing, 8–10px corners and light borders. Use existing lucide icons, no new assets or dependencies.

### Components
Keep `RelevanceScopeEditor` as the shared suggestion/confirmation editor. Scope styles to this component; keep API and callback contracts unchanged. Use consistent candidate, secondary and primary button variants.

### Accessibility
Retain accessible section and input names. Decorative icons are hidden from assistive technology. Buttons have visible keyboard focus, disabled styling and explicit types. Input guidance is associated with its field; errors retain alert semantics.

### Responsive behavior
Header and candidate groups wrap. Manual entry is capped at a readable width on wide screens. Below 560px, actions move below the full-width input; no horizontal overflow at 390px.

### Interaction states
Collapsed: current value and edit action. Editing: neutral change heading and manual input. Pending: actual clarification question and candidates. Saving: controls locked with visible status. Error: retained input and one visible message. Success: return to current value with updated draft.

### Content voice
Use direct labels. Ordinary changes say “修改调查地域”; use “指哪个调查范围” only for pending resolution. Saving updates the draft; it does not start collection.

### Implementation constraints
React/TypeScript, existing CSS and lucide-react. No backend, prompt or resource changes. Verify geographic interaction tests, typecheck/build, and real browser appearance in collapsed and editing states.

### Open questions
None blocking this bounded UI change. Assumption: the existing blue/slate workspace appearance remains the visual baseline.
