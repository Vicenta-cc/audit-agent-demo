# Product Baseline R0 Capability Traceability Matrix

The default question for a pre-Hermes experiment is **whether the current
product or Hermes has an irreplaceable dependency**, not which code should be
merged. If the answer is no, record its archival and recovery path only; do
not perform source integration.

| Capability | Canonical source | Status in R0 | Evidence / deterministic check | Open condition |
|---|---|---|---|---|
| Product shell and reviewed investigation UI | Demo `cd62a96` plus reviewed dirty files | Included | `pnpm build`, TypeScript check | UI remains product shell, not Agent authority |
| FastAPI job and crawler APIs | Demo `cd62a96` plus `backend/main.py`/`backend/api` | Included as compatibility | `tests/test_stage_3b_api.py`; route import check | External API must remain stable |
| Investigation session, turn, replay and SSE | Demo `backend/investigation/` and `backend/api/` | Legacy compatibility only | `tests/test_investigation_agent.py`, artifacts, stage 3b | Hermes/Application Layer replacement is recorded for M3 |
| AuditPipeline and existing source projection | Demo `cd62a96` | Included | Existing pipeline tests and import checks | No behavior rewrite in R0 |
| MediaCrawler acquisition | Source-only checkpoint `a77d8f4` from tracked HEAD `ec56ebf...` and adapter | Compatibility dependency, selectively checkpointed | Adapter command tests; checkpoint offline tests | Unsupported platform/API dirty changes remain excluded; see migration matrix |
| Frozen Post / Comment / Finding / Evidence contracts | Hermes `0a50905` reporting + selected `hermes_m0` domain | Included selectively | `tests/test_domain_*`; report graph tests | Hermes reporting imports `hermes_m0`; layering cleanup is M3 |
| R3.1 ReportGraph and immutable ReportStore | Hermes `backend/reporting/` | Included | `tests/test_report_graph.py`, reporting import boundary | Real-data acceptance remains evidence-only |
| Report API and frontend projection | Demo API/UI plus Hermes R3.1 reporting | Included | stage 3b tests, frontend build | Published report versions remain immutable |
| Hermes M1 report Q&A tools | Selected `hermes_m0` service/repository/report-task modules | Canonical future Agent route | Fixture load and plugin manifest checks | Pinned external `hermes-agent` install must pass clean-room gate |
| Hermes M2.2 Account drilldown | `account_corpus`, Account activity modules, R3.1 Account projection | Canonical future Agent route | Account fixture hash, non-Top5/risk parent-chain tests, R3.1 projection tests | No new Account A0/A1/A2 runtime integration; frozen questions/oracle are evidence-only |
| Account corpus and activity identity | `hermes_m0/account_corpus.py`, activity refs/repository/service | Included selectively | Frozen fixture checksum; deterministic query tests | Must remain read-only and scoped to authorized report |
| Dependency/configuration entry | `requirements.txt`, `requirements-hermes.txt`, `backend/hermes_runtime` | Included | preflight and import checks | Hermes version/commit availability is a gate |
| Legacy external contract facade | Existing FastAPI/SSE routes and `backend/investigation/` | Included, marked legacy | compatibility test report | No new feature work on old runtime |

### Agent route decision

Hermes M1/M2.2 is the only future formal investigation Q&A Agent Runtime. The
old Controller, Planner, Focus/Dossier, Search Runtime, Finalizer and Account
A0/A1/A2 experiments are archival unless one of the five approved preservation
reasons is proven. R0 does not delete those sources and does not reorganize
the application/domain/agent/infrastructure directories; that is M3 work.

For pre-Hermes Agent experiments, the Matrix default question is whether the
current product or Hermes still has an irreplaceable dependency. If not, record
only archival and recovery instructions; do not perform source integration.
