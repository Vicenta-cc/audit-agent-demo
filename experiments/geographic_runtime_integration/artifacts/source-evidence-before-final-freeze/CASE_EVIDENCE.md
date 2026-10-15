# 正式地域接入逐例证据索引

状态：实施证据，待原会话验收。不是P4返修原会话复验，也不是R6真实链路通过。

本文件只索引实际测试节点、JUnit/guard产物与只读源码比较；没有制造请求/响应或完整场景trace。测试文件存在不等于运行通过。分轮结果不能相加冒充统一验收。SOURCE_MANIFEST是当前共享工作树快照；既有runner未记录每个业务文件的运行时hash，不能反称全部历史通过与当前字节严格相同。父任务最终统一验证后须补最终快照。

## 源码边界

- triage.py、triage_matcher.py、crawler_adapter.py、P1 geographic_catalog.py/catalog JSON/scope_selection.py：完整字节同时等于最终P1与整合HEAD。
- pipeline._run_triaged_search：AST同时等于最终P1与整合HEAD；pipeline其它正式分派部分有授权改动，不称整个pipeline未变。
- scope_intent.py仅scope_blockers/ensure_execution_ready AST改变（显式服务端隔离能力）；其余P1函数AST未变。默认能力关闭证据须结合测试。
- P2/P4目录共22份P3导出逐字节等于固定tag提交。运行时衍生relevance.py/relevance_scope.py仅fixture→confirmed_run与错误码字面量替换，其他导出字节一致。新__init__封装另列摘要，不冒充原tag。
- 仓库未跟踪MediaCrawler源码；crawler_adapter未变。未审阅外部MediaCrawler工作区/运行版本，不能证明外部环境未变化。

## T01–T25映射

### T01

部分：正式profile拒fixture已有通过；完整确认反伪造以实际运行node为准。

- 定义：`tests/test_geographic_runtime_freeze.py::test_fixture_and_rehashed_current_resource_forgery_rejected`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_bridge.py::test_production_profile_rejects_fixture_origin_and_rehashed_drift`
  - 实际运行：[runtime-bridge-green-01](../geographic_joint_integration/artifacts/runtime-bridge-green-01/junit.xml): passed=1；[runtime-bridge-green-02](../geographic_joint_integration/artifacts/runtime-bridge-green-02/junit.xml): passed=1；[runtime-bridge-red-01](../geographic_joint_integration/artifacts/runtime-bridge-red-01/junit.xml): failed=1

### T02

四资源正式确认/桥接已跑；并发变更事务需freeze证据。

- 定义：`tests/test_geographic_runtime_freeze.py::test_formal_capture_freezes_real_authority`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_freeze.py::test_confirm_job_roundtrip_and_live_drift`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_freeze.py::test_confirmation_rechecks_authority_and_rolls_back`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_bridge.py::test_actual_confirmed_run_drives_bridge_without_risk_changes`
  - 实际运行：[runtime-bridge-green-02](../geographic_joint_integration/artifacts/runtime-bridge-green-02/junit.xml): passed=4

### T03

旧联合测试本轮受固定导入manifest保护拒绝；不能宣称边界完整通过。

- 定义：`tests/test_geographic_joint_risk.py::test_effective_parameter_zero_false_and_revision_zero`
  - 实际运行：[runtime-joint-final-01](../geographic_joint_integration/artifacts/runtime-joint-final-01/junit.xml): failed=3
- 定义：`tests/test_geographic_joint_risk.py::test_search_terms_cap_supported_range_reaches_projection`
  - 实际运行：[runtime-joint-final-01](../geographic_joint_integration/artifacts/runtime-joint-final-01/junit.xml): failed=5
- 定义：`tests/test_geographic_joint_risk.py::test_exact_four_queries_sources_and_region_changes_leave_risk_unchanged`
  - 实际运行：[runtime-joint-final-01](../geographic_joint_integration/artifacts/runtime-joint-final-01/junit.xml): failed=1

### T04

正式对象重建恢复已通过；完整独立worker重启/live漂移须各node证据，旧联合失败不算通过。

