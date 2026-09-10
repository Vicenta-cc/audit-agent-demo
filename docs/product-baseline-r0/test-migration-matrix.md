# Hermes Deterministic Test Migration Matrix

Audit source: Hermes M2.2 worktree commit
`0a5090579c4cfcda1208269814f32fbe77da4c86`. The matrix records contract
semantics, not historical runtime implementation. Candidate tests run only on
candidate fixtures and temporary ledgers/databases.

| Test source | Contract covered | Ported | Excluded | Reason |
|---|---|---|---|---|
| `tests/hermes_m0/test_smoke.py` | Tool/schema declaration boundary | Yes, `tests/test_hermes_m1_m22_offline.py` | No | Candidate checks the canonical M0/M1 tool lists offline |
| `tests/hermes_m0/test_tool_contracts.py` | Report, Finding, Post, Evidence navigation, parent/reference fencing, public-safe results | Yes, `tests/test_hermes_m1_m22_offline.py` | No | Candidate-local frozen report fixture exercises the full read chain |
| `tests/hermes_m0/test_m1_r1_report_navigation.py` | M1 report/category/post/evidence navigation and replay boundaries | Partial | Real-report and provider-specific cases | Candidate has the report fixture chain; no external published DB or provider trace is imported |
| `tests/hermes_m0/test_m1_search.py` | Cross-dataset search and cursor continuation | No | Yes | Requires a separate cross-dataset fixture and is outside the selected Report/Finding/Post/Evidence contract |
| `tests/hermes_m0/test_task_snapshot_tools.py` | Cross-task snapshot tools and source projection | No | Yes | Uses a historical cross-dataset fixture; no current product dependency was proven |
| `tests/hermes_m0/test_recovery.py` | Interrupted/failed execution, unknown outcome fence, execution_count=1, completed replay | Yes, `tests/test_hermes_m1_m22_offline.py` | No | Candidate-local `ToolExecutionLedger` tests cover interruption, orphan recovery and exact replay |
| `tests/hermes_m0/test_m2_account_activity.py` | Account bridge, occurrence paging, Comment/Post roles and parent navigation | Partial | Published-report DB integration cases | Candidate Account corpus tests cover non-Top5 accounts, risk filtering, author identity and parent occurrence chain |
| `tests/hermes_m0/test_m21_account_corpus_post_projection.py` | Comment/Post Author identity, title/time preservation and parent post projection | Partial | Tests requiring external report DB | Candidate verifies frozen occurrence identity and parent author/display projection |
| `tests/hermes_m0/test_m21_cross_investigation_account_drilldown.py` | Cross-investigation Account Overview and public-safe drilldown | No | Yes | Requires multiple published report SQLite sources not copied into candidate |
| `tests/hermes_m0/test_m22_unified_account_risk_navigation.py` | All-risk-comment navigation and M2.2 report-linked risk paths | Partial | Held-out runner and external report paths | Candidate covers offline risk-only filtering and parent identity; frozen questions/oracle remain evidence-only |
| `tests/hermes_m0/test_m22_heldout_runtime_preflight.py` | Exact interpreter/provider preflight | No | Yes | Provider/live preflight is forbidden in R0.1; exact Hermes package gate is tracked separately |
| `tests/reporting/test_report_generation_r31_account_overview.py` | R3.1 ordered sections and Account Overview projection | Yes, `tests/test_r31_contracts.py` | Full generation against published DB | Candidate tests deterministic section numbering and public account ordering with synthetic in-memory state |
| `tests/reporting/test_report_generation_r3_account_entries.py` | R3 account entries, account cards, replay | Partial | Full R3 published fixture generation | Current candidate's report graph and Account corpus tests cover the stable portions without external paths |
| `tests/reporting/test_report_generation_r2.py` | R2 report section/claim contracts | Yes, existing `tests/test_report_graph.py` | Provider repair/trace cases | Deterministic report generation is already covered by candidate fake model tests |
| `tests/reporting/test_reporting_import_boundaries.py` | Lazy reporting import boundary and no eager LangGraph import | Yes, copied unchanged to candidate | No | Four deterministic import-boundary tests pass in candidate venv |

The selected candidate suite covers M1 Report/Finding/Post/Evidence, M2.2
Account bridge and risk navigation, Comment/Post Author and Parent Post
identity, recovery/replay fencing, R3.1 ordering and Account Overview,
ReportStore reload/immutability, reporting import boundaries, and public
internal-ID/absolute-path leakage checks. Provider traces, one-shot runners,
real artifacts, live databases and pre-Hermes runtimes are excluded by design.
