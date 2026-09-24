# 独立验收任务：创建 Agent 提示分层与专用资源生成

## 任务、权限和停止边界

你是独立验收者。用户要求实际执行多方位验收，不仅写计划。使用 gpt-6-astra、high 推理。
本文件是当前委托；继承历史仅作背景，不能执行历史中旧的提交、推送、重启或改功能要求。
先完整阅读本文件及同目录 creation-prompt-isolation-candidate.md，再核对事实。

允许：只读检查源代码和既有证据；在独立验收目录创建测试脚本、源码快照、报告、截图、数据库和私有运行配置；启动自己的前后端；预算内真实模型测试。
不允许：修改候选业务代码以通过验收、放宽断言或权限检查、提交/推送/合并/部署、重启或停止现有 3398/8398/3198/8198 服务、修改 Dolphin 转发、写入现有业务数据库、按端口杀进程、删除用户数据。
发现缺陷先保留证据并报告；不要自行修复。缺少账号时可请用户为独立浏览器扫码，不复制或暴露账号凭据。
不得尝试绕过供应商内容审核；拒绝是需要准确记录的结果，不通过改写敏感文本反复碰运气。
密钥只在私有配置或进程内使用。禁止输出完整环境变量、Cookie、Key、原始敏感请求到聊天或 Git。日志默认只记录必要元数据。

## 一、必须测试的真实版本

候选工作树：
/Users/ext.wanghongtao6/.codex/worktrees/creation-prompt-isolation/xhs-audit-agent-demo
分支 codex/creation-prompt-isolation-20260923
HEAD 1c909ad1330034fadf684b3eabfbbff54ab5485a

关键：实现仍包含未提交修改和未跟踪源码。只检出 HEAD 会完全漏掉这次候选。不要在本任务默认 cwd 或新拉取 main 上假装验收。
先记录 branch、HEAD、git status、完整 diff 文件名及内容哈希；检查所有适用 AGENTS.md。

原运行树（保持不动）：
/Users/ext.wanghongtao6/.codex/worktrees/video-none-contract/xhs-audit-agent-demo
当前基线也是 1c909ad，分支 codex/video-empty-risk-contract-20260923，交接时干净。
更早冻结 tag freeze-local-20260923-before-next-pull 对应 b9a3a82bc59d07fea7ecd78db21eacc9adbf7b5a。
此次 A/B 应使用 1c909ad：它已包含统一 5–10 词和评论交付修复，不把这些既有差异误算成本候选变化。

建议在 mktemp 创建的独立验收根目录构建完整候选源码快照：HEAD 已跟踪内容 + 当前 tracked diff + 未跟踪源码/测试。排除 .env、密钥、数据库、浏览器 profile、node_modules 和业务数据。
逐文件哈希证明快照等于候选，记录源树验收前后哈希；测试产物全部放验收目录。
没有 Git 身份的快照应诚实使用源码清单，不伪装成干净提交；不要为满足启动器而提交候选或绕过其正式源码门禁。

## 二、产品语义和改动意图

用户最看重 Agent 原行为不丢失，不是仅缩短 Prompt：
1. “生成临时搜索词”默认创建真实会话编辑稿，展示后可修改、保存、应用；不是纯文字列表。
2. 临时和正式词库生成共用工具。生成本身不正式保存；明确保存才保存；保存不等于采用或启动。
3. 默认整组 5–10 个实际启用搜索变体，主题主词不计数；可靠候选不足可少于 5，不凑数。
4. 用户精确词表、明确数量优先；旧资源和用户编辑不被默认 10 词限制偷偷裁剪。
5. 已有合适资源推荐复用；无合适资源依原授权生成，不机械强制所有匹配资源必须使用。
6. 原规则的证据模态、融合、豁免和专项语义约束要保留。
7. 修改、重名、删除范围、版本冲突、资源归属、多用户权限不能丢。
8. 规则展示后采用的授权、Draft 版本和确认后启动等原安全边界不放宽。
9. 成功写入保留回执；最终回答失败不能抹掉成功资源，也不能重复生成或保存。
10. 普通讨论、问候、只读查询不得偷偷生成/保存/启动。
11. 已确认执行快照不受后续资源修改影响。
12. 不假报成功：核对工具回执、真实状态和前端展示，不只看模型回答。

