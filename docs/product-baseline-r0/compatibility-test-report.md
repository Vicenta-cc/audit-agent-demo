# R0 Compatibility Test Report

- Python backend: 213 selected deterministic tests passed; 4 real-data tests
  skipped by their existing `skipUnless`/availability guards.
- Hermes offline smoke: M0 fixture tools, M1 report navigation, and M2.2
  Account corpus overview/occurrence projection passed from candidate-local
  fixtures and temporary ledgers.
- Frontend: `pnpm install --frozen-lockfile` passed and `pnpm build` passed
  (TypeScript plus Vite production bundle). Vite emitted only the existing
  large-chunk advisory.
- Hermes package bridge: `hermes_m0` imports, both copied fixtures load, and
  the plugin manifest parses. The external `hermes-agent==0.20.4` package is
  intentionally not installed in this run; the adapter failed closed with the
  expected `HermesRuntimeUnavailable` message.
- MediaCrawler: no live process was started. The adapter remains an external
  compatibility boundary and is blocked on a reproducible source artifact.
- Absolute path scan: no audited Hermes/MediaCrawler worktree path or foreign
  Windows default path appears in candidate runtime source.

## Acceptance status

The product shell, reporting, Account fixtures and deterministic compatibility
surface are green. The R0 candidate is **not accepted/tagged** because the
external Hermes install/commit verification and dirty MediaCrawler dependency
remain explicit clean-room blockers.