- 定义：`tests/test_geographic_runtime_freeze.py::test_confirm_job_roundtrip_and_live_drift`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_pipeline.py::test_formal_capture_barrier_detail_review_durable_delivery_and_restart`
  - 实际运行：[runtime-pipeline-green-01](../geographic_joint_integration/artifacts/runtime-pipeline-green-01/junit.xml): passed=1；[runtime-pipeline-green-02](../geographic_joint_integration/artifacts/runtime-pipeline-green-02/junit.xml): passed=1；[runtime-pipeline-red-01](../geographic_joint_integration/artifacts/runtime-pipeline-red-01/junit.xml): failed=1
- 定义：`tests/test_geographic_joint_risk.py::test_restore_after_live_rules_words_settings_changed_never_reads_live`
  - 实际运行：[runtime-joint-final-01](../geographic_joint_integration/artifacts/runtime-joint-final-01/junit.xml): failed=1

### T05

已有旧triaged36与报告28回归；完整v2/v3/v4所有序列化组合未由此证明。

- 定义：`tests/test_geographic_runtime_freeze.py::test_optional_plan_absent_from_legacy_serialization`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_reporting.py::test_non_geographic_snapshot_omits_new_fields`
  - 实际运行：[runtime-report-green-01](../geographic_joint_integration/artifacts/runtime-report-green-01/junit.xml): passed=1；[runtime-report-green-02](../geographic_joint_integration/artifacts/runtime-report-green-02/junit.xml): passed=1；[runtime-report-green-03](../geographic_joint_integration/artifacts/runtime-report-green-03/junit.xml): passed=1；[runtime-report-green-04](../geographic_joint_integration/artifacts/runtime-report-green-04/junit.xml): passed=1；[runtime-report-red-01](../geographic_joint_integration/artifacts/runtime-report-red-01/junit.xml): passed=1

### T06

已通过10组原P3固定响应差分和固定源码校验；不代表真实模型质量。

- 定义：`tests/test_geographic_runtime_bridge.py::test_p3_production_origin_changes_only_envelope`
  - 实际运行：[runtime-bridge-green-01](../geographic_joint_integration/artifacts/runtime-bridge-green-01/junit.xml): passed=10；[runtime-bridge-green-02](../geographic_joint_integration/artifacts/runtime-bridge-green-02/junit.xml): passed=10；[runtime-bridge-red-01](../geographic_joint_integration/artifacts/runtime-bridge-red-01/junit.xml): failed=10
- 定义：`tests/test_geographic_runtime_bridge.py::test_runtime_source_manifest_and_semantic_code_are_exact`
  - 实际运行：[runtime-bridge-green-01](../geographic_joint_integration/artifacts/runtime-bridge-green-01/junit.xml): passed=1；[runtime-bridge-green-02](../geographic_joint_integration/artifacts/runtime-bridge-green-02/junit.xml): passed=1；[runtime-bridge-red-01](../geographic_joint_integration/artifacts/runtime-bridge-red-01/junit.xml): failed=1

### T07

正式Bridge旧原文/评论/身份拒绝已通过；首次/屏障/恢复完整证据须P4新鲜runner完成。

- 定义：`tests/test_scoped_execution.py::test_first_risk_rejects_old_record_for_same_identity_before_persistence`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_scoped_selection_store.py::test_risk_write_and_query_barrier_bind_immutable_capture`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_scoped_selection_store.py::test_recomputed_snapshot_digest_does_not_authorize_old_risk_cache`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_bridge.py::test_actual_confirmed_run_drives_bridge_without_risk_changes`
  - 实际运行：[runtime-bridge-green-02](../geographic_joint_integration/artifacts/runtime-bridge-green-02/junit.xml): passed=4

### T08

raw字段与规范投影差异有专门测试；逐别名全量覆盖不额外推断。

- 定义：`tests/test_scoped_execution.py::test_first_risk_uses_raw_capture_not_lossy_p3_fields`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_bridge.py::test_actual_confirmed_run_drives_bridge_without_risk_changes`
  - 实际运行：[runtime-bridge-green-02](../geographic_joint_integration/artifacts/runtime-bridge-green-02/junit.xml): passed=4

### T09

正式Bridge与pipeline通过：原风险保留、同档精采交付。