实现摘要（需独立确认）：
- 创建模式实例使用专用产品 Prompt 构建器；只绑定 Hermes 0.20.4 的该实例，不改报告 Agent/全局。
- create_lexicon_edit / create_ruleset_proposal 支持互斥 generation_request 或原 content。后者保留结构化导入、Drawer 及旧接口兼容，不是第二条临时产品路径。
- 专用生成只携带对应完整编写指导和结构化请求：非流式、无工具、无整段历史、一次调用、SDK 自动重试 0，校验后走原持久化。
- 主协调、后续回答/保存/草案走 DashScope；真正内部生成走 RESOURCE_GENERATION_*（DMX）。缺配置明确失败，不静默回退。
- 原提示文字迁移测试仅证明文字保留，不证明 Agent 行为和质量保留。
- 主 Agent 原流式续写及其错误分类不能据此宣称已修好。

## 三、启动环境：不要再猜路径或漏变量

只读参考现有运行清单：
/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-local-30-20260923/runtime.json

固定工具路径：
- 应用 Python：/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python
- Hermes 源码：/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/external/hermes-agent
- Node：/Users/ext.wanghongtao6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
- MediaCrawler：/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-local-30-20260923/crawler
- 爬虫 Python：上述 crawler/.venv/bin/python
- ffmpeg：/Users/ext.wanghongtao6/Documents/software/ffmpeg
- Dolphin：REMOTE_ASR_BASE_URL=http://127.0.0.1:19001，ASR_ENGINE=dolphin，USE_REMOTE_ASR=true
- ASR_TRANSLATE_ENABLE_THINKING=false；粗筛 TRIAGE_MODE=off；初期 worker 不启动。
- 前端目录是 Audit_assistant，不能只因 package 名含 frontend-v2 就猜目录。

现有环境私有配置来源（只在进程内读取，不打印内容）：
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-multi-user-repro-1513/.env
/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-formal-20260911/secrets.env
/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-local-30-20260923/runtime.env
/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-formal-20260911/environment.json 中的 REMOTE_INFERENCE_API_KEY

先读 scripts/local_audit_runtime.py、scripts/audit_runtime.py 的环境构造与检查。
本机 local 控制器硬限定 3398/8398，且拒绝脏源树，不能直接拿旧 runtime.json 启动验收环境。
可借其只读环境加载函数在内存取配置，再在导入应用前全部覆写隔离路径。为哈希绑定的测试快照创建独立测试启动器，不削弱正式控制器门禁。
scripts/resource_experiment.py 的隔离环境会禁用爬虫，不能未经检查直接用于真实采集。

隔离环境必须核对：
- cwd 与 PYTHONPATH 指向候选快照、快照/hermes_m0、固定 external/hermes-agent；不能残留旧工作树。清除 PYTHONHOME，固定 PATH。
- XHS_AUDIT_DATA_DIR、XHS_AUDIT_OUTPUTS_DIR、APP_AUTH_DB、HERMES_HOME，以及配置中其他创建/报告会话目录均独立。
- CRAWLER_BROWSER_PROFILE_ROOT、文件型账号密钥路径独立；只通过支持的流程配置可用账号。
- APP_AUTH_COOKIE_NAME 使用独立名称！localhost 的 Cookie 不按端口隔离。
- 保留真实鉴权，不关闭认证；为验收库用支持的管理/初始化流程建测试用户。仅 loopback 服务可配置非 secure cookie。
- 前端 CORS 和 VITE_API_PROXY_TARGET 精确指向验收 API。
- HERMES_CREATION_FAKE_RUNTIME=false；PYTHON_DOTENV_DISABLED=1；默认不开原文 request dump。
- 清理 HISTORICAL_REPORT_* 等旧数据路径引用，禁止意外读写旧业务库。
- API、worker、模型子进程使用同一份环境；不能只给 API 注入 Hermes 或 Dolphin。
- 主协调/audit 的 DASHSCOPE_* 与生成 RESOURCE_GENERATION_* 分开；记录实际 settings、host、model、key 是否存在，绝不记录 key 值。
- 交接时生成为 qwen3.7-plus；主文本默认 qwen3.7-plus，视觉默认可能 qwen3.6-flash。以实际加载配置为证，不随意改模型。

