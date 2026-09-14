# V2 Phase 2 collection validation (2026-09-14)

This phase is implemented only in the new integration worktrees. The formal
runtime and its SQLite database were not changed.

## Delivered

- Jobs persist `crawl_checkpoint_page` and `crawl_checkpoint_keyword`; the
  frozen `start_page` remains unchanged, so a new task still starts from its
  own configured first page.
- Shared content rows retain an immutable, validated raw payload for complete
  collection results. A later task gets a `task_contents` reference and its
  own audit queue without rewriting the shared payload.
- `reusable_content_keys()` uses the indexed `(platform, content_key)` lookup
  and fails closed when the raw JSON is missing, malformed, or has a mismatched
  identity.
- MediaCrawler accepts `--reusable_content_db` and checks the current search
  page's IDs against complete Douyin rows before detail, comment, or media
  calls. An optional newline ID file remains available for isolated runs.
- The adapter persists the page boundary reported by MediaCrawler logs and
  resumes the same task from that boundary.

## Validation

Application tests:

```text
tests/test_phase2_collection_resume.py: 2 passed
tests/test_comment_alias_and_failure_isolation.py: passed
tests/test_analyze_limit_ingestion.py: 23 passed, 1 pre-existing failure
```

The existing failure is
`test_authoritative_provider_failure_prevents_completed_result_persistence`
and reproduces on the Phase 1 application baseline before these changes.

MediaCrawler tests:

```text
tests/test_douyin_pagination_dedupe.py: 5 passed
```

The database lookup is deliberately local and indexed; it does not load the
historical ID set into the application process.
