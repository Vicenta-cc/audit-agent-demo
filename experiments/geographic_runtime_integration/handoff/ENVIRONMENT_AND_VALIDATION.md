# 克拉玛依地域调查前端全流程验证交接

日期 2026年10月15日。接收人是接续此任务的新 Codex 窗口。用户当前额度不足，要求先交付环境清单和操作 prompt，本轮不进行真实测试。

目标是确认“前端发起调查 → 冻结克拉玛依地域 → 真实采集 → 原风险精选 → 地域复核 → 原正式审核 → 报告”是否可用。先验证功能，不优化精选质量。现有离线测试通过不能替代真实账号与浏览器流程成功，也不能替代 P4 原会话复验。

## 当前结论

新整合代码已经具备地域正式路径的接口与离线实现。最后统一测试218通过、13跳过、0失败；13项是缺少历史真实报告数据库的旧测试。原风险/P4范围另跑284通过。旧联合固定源码清单与新增接口发生不一致的失败仍保留，不能写成所有历史验收全绿。

当前3993和8993是旧P1环境，并非新整合代码。2026年10月15日本轮只读核验：前端 GET /investigation 为200，后端 GET /openapi.json 为200。HTTP200只说明这些入口可响应，不说明账号、模型、地域精采或报告链可用。本轮未打开浏览器发送调查、未调用真实模型、未采集、未迁移账号、未改网络或服务。

## 本轮实测环境清单

| 项目 | 核验结果 | 使用注意 |
|---|---|---|
| 旧前端 | 127.0.0.1:3993，node PID32254；工作目录为P1的Audit_assistant | 可只读查看旧界面，不作为新代码验收 |
| 旧后端 | 127.0.0.1:8993，Python PID49506；工作目录为P1根目录 | 不修改其运行源码；新对话可能拉起新Python进程 |
| 旧中继端口8994 | 本轮无监听 | 用户贴出的早期8994说明已经过时，不据此重启中继 |
| 测试代理端口8996 | 127.0.0.1:8996，mihomo-un PID82828 | 当前启动器HTTPS_PROXY指向它；记录称TAG香港01，本轮未外网探测或验证实际节点 |
| P1数据库 | runtime/geographic-p1-event-place-20261015/data/audit_index.sqlite3 | crawler_accounts数量为0 |
| P1爬虫配置 | MEDIACRAWLER_DIR指向runtime下disabled-crawler | 是禁用采集配置，不能用于真实流程 |
| 历史账号来源 | runtime/independent-retest-20261013/business/data/audit_index.sqlite3 | 只读查询发现1条dy active，last_used_at为2026-10-13T22:48:57；不证明目前登录有效 |
| MediaCrawler候选 | projects/MediaCrawler，HEAD ec56ebfe638dcfc8680f41711215ee9618a843e2 | 工作树有修改，未审查兼容性；禁止reset、覆盖或宣称与HEAD完全一致 |
| 当前新源码 | 470份Python文件逐个与最终测试source-after摘要一致 | 新窗口重新校验；交接提交与接入前基线HEAD不同 |

PID和端口状态会变化，接手时必须重查。

## 绝对路径

```text
新整合代码 W
/Users/ext.wanghongtao6/.codex/worktrees/geographic-integration/xhs-audit-agent-demo

旧 P1 代码
/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p1/xhs-audit-agent-demo

旧 P1 运行目录
/Users/ext.wanghongtao6/Documents/Codex/runtime/geographic-p1-event-place-20261015

旧 P1 数据目录
/Users/ext.wanghongtao6/Documents/Codex/runtime/geographic-p1-event-place-20261015/data

旧 P1 输出目录
/Users/ext.wanghongtao6/Documents/Codex/runtime/geographic-p1-event-place-20261015/outputs

旧启动器
/Users/ext.wanghongtao6/Documents/Codex/runtime/geographic-p1-event-place-20261015/launch_backend.py

旧直连中继脚本
/Users/ext.wanghongtao6/Documents/Codex/runtime/geographic-p1-event-place-20261015/direct_connect.py

现有 Python
/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python

MediaCrawler候选
/Users/ext.wanghongtao6/Documents/Codex/projects/MediaCrawler

账号候选源库
/Users/ext.wanghongtao6/Documents/Codex/runtime/independent-retest-20261013/business/data/audit_index.sqlite3

私有模型配置位置 仅由本地进程读取
/Users/ext.wanghongtao6/Documents/Codex/runtime/independent-retest-20261013/baseline-environment.private.json

实施计划
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/.omx/plans/geographic-relevance-runtime-integration-plan.md
```