- 定义：`tests/test_scoped_execution.py::test_detail_same_tier_keeps_first_risk_and_capture_after_evidence_change`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_bridge.py::test_actual_confirmed_run_drives_bridge_without_risk_changes`
  - 实际运行：[runtime-bridge-green-02](../geographic_joint_integration/artifacts/runtime-bridge-green-02/junit.xml): passed=4
- 定义：`tests/test_geographic_runtime_pipeline.py::test_formal_capture_barrier_detail_review_durable_delivery_and_restart`
  - 实际运行：[runtime-pipeline-green-01](../geographic_joint_integration/artifacts/runtime-pipeline-green-01/junit.xml): passed=1；[runtime-pipeline-green-02](../geographic_joint_integration/artifacts/runtime-pipeline-green-02/junit.xml): passed=1；[runtime-pipeline-red-01](../geographic_joint_integration/artifacts/runtime-pipeline-red-01/junit.xml): failed=1

### T10

正式变档无membership通过；P3失败/不足与预算完整组合依P4 runner证据。

- 定义：`tests/test_geographic_runtime_pipeline.py::test_detail_changed_outside_region_never_creates_membership`
  - 实际运行：[runtime-pipeline-green-01](../geographic_joint_integration/artifacts/runtime-pipeline-green-01/junit.xml): failed=1；[runtime-pipeline-green-02](../geographic_joint_integration/artifacts/runtime-pipeline-green-02/junit.xml): passed=1；[runtime-pipeline-red-01](../geographic_joint_integration/artifacts/runtime-pipeline-red-01/junit.xml): failed=1
- 定义：`tests/test_scoped_execution.py::test_changed_tier_is_collected_not_delivered_and_budget_stays_used`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。

### T11

正式全词屏障通过；晚词weak优先等原选择器组合需P4测试；旧联合此次失败。

- 定义：`tests/test_scoped_selection.py::test_tier_outer_then_query_then_supplied_ranking_without_mutation`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_joint_execution.py::test_real_resource_combinations_late_local_beats_early_province`
  - 实际运行：[runtime-joint-final-01](../geographic_joint_integration/artifacts/runtime-joint-final-01/junit.xml): failed=4
- 定义：`tests/test_geographic_runtime_pipeline.py::test_formal_capture_barrier_detail_review_durable_delivery_and_restart`
  - 实际运行：[runtime-pipeline-green-01](../geographic_joint_integration/artifacts/runtime-pipeline-green-01/junit.xml): passed=1；[runtime-pipeline-green-02](../geographic_joint_integration/artifacts/runtime-pipeline-green-02/junit.xml): passed=1；[runtime-pipeline-red-01](../geographic_joint_integration/artifacts/runtime-pipeline-red-01/junit.xml): failed=1

### T12

覆盖定义存在；完整正式路径各策略组合未单独跑通，旧联合失败不视为通过。

- 定义：`tests/test_scoped_selection.py::test_policy_and_p3_consumption`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_scoped_selection.py::test_ip_requires_valid_strong_but_not_risk_score`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_joint_execution.py::test_frozen_geographic_policy`
  - 实际运行：[runtime-joint-final-01](../geographic_joint_integration/artifacts/runtime-joint-final-01/junit.xml): failed=6

### T13

原共用核心测试；真实账户轮换/并发正式worker未运行。

- 定义：`tests/test_scoped_selection_store.py::test_two_connections_only_one_active_batch`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_scoped_execution.py::test_one_auth_rotation_without_rescoring`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_scoped_execution.py::test_concurrent_capture_claim_does_not_repeat_external_capture`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_scoped_execution.py::test_concurrent_detail_claim_does_not_repeat_external_detail`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。

### T14

正式中断capture不精采已通过；各外部故障为受控端口，非真实平台验证。

- 定义：`tests/test_geographic_runtime_pipeline.py::test_partial_stopped_capture_never_reaches_detail`
  - 实际运行：[runtime-pipeline-green-01](../geographic_joint_integration/artifacts/runtime-pipeline-green-01/junit.xml): passed=1；[runtime-pipeline-green-02](../geographic_joint_integration/artifacts/runtime-pipeline-green-02/junit.xml): passed=1；[runtime-pipeline-red-01](../geographic_joint_integration/artifacts/runtime-pipeline-red-01/junit.xml): failed=1
- 定义：`tests/test_scoped_execution.py::test_capture_failure_never_opens_supplement`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_scoped_execution.py::test_pause_and_zero_candidates_have_distinct_outcomes`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。