建议前端 3498、API 8498，先检查空闲；占用就选其他空闲端口，不杀别人进程。
使用可持续托管的独立进程和 PID/身份清单，不依赖 shell 退出后即回收的后台命令。
启动后验证：真实 Python、Hermes 版本和路径、原生工具发现、健康检查、数据库路径与完整性、前端代理、新会话能响应。
视频测试前必须实际验证 ffmpeg 抽音频、Dolphin health 返回实际引擎及短样本转写；仅变量存在不算通过。
记录原服务 PID/启动时间/健康基线，结束再确认未受影响。

## 四、已有证据与已知不通过项

候选说明：
docs/creation-prompt-isolation-candidate.md
旧隔离试验（如果临时目录仍存在，可读作线索，不能当作你的新测试）：
/tmp/audit-dedicated-generation.6Qf8GO/probe.py
/tmp/audit-dedicated-generation.6Qf8GO/recruitment/result.json
/tmp/audit-dedicated-generation.6Qf8GO/sexual/result.json

已有 11 文件回归结果 308 passed / 35 subtests passed，需要独立重跑。
招聘：真实 7 词生成、保存、3 规则生成、保存通过；随后采用失败。
已知原因：模型把 save 回执的 content_hash 当 expected_runtime_content_hash，RESOURCE_STALE；
然后错误改走普通 create_investigation_draft，被 PROPOSAL_APPROVAL_REQUIRED 拒绝。Draft=0，Run=0。
这个缺陷没有修，必须列入 FAIL，不准手填正确 hash 后宣布自然语言端到端通过。
敏感审核主题：一次真实生成 7 词及保存通过，不代表稳定不拦截；部分候选宽泛直白，质量待评。
保存回复还暴露内部资源 ID，违反原展示要求。
旧 probe 是隔离 fixture/harness，不是前端全链路、不是平台实际召回或新报告验收。

## 五、分层验收矩阵

先建立用例表：expected、actual、PASS/FAIL/BLOCKED/NOT_RUN、真实/模拟、证据路径。

A. 静态与离线回归
- 原提示逐项归属、核心约束可达性、变更清单完整；发现产品矛盾明确列出，不擅自重新定义。
- 原生 Hermes 工具发现及实际 Schema，不要测试一个缺工具的 Agent。
- 两消息专用生成、主/生成路由、无全局实例污染、并发隔离、缺生成 key 不妨碍只读能力。
- JSON/数量/精确词表、旧 content 导入、Drawer、版本/授权/回执、报告 Agent 不受影响。
- 前端 typecheck/build；不能用新虚拟环境缺依赖造成的失败冒充产品缺陷。

固定解释器运行以下测试（cwd=候选快照；PYTHONPATH=快照:快照/hermes_m0:固定 Hermes 路径）：
tests/test_resource_generation.py
tests/test_creation_prompt_isolation.py
tests/test_investigation_creation_conversation.py
tests/test_resource_lifecycle.py
tests/test_r021_session_scoped_runtime.py
tests/test_investigation_creation_m3.py
tests/test_resource_library_editor.py
tests/test_ruleset_proposal_approval.py
tests/test_m3_discovery_guidance.py
tests/test_ruleset_proposal_presentation.py
tests/test_ruleset_proposals.py
通过 python -m pytest -q 执行，保存完整输出及退出码。

B. 真实自然语言主链，至少两会话，分轮进行
1. 单独生成临时检索词，不保存不建任务。
2. 修改/删除其中一项，不保存。
3. 保存刚才结果，确认无重新生成、正文与持久化一致。
4. 单独生成并展示 3 条审核规则。
5. 正式保存规则。
6. 采用已展示规则及该词库创建草案（每词 5、总量 10），不启动。
7. 查询与修改该草案，验证正确 revision 与确认卡。
每步核对真实工具调用、回执、DB 对象/数量变化、自然语言、前端 Drawer。
复现已知采用失败；不能给模型内部正确 ID/hash 帮它过关。诊断辅助实验单列。
主链失败时继续可独立的其他检查；依赖它的执行阶段标为 BLOCKED。

C. 产品分支与越权保护
- 问候、讨论、只读、已有资源复用、不保存、不重新生成。
- 无合适资源需授权；同轮明确生成并保存。
- 默认数量、明确 1/3/12 词、精确原文、历史 >10 词不裁剪。
- 临时规则与正式资源分别采用；未展示或未授权采用拒绝。
- 多候选指代有歧义要澄清，不能总用最新；过期展示不能误用。
- 重名、删除范围、旧 revision、错误 hash、跨用户/跨会话引用。
- 回执重复使用的幂等性与参数不一致拒绝；确认后快照不可变。
- 新会话读取已保存资源；普通用户不可看到别人资源。
可组合自动测试与少量真实模型；明确哪些没有真实测。