不要把 documents 技能路径当成运行数据目录。上面的旧P1实际数据根是data，不是business/data；后者属于历史independent-retest环境。

## Git与验收源码

- W分支：codex/geographic-integration。
- 接入前基线HEAD：9b36fbc2b5b3341bf7eb88bc8495ba08e8b4c363。
- 应用main历史核对值：a4997d3969beab3d499a218e7a7aee025fea46d9。它不是本轮新fetch结果，也不是服务器部署证明。
- 最终P1：031e0cd26379ee44c2372c9a28536c377ab3d4b9。
- 离线检查点：9b20f342dddbf6172596b2b440d3e19559a766a3。
- 固定P3 tag：p3-post-only-names-v2-local-20261015，解析为2120c53379d1b79c45e3cb8232b9c9b96657b183。
- 实际应用仓库历史地址：git@github.com:Vicenta-cc/audit-agent-demo.git。本地origin指向另一个saas-demo仓库，不得直接push origin猜测目的地。

用户后续授权将本轮接入代码保存为本地提交。本交接文件与代码、测试证据保存在同一提交中；以仓库内本文件的最近提交或外部交接目录CODE_CHECKPOINT.json确定版本，不要使用上述接入前基线代替。没有推送或部署。复验对象是交接提交及源码清单；新窗口切换前先检查工作树，不得reset/clean覆盖之后的新修改。

W下必须读取：

```text
experiments/geographic_runtime_integration/REPORT.md
experiments/geographic_runtime_integration/SOURCE_MANIFEST.json
experiments/geographic_runtime_integration/INTERFACE_MAPPING.md
experiments/geographic_runtime_integration/CASE_EVIDENCE.md
experiments/geographic_runtime_integration/RUN_HISTORY.json
experiments/geographic_runtime_integration/artifacts/runtime-final-20261015-01/summary.json
experiments/geographic_runtime_integration/artifacts/runtime-final-20261015-01/source-after.json
experiments/geographic_selection/REPORT.md
```

P4原始验收报告：
`/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p4/xhs-audit-agent-demo/experiments/geographic_selection/ACCEPTANCE-20261015.md`。

旧FRONTEND_READINESS报告保留历史意义，其中“缺少接线代码”的列表已被本轮实施更新，不能据此重复从零实现。以新REPORT和当前摘要为准。

## 模型与额度切换

旧启动器和environment.safe.json记录：对话、资源生成均使用deepseek-v4-pro-ga-260813，接口https://api.qhose.net/v1，fake runtime关闭；dotenv自动加载关闭。私有profile原文件的RESOURCE_GENERATION_MODEL为deepseek-v4-1-flash-260910，但旧启动器将资源模型覆盖为对话模型。因此不能只看私有JSON就认定当前资源生成使用Flash。

旧启动器当前HTTPS_PROXY为http://127.0.0.1:8996，NO_PROXY为127.0.0.1,localhost。旧启动器本轮SHA256为5bcd7152527e4974e398481605c645fe076fb19d6423ba58ab2d7615065571f1。这是磁盘启动配置证据，不是对每个已运行进程环境的完整核验。

“账号额度不足要切换”不要自行等同于采集账号失效：若切换的是Codex登录账号，只需继续读取同一本地交接；若切换模型服务商/API凭据，在新隔离profile内完成，不覆盖旧P1共用profile，不把密钥复制到提示、Git、报告或普通日志。若API/模型发生变化，记录角色对应关系并验证请求格式；仍不能修改评分提示、风险阈值或查询词来迁就结果。

待核验的模型角色：对话、资源生成、初筛风险、地域判断与精采复核、正式文字审核、启用时的图片/音视频处理、报告生成。DeepSeek对话配置不代表其他角色已配置。新启动器应输出不含秘密的角色→模型名→base URL→是否经预算网关→是否经专用出口清单；检查每轮Python子进程是否继承。

现有预算工具位于W的experiments/geographic_runtime_integration/model_gateway.py。默认30次、上限30次，转发前原子扣额，超时/错误/重试不退，重启不清零；18项离线测试通过。当前仅支持/v1/chat/completions，SSE缓冲返回，默认transport忽略环境代理。它尚未接入全部真实角色，不能直接因为设置HTTPS_PROXY就认为网关会走香港。正式冒烟前必须在隔离适配层解决专用出口和全部角色路由，TLS校验保留，不能偷偷有客户端绕过计数。

