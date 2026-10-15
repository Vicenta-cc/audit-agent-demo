# P2/P3/P4 离线接口映射

只存在新实验包；上游 schema、评分、P3 判定及 P4 状态机均不修改。实际入口为 `project_plan`（首次）/`BoundAdapter`（恢复）和 `JointHarness`。首次必须提供真实 P1 authority、已持久化的 P2 plan_path、外部 identity/digest；恢复必须提供独立保存的 expected 参数。

| 消费字段/接口 | 来源与校验 |
|---|---|
| P2 schema/origin | 原 `geo-execution-plan-v1 / fixture`，真实 resolver/capture/build/validate/save/load |
| P4 schema/origin | 原 `p4-fixture-input-v1 / fixture`；不伪造正式授权 |
| execution_id | 测试调用方独立提供并持久化，不是正式 Run/Job |
| owner/session/draft/revision | P2 identity.owner/session_id/draft_id/revision 原值 |
| query_id/query/order | ID 为 P2 plan_digest + 原 order 的 canonical SHA256；原数组顺序/词串逐字保留 |
| query sources | P2 全部 source_term/sources 等原字段保存在 binding.query_sources，并参与 binding_digest |
| max_notes / task_limit | P2 query.per_keyword_limit（主链每词1）/collection.max_total_notes |
| capture_parameters | effective_parameters 全量复制，追加 candidate_count/candidate_comment_limit/detail_comment_limit/collect_comments；0/false保留 |
| triage_mode | P2 collection.triage_mode；仅 select 通过，off/compare拒绝 |
| P3 context | inputs.configuration.relevance_scope_state + inputs.p3_inputs，经原 P2 derive_scope_context |
| P3 versions | 固定 commit；原 prepare 构造专用元数据 probe 派生 scope/policy/admin/prompt/model 摘要；probe不进入候选、评分或计数 |
| P3 请求/决策 | 原 P2 prepare/consume，true/true开关；包装 GeoRecord 后原 P4 validate_geo_record 完整重放 |
| risk.category_ids / lexicon | inputs.risk_inputs.category_ids；terms/search_groups 原样 |
| risk.rules | 原 P1 resolver execution.prompt_profile_snapshot 冻结内容 |
| risk.parameters | model.name→model、max_comments/request_timeout、identity全部、四项 rule_score_*；不读当前settings补默认 |
| 固定分值/fingerprint | 原 P2 validate_risk_inputs 核验 model_scores/prompt_hit_limit/discard_score/engine_sha256；完整内容保存在P2原计划，不静默丢弃 |
| 评分执行 | P2 build_frozen_engine + 原 terms_for/score；人工client只返回测试响应；原CandidateScore全字段保存 |
| raw/normalized | occurrence 保留normalize后的candidate及raw_source原post/comments；风险输入取raw_source，并交P4返修校验；P3处理规范化映射 |
| account/missing evidence | capture使用显式测试账号，无cookie；候选原字段/评论/IP/缺失内容随capture保存，不补采 |
| 原排序与选择 | 原 ScopedExecution调用rank_candidates；原P4完成屏障/档位/词序/每词原排序/事务预算 |
| detail/review/deliver | 注入本地内容与人工P3响应；保留原风险，仅地域复核；内存受控sink使用稳定幂等key，不证明外部严格一次 |

三个摘要不能互换：`p2_plan_digest` 指原 P2，`p4_projection_digest` 指投影（P4 state/receipt仍用此摘要），`binding_digest` 关联二者及execution、identity、adapter version、导入manifest SHA、完整query sources及两份风险配置摘要。bundle保存完整P2序列化文本、P4投影和绑定；恢复重新parse并重建投影，核对外部预期后才能构造执行器。重算payload自己的摘要无法替换外部预期。

未直接驱动真实动作的字段：下载媒体/ASR/OCR、实际账号配置、start_page/search_sort/子评论等采集参数在投影/原计划中保留和比较，本轮capture只回放，不验证真实平台对这些参数的执行。模型invocation参数、名录、prompt、阈值保留冻结来源并核对纯接口，不发实际模型请求。资源权限和来源由真实P1临时库及P2验证，不能将fixture凭证作为正式授权。

输入清单：`artifacts/joint-20261015-01/INPUT_MANIFEST.json`。来源源码/旧证据与本轮新增输出分开登记；P4来源为返修摘要匹配版本，其原会话复验仍待完成，当前继续依据用户随后明确的离线联调指令。