### T15

部分覆盖；完整R3逐断点独立进程与陈旧detail_completed拒绝尚未充分证明。

- 定义：`tests/test_scoped_execution.py::test_restart_does_not_recollect_completed_or_reserved_batch`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_scoped_execution.py::test_unknown_result_is_not_retried_without_complete_trusted_receipt`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_delivery.py::test_ledger_failure_rolls_back_membership_and_retry_reuses_evidence`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。

### T16

现有证据不能声称正式worker审核完成标记前所有独立进程故障恢复完成。

- 定义：`tests/test_geographic_runtime_delivery.py::test_new_process_recovers_only_current_evidence`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_scoped_execution.py::test_delivery_failure_retries_same_key_and_never_recollects`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。

### T17

原风险引擎/匹配器全字节一致、旧搜索AST一致，正式四资源风险调用/分数对照已通过。

- 定义：`tests/test_geographic_runtime_bridge.py::test_original_risk_inputs_requests_scores_and_sort_are_unchanged`
  - 实际运行：[runtime-bridge-green-01](../geographic_joint_integration/artifacts/runtime-bridge-green-01/junit.xml): passed=1；[runtime-bridge-green-02](../geographic_joint_integration/artifacts/runtime-bridge-green-02/junit.xml): passed=1；[runtime-bridge-red-01](../geographic_joint_integration/artifacts/runtime-bridge-red-01/junit.xml): failed=1
- 定义：`tests/test_geographic_runtime_bridge.py::test_actual_confirmed_run_drives_bridge_without_risk_changes`
  - 实际运行：[runtime-bridge-green-02](../geographic_joint_integration/artifacts/runtime-bridge-green-02/junit.xml): passed=4
- 定义：`tests/test_geographic_joint_risk.py::test_real_resolver_four_resources_request_and_score_parity`
  - 实际运行：[runtime-joint-final-01](../geographic_joint_integration/artifacts/runtime-joint-final-01/junit.xml): failed=4

### T18

节点已定义，实际fresh结果见下；不将单一授权测试推成全分页/所有篡改组合证明。

- 定义：`tests/test_geographic_runtime_status.py::test_authorized_real_run_endpoint_and_foreign_owner`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_status.py::test_authorization_precedes_database_and_snapshot_access`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_delivery.py::test_delivery_rejects_wrong_binding_and_expired_lease`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。

### T19

发布冻结/读取错content/篡改拒绝已通过；多个真实source_job聚合与多轮scope未单独端到端验证。

- 定义：`tests/test_geographic_reporting.py::test_geographic_snapshot_freezes_basis_and_keeps_risk_counts`
  - 实际运行：[runtime-report-green-01](../geographic_joint_integration/artifacts/runtime-report-green-01/junit.xml): passed=1；[runtime-report-green-02](../geographic_joint_integration/artifacts/runtime-report-green-02/junit.xml): failed=1；[runtime-report-green-03](../geographic_joint_integration/artifacts/runtime-report-green-03/junit.xml): passed=1；[runtime-report-green-04](../geographic_joint_integration/artifacts/runtime-report-green-04/junit.xml): passed=1；[runtime-report-red-01](../geographic_joint_integration/artifacts/runtime-report-red-01/junit.xml): failed=1
- 定义：`tests/test_geographic_reporting.py::test_real_delivery_context_is_readonly_source_bound_and_tamper_checked`
  - 实际运行：[runtime-report-green-02](../geographic_joint_integration/artifacts/runtime-report-green-02/junit.xml): passed=1；[runtime-report-green-03](../geographic_joint_integration/artifacts/runtime-report-green-03/junit.xml): passed=1；[runtime-report-green-04](../geographic_joint_integration/artifacts/runtime-report-green-04/junit.xml): passed=1

### T20

状态分类/worker门禁专测；真实零召回UI全过程未运行。

- 定义：`tests/test_geographic_runtime_status.py::test_incomplete_cannot_publish`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_status.py::test_empty_differs_from_positive_complete`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_status.py::test_worker_gate_rejects_pending_before_audit_completion`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。

### T21

默认门禁及服务端能力测试；未启动或改动现有P1服务。

- 定义：`tests/test_geographic_runtime_freeze.py::test_capability_defaults_closed_and_rejects_foreign_database`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_freeze.py::test_direct_store_cannot_use_installed_capability`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_freeze.py::test_isolated_capability_does_not_enable_wrong_mode`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_freeze.py::test_isolation_capability_does_not_survive_store_recreation`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。

