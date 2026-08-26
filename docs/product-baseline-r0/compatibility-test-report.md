# R0.1 Compatibility Test Report

- Backend deterministic contracts: `220` tests passed; `1` real-data test
  skipped by their existing `skipUnless`/availability guards. The selected
  suite includes M1 Report/Finding/Post/Evidence, M2.2 Account bridge and risk
  navigation, recovery/replay, and R3.1 projection contracts.
- Reporting import boundary: `4 passed` under the candidate pytest environment.
- R3.1 published evidence: the archive-registered published SQLite and
  `frontend-report.json` were opened read-only; the database exposed one or
  more `published` ReportVersion rows and the public projection loaded. These
  files remain evidence outside the candidate source tree.
- Backend import/startup: `backend.main` imported successfully; a no-provider
  Uvicorn startup reached `Application startup complete`; `/api/config` and
  `/api/tasks/{task_id}/report-versions` returned the stable compatibility
  shapes. No provider, crawler, login, or production database was invoked.
- Frontend: `pnpm install --frozen-lockfile` and `pnpm build` passed (TypeScript
  plus Vite production bundle). Vite emitted only the existing large-chunk
  advisory.
- Candidate Hermes adapter/plugin: local manifest/import and fail-closed smoke
  passed without the external package. The required exact Hermes clean-room
  fetch/install is **blocked**: the default transport failed with an HTTP/2
  framing error and an HTTP/1.1 retry failed to connect, both before any
  object was fetched. A prior inability to enumerate the SHA with
  `git ls-remote` is not treated as proof that the commit is absent.
- MediaCrawler: source-only checkpoint `a77d8f4` was created from tracked HEAD;
  its isolated auth/rate tests were `8 passed`. No live process, login, or
  platform request was started.
- Runtime secret/path scan: no literal credential token/private-key pattern,
  audited external worktree import, or foreign Windows default path was found
  in runtime source. Historical README/examples and SQL evidence retain
  expected absolute-path strings; these are documented scan exclusions, not
  runtime dependencies.

## Acceptance status

The product shell, reporting, Account fixtures, deterministic compatibility
surface, and MediaCrawler checkpoint are green. The R0.1 candidate is
**not accepted/tagged** because exact Hermes source acquisition, installation,
key-file hashing, and plugin discovery remain an unclosed clean-room gate.