## 只读核验命令

以下命令不启动模型、采集或服务。端口有正常服务则复用，只读检查不需要重启。

```bash
lsof -nP -iTCP:3993 -iTCP:8993 -iTCP:8994 -iTCP:8996 -sTCP:LISTEN
curl --noproxy '*' --max-time 5 -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3993/investigation
curl --noproxy '*' --max-time 5 -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8993/openapi.json

git -C /Users/ext.wanghongtao6/.codex/worktrees/geographic-integration/xhs-audit-agent-demo status --short
git -C /Users/ext.wanghongtao6/.codex/worktrees/geographic-integration/xhs-audit-agent-demo rev-parse HEAD
```

验证最终测试Python字节仍一致：

```bash
/Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python - <<'PY'
from pathlib import Path
import hashlib, json
root = Path('/Users/ext.wanghongtao6/.codex/worktrees/geographic-integration/xhs-audit-agent-demo')
p = root / 'experiments/geographic_runtime_integration/artifacts/runtime-final-20261015-01/source-after.json'
snapshot = json.loads(p.read_text())
drift = [name for name, expected in snapshot.items()
         if not (root/name).is_file() or hashlib.sha256((root/name).read_bytes()).hexdigest() != expected]
print({'checked':len(snapshot), 'drift':drift})
raise SystemExit(bool(drift))
PY
```

有漂移时保留现场并分析归属，不能直接覆盖他人代码。Python清单不含前端；前端须同时按SOURCE_MANIFEST核验。

## 采集账号与爬虫准备

已知账号候选库中有1条dy active。仅查询了状态/平台/最近使用元数据，没有输出账号凭据或cookie，没有尝试解密、复制或登录验证。接手先用只读SQLite连接检查表结构与非敏感元数据；不要SELECT整行或读取所有环境变量来“排查”。

准备真实测试时核对账号归属和当前登录状态，按既有账号/加解密接口导入新隔离库，确保新库正确使用密钥；不要把密文直接复制到不同密钥下。保留源库及原账号可见性，不把私人账号改成public。没有有效dy账号就记录环境阻塞，不用虚构账号继续。不要顺手扫描无关应用账号库。

MediaCrawler当前有未提交修改。先记录确切HEAD、相关文件摘要及差异，再核对原adapter需要的搜索/detail字段、作者认证信息、评论、错误类型与回执形状。保持原MediaCrawler及原crawler_adapter逻辑。兼容性不满足就明确阻塞，不做假成功响应通过真实测试。

## 新隔离环境准备

这不是现成可执行的启动器；新窗口应在准入条件满足后，基于已验证接口编写独立启动器，而不是把旧P1路径替换一下就运行。

1. 先核对P4风险证据返修原会话明确复验通过，且其依据对应要测试的源码。实施计划R0要求“可先写契约及离线测试；正式测试启动前关闭该项”。本轮未发现该记录，不能用218通过代替。
2. 新环境使用W源码；单独创建临时隔离根、数据库、输出、模型profile、日志和浏览器profile。IsolatedGeographicCapability要求专属临时根，按其实际接口安装；不要用请求参数、删blocker或改全局开关绕过。为了可恢复测试，整个测试期间保留同一根与预算DB，不要每次重启重建预算。
3. 前后端使用新的回环端口。可候选3995前端、8995后端、8997预算网关，但本轮未分配、未检测其空闲；先lsof再确定，不能抢占。更新独立auth/CORS/回调配置。前端必须显式代理新后端并strictPort。
4. 使用现有Python及本地前端依赖，不临时升级依赖。源码如果还需修改，先固定基线并做定向回归；不在正在提供旧P1服务的目录修改。
5. 检查默认生产地域能力仍关闭。只在独立服务显式安装本地测试能力；首版支持dy/search/select。off/compare不暗改为select。
6. 对话、资源、风险、地域、审核、报告及内部重试/子进程均经过同一持久预算门。未覆盖的角色不能放行网络。所有角色配置完成后再最小验证专用出口；不改全局TUN/其他应用节点，不关闭TLS。401只证明无凭据连通，不证明模型有效、额度充足或带密钥请求成功。
7. 新后端启动器完成后记录其路径、SHA256、安全配置、实际端口和PID。不要照抄不存在的R6启动命令或声称已配置。

新前端启动命令模板，仅在独立后端已通过就绪检查后运行；8995/3995须先确认空闲：

