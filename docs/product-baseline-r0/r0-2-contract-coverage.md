# R0.2 Contract Coverage Matrix

| Area | Current product contract | Evidence | Result |
|---|---|---|---|
| M0 | ledger, failed execution recovery, once-only execution, unknown outcome, completed replay | `test_hermes_m1_m22_offline.py`, `test_r02_runtime_closure.py` | PASS |
| M0 refs | stale and cross-session references reject | candidate-local ref test | PASS |
| M1 report | read report; Finding/Post/Evidence navigation; preview/detail; public IDs | candidate fixture tests plus published-report offline navigation | PASS |
| M1 membership | displayed membership, representative flag, membership Evidence subset versus all Post Evidence | published-report offline navigation against final service | PASS |
| M1 search | fixed report scope, cursor binding, risk preview, no client-selectable task scope | final schema exact check plus canonical service regression suite | PASS |
| M2/M2.1 | Account Overview, cross-investigation distribution, Comment to Parent Post, unified Account bridge | candidate corpus tests and published-report offline navigation | PASS |
| M2.2 | non-Top5 Account, all author roles, `risk_filter`, target+risk composition, no parent risk inheritance, risk comment directory | candidate-local deterministic tests plus final service smoke | PASS |
| M2.2 safety | no internal IDs/path; no relationship/support/coordination inference | public result scans and final Tool descriptions | PASS |
| R3.1 structure | ordered sections, zero standalone, target separation, Account Overview, schema fence | R3.1 tests and published SQLite validation | PASS |
| R3.1 persistence | ReportStore restart, complete Account index/pagination, published repository | temporary-copy restart and read-only repository smoke | PASS |
| R3.1 presentation | frontend JSON and Markdown share the graph source; legacy API remains | canonical graph exact source, structured route, stage 3b tests | PASS |
| API/SSE | stable URLs/DTOs, replay/resume/sequences/completed replay | full backend suite and focused formal-service tests | PASS |

Excluded as non-product contracts: Provider traces, live held-out runners,
cross-dataset TASK_MODE, diagnostic-only prompts/config, and all pre-Hermes
Controller/Planner/Focus/Dossier/Search implementations. No excluded runtime
was reintroduced to satisfy a test.
