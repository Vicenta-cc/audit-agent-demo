# Product Baseline R0.2 Capability Traceability Matrix

The default question for a pre-Hermes experiment is **whether the current
product or Hermes has an irreplaceable dependency**, not which code should be
merged. If the answer is no, record its archival and recovery path only; do
not perform source integration.

| Capability | Canonical source | Status in R0 | Evidence / deterministic check | Open condition |
|---|---|---|---|---|
| Product shell and reviewed investigation UI | Demo `cd62a96` plus reviewed dirty files | Included | `pnpm build`, TypeScript check | UI remains product shell, not Agent authority |
| FastAPI job and crawler APIs | Demo `cd62a96` plus `backend/main.py`/`backend/api` | Included as compatibility | `tests/test_stage_3b_api.py`; route import check | External API must remain stable |
| Investigation session, turn, replay and SSE | `backend/hermes_runtime/service.py` over the retained store/API facade | Hermes formal runtime; legacy facade retained | Formal API/executor Barrier overlap, transcript fail-closed matrix, `tests/test_r02_runtime_closure.py`, `tests/test_r021_session_scoped_runtime.py`, `tests/test_stage_3b_api.py` | Product Store is authority; worker-local runtime is keyed by Session and rebuilt after cache loss or authorization-source change |
| AuditPipeline and existing source projection | Demo `cd62a96` | Included | Existing pipeline tests and import checks | No behavior rewrite in R0 |
| MediaCrawler acquisition | Source-only checkpoint `a77d8f4` from tracked HEAD `ec56ebf...` and adapter | Compatibility dependency, selectively checkpointed | Adapter command tests; checkpoint offline tests | Unsupported platform/API dirty changes remain excluded; see migration matrix |
| Frozen Post / Comment / Finding / Evidence contracts | Hermes `0a50905` reporting + selected `hermes_m0` domain | Included selectively | `tests/test_domain_*`; report graph tests | Hermes reporting imports `hermes_m0`; layering cleanup is M3 |
| R3.1 ReportGraph and immutable ReportStore | Hermes `backend/reporting/` | Included and formally routed | Candidate tests plus read-only published SQLite validation | Generation entry constructs `AccountOverviewReportGraph`; no Provider generation ran |
| Report API and frontend projection | Existing legacy API plus R3.1 structured endpoint | Included | stage 3b tests, schema fence, both frozen frontend builds | Published report versions remain immutable; frontend JSON does not parse Markdown |
| Hermes M1 report Q&A tools | Selected `hermes_m0` service/repository/report-task modules | Canonical formal Agent route | Real plugin discovery, AIAgent constructor, published-report offline navigation | Provider execution remains disabled in baseline acceptance |
| Hermes M2.2 Account drilldown | `account_corpus`, Account activity modules, R3.1 Account projection | Canonical future Agent route | Account fixture hash, non-Top5/risk parent-chain tests, R3.1 projection tests | No new Account A0/A1/A2 runtime integration; frozen questions/oracle are evidence-only |
| Account corpus and activity identity | `hermes_m0/account_corpus.py`, activity refs/repository/service | Included selectively | Frozen fixture checksum; deterministic query tests | Must remain read-only and scoped to authorized report |
| Dependency/configuration entry | `requirements.txt`, `requirements-hermes.txt`, `backend/hermes_runtime` | Included and verified | Python 3.12.10 clean environment; Hermes 0.20.4 non-editable wheel; `_api_max_retries=1`; `pip check` clean | Official commit remains declared; this run used a hash-verified cached wheel after GitHub fetch failed, and records that provenance limit |
| Legacy external contract facade | Existing FastAPI/SSE routes and `backend/investigation/` | Included, marked legacy | compatibility test report | Formal route instantiates `HermesInvestigationAgentService`; no new old-runtime feature work |

### Agent route decision

Hermes M1/M2.2 is the only future formal investigation Q&A Agent Runtime. The
old Controller, Planner, Focus/Dossier, Search Runtime, Finalizer and Account
A0/A1/A2 experiments are archival unless one of the five approved preservation
reasons is proven. R0 does not delete those sources and does not reorganize
the application/domain/agent/infrastructure directories; that is M3 work.

For pre-Hermes Agent experiments, the Matrix default question is whether the
current product or Hermes still has an irreplaceable dependency. If not, record
only archival and recovery instructions; do not perform source integration.
