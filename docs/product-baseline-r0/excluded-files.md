# Product Baseline Consolidation R0: Excluded Files

## Pre-Hermes Agent experiments

The following are archival candidates, not Product Baseline runtime sources:

- `backend/investigation_domain/`
- `backend/investigation_runtime/`
- `docs/phase-*`, `docs/source-orchestrator-control-contract.md`, and the
  pre-Hermes architecture/evolution notes
- `experiments/` and `experiments/results/`
- v2/v3 investigation controller, planner, selector, discovery, aggregate and
  post-centered test files and their fixtures
- legacy Account A0/A1/A2 worktree implementations

They remain recoverable through the Preservation Gate bundles, archive manifest,
and recovery instructions. A unique commit is not an inclusion reason.

## Evidence and secrets

- Real report SQLite files, raw traces, provider exchanges, live output trees,
  `auth.json`, API keys, cookies, login state and foreign virtual environments
  are not copied into the candidate.
- The two R3.1 report artifact directories are registered as immutable,
  read-only evidence in the archive manifest only.
- Held-out M1/M2.2 questions, oracle, runner and manual-audit files are
  registered as evidence only; they are not copied into runtime source.

## External MediaCrawler

The dirty MediaCrawler worktree is not copied. Its required changes are
recorded in `docs/product-baseline-r0/media-crawler-dependency.md` and must be
made reproducible before acceptance. The product continues to call it only
through the existing `MediaCrawlerAdapter` compatibility boundary.
