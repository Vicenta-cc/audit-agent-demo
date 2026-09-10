# MediaCrawler R0.1 Source-Only Migration Matrix

The audited source worktree was inspected read-only before migration. Its
tracked HEAD was `ec56ebfe638dcfc8680f41711215ee9618a843e2`; its dirty status
and untracked manifest remain in the external preservation archive. Content
that could contain credentials was not copied or archived as a raw diff.

The product adapter invokes MediaCrawler through the CLI and currently supports
only `xhs`, `dy`, and `ks`. The evidence for the selected changes is the
candidate adapter command construction in
`backend/audit_agent/crawler_adapter.py`, the candidate execution-settings
tests, and the checkpoint's offline tests. No live browser, login, or platform
request was run.

| Source file | Source status / SHA-256 | Target / included hunks | Runtime dependency evidence | Inclusion reason |
|---|---|---|---|---|
| `base/base_crawler.py` | M / `d06f67678a4246440ae9064cd76dfdb426c8e0f2c0dc11c5e54c3c5a012d71b7` | same path; account state injection, invalid marker, semaphore-before-rate-slot context | Adapter sends `MEDIACRAWLER_ACCOUNT_AUTH_STATE_B64`; candidate tests assert typed auth failure and command isolation | Required auth, rate-limit ordering, and streaming-safe request contract |
| `cmd_arg/arg.py` | M / `c681b66aeb78713073685b938191edf299e9881232f0135f93a1f8a02c85301b` | same path; `--crawler_max_items_per_minute`, `--crawler_sleep_sec`, bounded concurrency parsing | Adapter command includes all three flags | CLI contract required by product adapter |
| `config/base_config.py` | M / `f5c6c4cf6dcc8a8fd72a7f1d52aa3a27cb01696f6f2a3d0019bffce07904ffae` | same path; bounded primary-content rate default | CLI option resolves to this default | Keeps rate contract deterministic and configurable |
| `media_platform/xhs/core.py` | M / `762322a3636cdc530825d555651a63b6fdf6f20930b7fe0aa2ff1aefd79d220f` | same path; injected state, content slots, stream item sleep, shared semaphore | Candidate supports `xhs`; adapter enables streaming and rate flags | Product-facing platform implementation |
| `media_platform/douyin/core.py` | M / `2e1417ad3df17734c6f1464647d3383c7c50207eb3772a40a9e934aeb6653f3f` | same path; injected state and content slots | Candidate supports `dy`; same adapter command contract | Product-facing platform implementation |
| `media_platform/kuaishou/core.py` | M / `0b289d6775c2d99466c10f260119f0f3023104b694cadf763020fdb6ad684738` | same path; injected state and content slots | Candidate supports `ks`; same adapter command contract | Product-facing platform implementation |
| `tools/crawl_rate_limiter.py` | ?? / `9420d8c6a4e6de16975a56697aaa5998149accc9220f20d25f2076953f57bac4` | same path; deterministic bounded scheduler | Imported by `base/base_crawler.py` | Shared rate-limit implementation |
| `tests/test_account_auth_state.py` | ?? / `129d97dfa058297b3f44fbf8606be37e8b5d2a3ebe1e795119eb0dfe4f2422b9` | same path; fake context and malformed-state tests | Exercises candidate-proven auth boundary without real cookies | Deterministic contract evidence only |
| `tests/test_crawl_rate_limiter.py` | ?? / `b36e4197e95300861b822d89cf471d0f100c55bc09015ab542900f8a2892223b` | same path; spacing, jitter, expiry, concurrency tests | Exercises the shared scheduler offline | Deterministic contract evidence only |

The source-only checkpoint is `a77d8f4ad99b692641711c8e170c73dc7ebdf627` on
branch `codex/mediacrawler-product-baseline-r0-1`. Its isolated venv ran the
two migrated test modules with `8 passed`.

## Explicit exclusions

`api/main.py`, `api/schemas/crawler.py`, and `api/services/crawler_manager.py`
were excluded because the product invokes the CLI runner, not MediaCrawler's
optional API server. `README.md` was documentation-only. The `bilibili`,
`tieba`, `weibo`, and `zhihu` dirty platform changes were excluded because the
candidate adapter's supported platform set is `xhs/dy/ks`.

The original dirty worktree's cache, browser state, credentials, collection
results, SQLite files, logs, and absolute-path configuration were not copied.
Any future inclusion requires a new per-file proof and checkpoint; no
uncertain dirty change is treated as a product dependency by inference.