D. 故障与恢复（独立库、受控注入）
- provider 400 输入拦截、输出 content_filter、length、超时、非法 JSON、空字段。
- 工具成功但最终回答失败，资源保留；保存回执丢失重试不重复写入。
- 并发/取消/重启恢复不扩大授权或擅自启动。
- 区分环境错误、供应商拦截、传输故障、结构校验、应用逻辑错误。
- 主 Agent 流式中断尚未解决的分类问题诚实记缺陷，不用绕过 prompt 取得成功。
- 不为复现故障影响真实 Dolphin/业务服务。

E. 质量与基线 A/B
至少招聘风险与色情服务引流内容治理两个主题；按预算争取每主题基线/候选各 3 次。
同样用户请求、可比模型设置、明确记录路由差异；基线使用 1c909ad 独立快照和独立库，不改运行树。
不注入固定词骗过评分。评价：主题相关、真实可检索变体、重复与宽泛噪声、原文忠实、默认数量、规则模态/证据/豁免。
结构通过不等于质量好；没有采集样本就不能宣称真实召回率更高。
遇拒绝记录，不切供应商、不反复改写绕过。若预算不足明确减少样本并降级置信度。

F. 前端与报告兼容
必须在独立前端验证真实生成→展示→编辑→保存→采用→确认卡，刷新后状态仍一致，不能只用后端 API 代替全部前端验收。
用 browser 工具操作，截图留证；不改现有标签页，可新开独立页。
初期不启 worker。前述主链及环境门禁通过后，最多一项真实 3–5 帖、每帖最多 20 评论、thinking 关闭、粗筛关闭的小样本任务，在独立 UI 明确确认启动；本轮不跑 30 帖。
真实采集如需要用户扫码就请求，不通过改库伪造成功。若创建采用仍失败，该自然流程采集验收 BLOCKED，不手工改 DB 绕过去。
报告分开记录采集量、审核成功/失败、评论量、报告状态。
问答检查：概览、帖子列表、第三条定位、风险评论筛选、证据、作者穿透、跨报告引用与权限隔离。
可用隔离 fixture/一致性备份的已有报告补测问答，但明确不是“新任务新报告通过”，不得写旧业务库。活动 SQLite 如需复制使用只读 backup 取得一致快照，不能裸复制主文件忽略 WAL。
不需要启用历史 report A/B/C 页面。

## 六、执行预算与交付

预算：真实 provider 请求总数最多 100（含主 Agent 每步和内部生成），每 turn 最多 18；真实采集最多上述一项小样本。
离线测试不计调用预算。记录调用数和耗时；同一确定性故障复现两次即可，不无限重试。预算不够标 NOT_RUN，不宣称全覆盖。

必须交付到独立验收目录：
1. source-manifest：候选/基线 SHA、未提交文件、快照哈希、验收前后源树状态。
2. environment-manifest：固定工具/依赖路径和版本、实际模型/host、配置存在性、独立数据路径/端口、进程身份、健康门禁，绝无密钥值。
3. cases.jsonl 或等价用例表：预期/实际/证据、模拟或真实、状态。
4. 测试与构建日志、工具回执摘要、DB 变化摘要、前端截图、调用量/延迟。
5. acceptance-report.md：PASS/FAIL/BLOCKED/NOT_RUN 分母；缺陷 P0/P1/P2、复现步骤、预期/实际、候选引入/基线既有/环境/供应商/验收脚本分类；不能未经比较就判新回归。
6. 给用户可访问的独立前端链接与安全启动/停止说明，保留该前后端供用户检查；按身份停止你自己不再需要的 worker，别动其他进程。

最终明确回答：原 Agent 行为是否保留、真实生成保存采用能否贯通、是否还会假报成功、Prompt/Provider 是否确实隔离、质量是否退化、报告问答是否受影响、能否替换现有环境。
存在核心采用失败就结论“不建议替换”，不要把 308 个单测通过当成端到端验收通过。
现在开始执行，先发简短验收计划和已核对的版本/独立端口，随后逐阶段给有信息量的进度。