### T22

未执行：R6隔离前端真实克拉玛依大火、模型/采集/审核/报告链全部未做，不用替身替代。


### T23

受控失效fence覆盖；真实多worker竞争及crawl epoch全过程未验证。

- 定义：`tests/test_geographic_runtime_pipeline.py::test_runtime_uses_frozen_parameters_and_rejects_stale_fence`
  - 实际运行：[runtime-pipeline-green-01](../geographic_joint_integration/artifacts/runtime-pipeline-green-01/junit.xml): passed=1；[runtime-pipeline-green-02](../geographic_joint_integration/artifacts/runtime-pipeline-green-02/junit.xml): passed=1；[runtime-pipeline-red-01](../geographic_joint_integration/artifacts/runtime-pipeline-red-01/junit.xml): failed=1
- 定义：`tests/test_geographic_runtime_freeze.py::test_runtime_fence_checks_current_claim_on_every_call`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_store.py::test_commit_fence_rolls_back_events_with_operation`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_delivery.py::test_lease_expiring_before_delivery_commit_rolls_back`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。

### T24

交付证据专属文件与恢复读取测试；完整两个正式Run→审核结果全过程未独立证明。

- 定义：`tests/test_geographic_runtime_delivery.py::test_current_raw_survives_shared_old_and_retry`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_runtime_delivery.py::test_new_process_recovers_only_current_evidence`
  - 本索引扫描到的已完成guard产物中无该节点运行证据。
- 定义：`tests/test_geographic_reporting.py::test_real_delivery_context_is_readonly_source_bound_and_tamper_checked`
  - 实际运行：[runtime-report-green-02](../geographic_joint_integration/artifacts/runtime-report-green-02/junit.xml): passed=1；[runtime-report-green-03](../geographic_joint_integration/artifacts/runtime-report-green-03/junit.xml): passed=1；[runtime-report-green-04](../geographic_joint_integration/artifacts/runtime-report-green-04/junit.xml): passed=1

### T25

未实现/未验证：跨进程所有模型传输原子总额度、内部重试、超时不退、重启不清零。R6前置缺口。


## 新鲜运行记录（保留失败）

| 产物目录 | tests / failures / errors / skipped | exit | unexpected guard | 真外部调用成功 |
|---|---|---|---|---|
| [runtime-auth-red-01](../geographic_joint_integration/artifacts/runtime-auth-red-01/summary.json) | 1 / 1 / 0 / 0 | 1 | 0 | 0 |
| [runtime-auth-red-02](../geographic_joint_integration/artifacts/runtime-auth-red-02/summary.json) | 1 / 1 / 0 / 0 | 1 | 0 | 0 |
| [runtime-bridge-green-01](../geographic_joint_integration/artifacts/runtime-bridge-green-01/summary.json) | 14 / 0 / 0 / 0 | 0 | 0 | 0 |
| [runtime-bridge-green-02](../geographic_joint_integration/artifacts/runtime-bridge-green-02/summary.json) | 18 / 0 / 0 / 0 | 0 | 0 | 0 |
| [runtime-bridge-red-01](../geographic_joint_integration/artifacts/runtime-bridge-red-01/summary.json) | 14 / 14 / 0 / 0 | 1 | 0 | 0 |
| [runtime-joint-final-01](../geographic_joint_integration/artifacts/runtime-joint-final-01/summary.json) | 70 / 56 / 13 / 0 | 1 | 0 | 0 |
| [runtime-pipeline-green-01](../geographic_joint_integration/artifacts/runtime-pipeline-green-01/summary.json) | 4 / 1 / 0 / 0 | 1 | 0 | 0 |
| [runtime-pipeline-green-02](../geographic_joint_integration/artifacts/runtime-pipeline-green-02/summary.json) | 40 / 0 / 0 / 0 | 0 | 0 | 0 |
| [runtime-pipeline-red-01](../geographic_joint_integration/artifacts/runtime-pipeline-red-01/summary.json) | 4 / 4 / 0 / 0 | 1 | 0 | 0 |
| [runtime-report-green-01](../geographic_joint_integration/artifacts/runtime-report-green-01/summary.json) | 31 / 0 / 0 / 0 | 0 | 0 | 0 |
| [runtime-report-green-02](../geographic_joint_integration/artifacts/runtime-report-green-02/summary.json) | 32 / 1 / 0 / 0 | 1 | 0 | 0 |
| [runtime-report-green-03](../geographic_joint_integration/artifacts/runtime-report-green-03/summary.json) | 32 / 0 / 0 / 0 | 0 | 0 | 0 |
| [runtime-report-green-04](../geographic_joint_integration/artifacts/runtime-report-green-04/summary.json) | 32 / 0 / 0 / 0 | 0 | 0 | 0 |
| [runtime-report-red-01](../geographic_joint_integration/artifacts/runtime-report-red-01/summary.json) | 3 / 2 / 0 / 0 | 1 | 0 | 0 |

每目录 `invocation.json` 给实际启动命令，`commands.json` 给pytest argv及导入前守卫，`junit.xml` 给节点状态，`pytest.log` 给原错误。例：`PYTHONPATH=backend:. /Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -m experiments.geographic_joint_integration.run_offline --output-dir experiments/geographic_joint_integration/artifacts/runtime-report-green-04 tests/test_geographic_reporting.py tests/test_pass_report.py tests/test_published_report_runtime_snapshot.py`。

runtime-joint-final-01失败原文包括 `imported_source_drift:backend/investigation_creation/scope_search_plan.py`：旧联合manifest固定旧导入字节，正式契约扩展后它正确拒绝，不能修改旧manifest后称原证据通过。该轮70 collected/56 failed/13 errors/1 passed，不能称历史87回归已通过。

## 未验证与准入缺口

- P4风险证据绑定返修的原会话明确复验通过记录仍缺失；所有作者自测均不替代。
- R6没有执行：未启动真实服务/浏览器业务流程、未调用真实模型或平台采集、未验证账号登录及代理、未产出真实合法帖子报告。
- T25传输预算门未实现/验证；R6不得视为就绪。
- R3列举全部断点的正式worker独立进程恢复、多源地域聚合发布隔离、真实账户轮换仍须补足，不能从局部单测外推。
- 原报告与失败日志未覆盖；未提交或部署。

## 其他执行者交付的新鲜日志（证据等级单独列）

- `/tmp/geographic-r1-r4-validation-20261015-01/freeze.log`：原文 `17 passed, 2 warnings in 19.64s`，对应 `tests/test_geographic_runtime_freeze.py`；为 T01/T02/T04/T05/T21/T23 提供分组通过证据。日志没有每个 node 或 guard 结构元数据，不能伪造逐例 trace/守卫证明。
- 同目录 `logic.log`：31 个 Playwright logic 测试通过，其中 `geographicRuntimePresentation.spec.tsx` 8 项包含状态原因、IP 未核实标签、原风险统计不变、无地域兼容和冻结来源展示。是组件/逻辑测试，不是 T22 浏览器真实业务流程。
- 同目录 `typecheck.log` 无诊断、`build.log` 原文 `built in 1.42s`，大 chunk 警告保留。执行者报告命令为 `npm run typecheck`、`VITE_API_PROXY_TARGET=http://127.0.0.1:8010 npm run build`，本只读核验没有启动服务器。
- `/tmp/geographic-runtime-store-delivery-status-m3.log`：原文 `66 passed, 6 warnings, 23 subtests passed in 6.00s`；执行者说明包含 runtime store/delivery/status 与原 M3。没有逐 node JUnit，故不将此日志伪装成每个新增节点的运行记录；晚于此日志新增的测试不能引用它作为通过证据。
- 上述日志 SHA256 固定在 SOURCE_MANIFEST；正式统一 guard 结果应由最终 REPORT 引用，不能把这些分次通过相加成验收总数。