```bash
cd /Users/ext.wanghongtao6/.codex/worktrees/geographic-integration/xhs-audit-agent-demo/Audit_assistant
VITE_API_PROXY_TARGET=http://127.0.0.1:8995 node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3995 --strictPort
```

不要运行旧launch_backend.py --seed；不要启动旧direct_connect.py去覆盖当前代理配置。现有P1服务正常时保持运行。

## 克拉玛依大火验证步骤

首轮上限按原计划：最多2个原样查询词，每词最多3候选，总精采最多2条，候选评论最多10，真实模型请求总上限30次并包含失败及重试。配置以resolver实际较小上限为准，不能改引擎或P2默认实现强行凑额度。禁止自动加词、反复重跑直到成功。无适用资源时走现有资源流程，计入总模型上限。

1. 使用新隔离前端、新测试会话输入“调查克拉玛依大火”。如对话需要明确，回答“调查范围为新疆克拉玛依市，先验证流程可用，保留原风险规则”。不要把历史“克拉玛依欠薪”会话当本次证据。
2. 检查草案显示克拉玛依市及正确的行政代码/层级，地域选择策略明确；保存实际解析对象，不只看一句文本。不要要求所有搜索词被改写加上克拉玛依，查询词须保留已确认资源的原样顺序。
3. 检查平台dy、search、select、有效账号、模型、真实/非fake运行以及各项小额上限。地域判定不等同于开启精选，两者必须分别有配置证据。
4. 确认后记录owner/session/draft/revision/run/job/execution、config_hash、P2/P4计划摘要与版本。前端显示、Run冻结、Job和实际请求一致才继续。
5. 观察全词capture先完成，再进入选择精采；保存原始帖子/评论/身份字段的摘要。风险评分仍调用原引擎，P3依固定判定语义，排名与预算按已冻计划执行。
6. 精采后只做地域复核，保留第一次风险结果。合法同档变化可交付；地域变档/失败不送审，成功采集预算仍计入。正式审核读取本次获准原文，不能被旧shared raw替换。
7. 从前端查看筛选状态与地域标签；核对按Run授权的筛选接口、账本、membership、正式审核和报告数量。报告里的地域统计与原风险统计分别正确，不把IP补充标成克拉玛依本地事件。
8. 如有真实合法入选，打开报告，核对冻结来源和本次原文。重启独立服务后，保留原DB/输出/预算DB验证记录和报告恢复；不要重建新会话冒充恢复。
9. 达到额度或账号失败就沿既有终止/恢复规则停止，输出原因；不知道detail是否完成时不要清预算重采。不碰现有P1服务或源账号库。

## 通过标准与旧风险逻辑核验

正向功能通过需要至少1条真实合法入选内容走完正式审核并有可打开报告，且身份、摘要、预算、交付次数能对账。仅省级/IP补充成功，就只报告对应补充分支成功；不能宣布克拉玛依本地精采已实跑。零入选可以验证正确结束及原因展示，但不能证明正向报告链。没有真实合法入选，不降低阈值、不修改固定P3、不塞人工帖子。

原风险语义核验同时做两种检查：

- 静态：triage.py、triage_matcher.py、crawler_adapter.py与最终P1逐字节一致；原pipeline._run_triaged_search AST一致。整个pipeline本轮有必要接入修改，不能说全文件未变。
- 动态：相同原始捕获、冻结规则、原模型响应交给原TriageEngine，比较strength、score、discard理由和排序；用保存响应回放，不额外调用模型。验证原文/评论/认证变化不能套用旧RiskRecord；正常精采同档变化保留原风险结果。本轮已有回归证据，真实阶段需绑定本次输入与源码。

本次不评估精选效果是否优秀，不修改旧风险阈值、评分权重、豁免逻辑、模型提示、查询词或P3判定规则。

## 验证后输出

保留新鲜REPORT、ENVIRONMENT_SAFE、SOURCE_MANIFEST、逐例CASE_EVIDENCE、请求预算账本、浏览器截图和脱敏网络/后端证据、run/job/报告标识、失败/跳过原因、未验证项。安全配置不含API密钥、cookie、密码、账号密文或完整私有profile。

分别给出界面可用、地域冻结正确、select实际开启、真实采集成功、本地或补充分支精采、正式审核、报告可打开、恢复、原风险逻辑保持的结论。某一步未跑就写未验证，不用HTTP200或离线用例替代。交原会话验收，不自行宣布P4全阶段通过、正式链路可上线；本次只保存本地交接提交，后续不自行合并、推送或部署。
