# MediaCrawler Dependency Boundary

The product retains the existing `MediaCrawlerAdapter` as a compatibility
facade. R0.1 selectively records a source-only checkpoint; it does not rewrite
the crawler or modify the original dirty worktree.

- Audited source: `/Users/ext.wanghongtao6/Documents/Codex/projects/MediaCrawler`
- Recorded tracked HEAD: `ec56ebfe638dcfc8680f41711215ee9618a843e2`
- Source-only checkpoint: branch `codex/mediacrawler-product-baseline-r0-1`,
  commit `a77d8f4ad99b692641711c8e170c73dc7ebdf627`
- Preservation: `MediaCrawler-refs.bundle` and tracked/untracked diff manifests
  in `/Users/ext.wanghongtao6/Documents/Codex/archives/product-baseline-r0-20260826`
- Candidate configuration: `MEDIACRAWLER_DIR` and `CRAWLER_LOGIN_PYTHON`
  environment variables; no foreign absolute path is used by acceptance tests.
- Credentials: auth state remains environment/key-file based and is not copied.

## R0.1 checkpoint condition

The product-required account-auth, rate-limit, concurrency and streaming CLI
contracts are now available from the reproducible source-only checkpoint. Its
offline tests passed in an isolated checkpoint environment. This does not
authorize live collection or login. The remaining dirty API and unsupported
platform changes are explicitly excluded; they are not blockers unless a
future product contract proves a dependency on them.
