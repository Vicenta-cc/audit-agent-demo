# 自然事件输入补测：调查克拉玛依大火的舆情

日期：2026-10-15。**补测未完成模拟草案配置。保留当前 confirmation 版本，P0未整体通过、待原会话验收。**

用户授权补测原句，要求先不修改当前版本；首次连接SSL失败后，用户追加要求“用绕过tun的方式测试”“用deepseekpro”。本次只在隔离输入和模拟资源内补一例，未修改当前代码、提示、schema、工具集、判分器、4响应/12工具/45秒上限，也不伪造后续用户答复。

## 输入与条件

唯一当前用户原文：**“调查克拉玛依大火的舆情”**。无历史用户指令、无已有草案，不向用户句子补平台、地域策略、资源采用或“只查当地”等要求。沿用原资源齐备状态，但改用本例所需的模拟公共安全规则和“克拉玛依大火”事件词库，搜索投影为完整事件名；可用平台只有dy。资源、平台属于显式模拟应用条件，因此不能把成功读取预置词库当作模型自行理解事件的证据。本例不测资源从无到有的生成，也不测正式采集/帖子筛选/卡片展示。

模型仍为 `deepseek-v4-pro-ga-260813`，provider `https://api.qhose.net/v1`；使用既有私有JSON中CONVERSATION配置，未把密钥写入请求载荷、报告、命令参数或包。预先离线冻结的地域标签为set克拉玛依、local_first_with_supplement，原自动判分保持失败，不事后改成keep来刷分；完整事件主题保留、是否实际落草案另行复核。**事件名的地理归属与用户明确限定讨论地域不完全等价，此标签仅为诊断假设，不足以定义“理解事件”的全部正确答案。**

## 两次尝试分别记录

| 尝试 | 模型请求/响应 | 连接与结果 |
|---|---:|---|
| 原连接路径 | 1/0 | SSLError；没有模型响应或工具调用，语义未评测 |
| 用户追加授权的en0直连 | 4/4 | 四次HTTP200、TLS校验均通过；四轮用完，无配置调用 |

原路径DNS解析到198.18.0.142，路由utun4；无模型请求的额外TLS诊断报“unable to get local issuer certificate”。按用户要求使用en0与TLS校验通过的DoH结果179.255.137.170，保持原HTTPS域名、Host/SNI、模型及请求内容不变；未关闭证书校验，未更改系统代理、路由或TUN全局配置。两次初始模型请求内容哈希一致，第二次只改变传输路径。此前失败保留；直连本次每个请求只发送一次，无自动重试。

## 真实响应逐轮复核

| 轮次 | 模型实际行为 | 工具回执 |
|---|---|---|
| 1 | record_geographic_intent(action=keep) | 成功捕获；没有解释为何keep |
| 2 | query_investigation_options(platform=dy, mode=search) | 返回资源列表 |
| 3 | 再次query，索取词项及规则详情 | 模拟fixture仍只返回相同列表，未提供所索取详情 |
| 4 | read_resource分别读取规则和事件词库 | 两次成功读取；随后达到四轮上限 |

共四次真实响应、五次工具请求与回执全部审读。没有 configure_investigation_draft，**模拟草案落地0/1**；没有title/objective或最终确认文本，不能说完整事件主题已正确进入草案。模型记录keep，没有设置地域；按冻结set标签，地域记录与落地原自动判分均失败。单凭keep也不能断言模型把事件理解错，因为原话可能是在指定事件而非额外限定讨论地域；当前证据无法确定其判断理由。

未发生用户追问，本次卡在模型/fixture的资源读取链路与四轮上限。第三轮fixture详情覆盖不足是已知影响因素；不能把全部原因归于模型，也不能假设第五轮一定会成功。本轮不延长预算、不改fixture、不修提示、不选择性重跑。

## 与旧结果的关系

原固定18dev中5/5可落地set结果继续保留，本例另列为自然事件补测0/1；不能用旧5/5承诺自然输入均成功，也不把本例偷偷加入旧dev/holdout后改写历史。此前选中组50请求50响应不变；本次新增5次请求尝试、4条完成响应（含首次SSL失败），项目累计**507请求/499响应**。新一例不构成稳定成功率证据。

直连耗时17.69秒；已返回usage prompt 100304 / completion 398 / total 100702，费用未知。没有生成草案，因此此耗时不是“草案生成时间”。框架脚本预检通过只能证明同一fixture/工具可执行，不是模型效果。原435+25测试证据保持；本次未改冻结代码，不重复无关回归。

## 证据与交接

- [直连真实结果](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/natural-event-kelamayi-direct-v1/results.json)
- [直连请求账本](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/natural-event-kelamayi-direct-v1/http-ledger.jsonl)
- [直连冻结](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/natural-event-kelamayi-direct-v1/freeze.json)
- [补测输入](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/natural-event-kelamayi-direct-v1/case.json)
- [首轮失败结果](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/natural-event-kelamayi-v1/results.json)
- [TLS诊断](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/natural-event-kelamayi-v1/tls-diagnostic.json)
- [离线框架预检](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/outputs/geographic-scope-intent/natural-event-kelamayi-v1/preflight.json)
- [逐例人工复核](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/natural-event-kelamayi-review-20261015.json)

候选代码与旧证据保持。本例用于验收时补充自然表达缺口，不宣布地域功能成功或P0整体通过；未改正式业务，未启动调查/采集，未进入P1。下一步如需修改fixture或调整轮次，需另记版本和本例非独立验证属性，不能覆盖本轮结果。

独立只读复核：[审读结论](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/natural-event-kelamayi-independent-review-20261015.json)；[证据核验](/Users/ext.wanghongtao6/.codex/worktrees/geographic-relevance-p0/xhs-audit-agent-demo/experiments/geographic_scope_intent/evidence/natural-event-kelamayi-audit-20261015.json)。两名实施/复核代理一致：未落草案、事件语义未获证明，不据keep单独判定理解对错。
