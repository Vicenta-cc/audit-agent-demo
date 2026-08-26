# MediaCrawler Dependency Boundary

The product retains the existing `MediaCrawlerAdapter` as a compatibility
facade. R0 does not merge the dirty external MediaCrawler worktree or rewrite
the crawler.

- Audited source: `/Users/ext.wanghongtao6/Documents/Codex/projects/MediaCrawler`
- Recorded HEAD: `ec56ebfe638dcfc8680f41711215ee9618a843e2`
- Preservation: `MediaCrawler-refs.bundle` and tracked/untracked diff manifests
  in `/Users/ext.wanghongtao6/Documents/Codex/archives/product-baseline-r0-20260826`
- Candidate configuration: `MEDIACRAWLER_DIR` and `CRAWLER_LOGIN_PYTHON`
  environment variables; no foreign absolute path is used by acceptance tests.
- Credentials: auth state remains environment/key-file based and is not copied.

## R0 acceptance condition

Clean-room acceptance is blocked until the required external changes for
account auth, rate limiting and streaming are available from a reproducible
commit or release artifact. Until then, the adapter and API contract may be
tested with mocked subprocess boundaries only. No new feature work may land in
the old crawler runtime under the legacy compatibility exception.
