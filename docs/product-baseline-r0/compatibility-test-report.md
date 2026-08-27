# R0.2 Compatibility Test Report

- Backend deterministic contracts: `304 passed`, `4` existing conditional
  skips, `13` subtests passed. The R0.2 focused closure set is included.
- Backend import/startup: `backend.main` instantiates
  `HermesInvestigationAgentService`; five stable report/session/turn/SSE routes
  were present; `/api/config` returned 200 and a missing structured report
  returned 404. No Provider or crawler was invoked.
- R3.1 published evidence: SHA-fenced SQLite loaded read-only; a temporary copy
  proved `ReportStore` restart equality, 14 ordered sections, zero-standalone
  wording, a 1345-entry full Account index, safe pagination, and legacy
  `human-report-v1` compatibility.
- Frontend: both frozen installs and production builds passed. Installs reused
  the local package store and downloaded zero packages. The legacy UI emitted
  only its existing large-chunk advisory.
- Hermes adapter/plugin: real 0.20.4 discovery enabled the project plugin,
  registered the exact 11-tool M2.2 catalog, and constructed
  `run_agent.AIAgent` with the canonical prompt. No Provider call was made.
- MediaCrawler: source-only checkpoint `a77d8f4` was created from tracked HEAD;
  its isolated auth/rate tests were `8 passed`. No live process, login, or
  platform request was started.
- Runtime secret/path scan: no literal credential token/private-key pattern,
  audited external worktree import, or foreign Windows default path was found
  in runtime source. Historical README/examples and SQL evidence retain
  expected absolute-path strings; these are documented scan exclusions, not
  runtime dependencies.

## Acceptance status

All R0.2 runtime and compatibility gates pass. Final acceptance remains
conditional only on the final committed-tip scan, clean worktree check, and
annotated tag creation recorded after these reports are committed.
