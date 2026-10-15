# 验收矩阵与逐例证据

本轮存在明确启动门槛例外：S0未找到P4原会话返修复验通过；用户随后明确要求继续离线联调。此项仍待复验，不改写为满足。其余来源、边界及测试证据如下。`J`指 `artifacts/final-joint-20261015-01/`；`P2`指 `../geographic_execution_plan/artifacts/joint-regression-20261015-01/`；`P4`指 `../geographic_selection/artifacts/joint-regression-20261015-01/`。未特别标明的测试均是新增joint测试；上游补充测试明确单列。

| 启动文件第8节分组 | case ID / 测试节点与实际断言 | 证据 |
|---|---|---|
| 门槛/版本 | P4原会话复验待完成；最新用户指令覆盖等待条件。P1 1570原文件、209导入文件来源与目标、P3两份各11导出逐字节核对 | INPUT_MANIFEST、source-audit-final、FINAL_TESTED_SOURCE |
| 真实P2主链路 | execution `resources-0-0-0 / 1-0-1 / 0-1-1 / 1-1-0`；risk `resources-rules-*-lexicon-*`：四组合真实resolver/capture/save/load进入P4；缺资源拒绝由原P2 resources测试覆盖 | J/cases/resources-*.json；J/risk-contract-cases.json；P2/junit.xml |
| 两类来源 | 上述conversation/UI真实记录；risk `test_ui_source_survives_real_title_rule_revision_and_rejects_bad_lineage`；上游P2 invalid_bindings/provenance/resources | J/risk-contract-cases.json；P2/junit.xml |
| 原词/参数 | `exact-query-and-region-invariance`，`parameters-False-75 / True-0 / True-75`，`search-max-1/10/100/101/200`；四词空格/原序/多来源保留，P2有效每词1、false/0/revision0保持 | J/risk-contract-cases.json；J/adapter-contract-cases.json |
| 双摘要/防篡改 | adapter `test_self_rehashed_projection_cannot_replace_external_binding`，`test_independent_expected_values_required`，authority/存储/源码清单负例；旧receipt完整绑定由原P4 store `test_receipt_must_match_full_binding_and_delivery_needs_review`覆盖 | J/adapter-contract-cases.json；P4/pytest.log；测试源码 |
| 风险直接对照 | risk四资源组合：原P1真实resolver构建引擎，对比完整prompt/kwargs/category/terms/group/rules、身份排除、自命中、全部CandidateScore字段及原排序 | J/risk-contract-cases.json，各case baseline_calls/joint_records/baseline_scores/original_order |
| 地域不改风险 | `exact-query-and-region-invariance`两目标策略风险input/calls/score完全相同；`resources-*`晚词local weak优先早词province strong；`province-before-ip`省级weak优先IP strong；`same-tier-query-order / original-rank-order`保留原序 | J/risk-contract-cases.json；J/cases/*.json |
| P4返修防回退 | `old-risk-post/comments/identity`：相同execution/query/content/rank、同occurrence ID，首次写入及完成屏障都拒绝；identity当前discard/旧weak，detail与deliver均0；`detail-change-post/comments/identity`同档变化保留原风险不重评分 | J/cases/old-risk-*.json；J/cases/detail-change-*.json；原P4返修23用例也重跑 |
| P3真实纯接口 | adapter实际风险/地域测试、risk `test_non_xinjiang_ip_and_comment_text_isolation`：原prepare/consume、GeoRecord完整验证；评论文字不进semantic prompt，IP证据可变；固定true/true | J/adapter-contract-cases.json；J/risk-contract-cases.json |
| IP非硬编码 | `non-xinjiang-ip-strong / non-xinjiang-ip-weak`与深圳/广东评论去重、覆盖/比例测试；none、失败回退weak与协议/冲突/不足负例由原P4 ip_requires_valid_strong/bridge及原P2 p3测试覆盖 | J/cases/non-xinjiang-*.json；J/risk-contract-cases.json；P2/junit.xml；P4/pytest.log |
| 地域范围 | `local-only-excludes-province / province-target-local / municipality-target / explicit-other-city / p3-known-mixed-province-city`；后者如实消费固定province_unknown并标明限制 | J/cases/*.json |
| 完成屏障/归属 | `resources-*`全部capture先于detail；`pause-and-empty / capture-failed-barrier`；`same-entity-later-query` A选Y、B合法选X；不同query/rank/comments原P4 bridge/store与risk对照 | J/cases/*.json；J/risk-contract-cases.json；P4/pytest.log |
| 失败/预算 | `failure-empty/media/authentication/rate_limit`：空/媒体不成功、不补挑；换号一次、持久auth_retries及恢复不重采；限流抛出并保留待对账预留。DB提交失败、认证中断重启、多条批次由原P4 store/execution测试覆盖 | J/cases/failure-*.json；P4/pytest.log |
| 精采/恢复 | `restore-after_capture/risk/geo/reserve/receipt/settle/delivery`；`restore-unknown-result`返回未记receipt后保留预留、可信对账；detail变档不交付但保留已采额度；`independent-process-reserve`真实新Python进程恢复 | J/cases/restore-*.json；J/cases/detail-change-tier.json；J/cases/independent-process-reserve*.json；J/allowed-children.jsonl |
| 不读live | `restore-no-live-reads`及risk `test_restore_after_live_rules_words_settings_changed_never_reads_live`：修改真实临时规则/词库/设置/草案；恢复resolver/settings设失败探针，风险与P3请求保持，已完成评分不重复 | J/cases/restore-no-live-reads.json；J/risk-contract-cases.json |
| 缺省/不支持 | adapter no_scope返回None，off/compare拒绝；pending、不一致、其他平台/creator由原P2 integration/p3测试覆盖 | J/adapter-contract-cases.json；P2/junit.xml |
| 禁区/阻断 | 原geographic_draft_configuration全部32项、triaged_search/account_rotation原回归；产品文件逐字节不变；外层guard意外尝试0、实际外部成功0 | P2/junit.xml；P4/checks.json；J/summary.json；source-audit-final.json |

J的case JSON保留原bundle/P2序列化、P4投影、三类摘要、raw候选、风险请求/结果、P3请求/人工响应/结果、query/occurrence/attempt/receipt/交付调用轨迹。adapter和risk契约另用聚合JSON按case/node标识保存输入输出。JUnit为最终每节点断言结果；上游P4原runner只生成pytest日志/checks，没有伪造JUnit。

独立子进程恢复在同一受限临时根生命周期中运行，结束后删除DB；可通过README测试命令重新构造并复跑。内存sink只证明受控环境幂等；它不证明跨进程生产交付严格一次。
