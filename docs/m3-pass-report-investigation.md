# Published pass report investigation

The pass report adapter extends the M1/M2 query surface for published reports whose verified `report_document.template_kind` is `all_pass`. It does not change the canonical A/B tool schema, prompt, stored report projections, or legacy account corpus.

## Sources and boundaries

- Every post, audit finding, comment, caption and transcript comes from the current report's verified published snapshot. Existing snapshot, payload, relation and report hashes are checked before exposing data. Missing report-level risk findings are allowed only for all-pass reports; non-pass post results or completed risk comments are rejected.
- `read_report` provides current-report counts, post navigation and author account entries. `search_posts` includes `pass` / `none` candidates and supports verified post context and durable paging. Search is bounded candidate discovery; the model must assess relevance from returned previews.
- `read_posts` uses the existing audit finding and detail path after a pass-specific provenance check. Original caption, original ASR and its stored translation remain separate. Missing material is not invented and no new audit is performed during Q&A.
- `list_post_comments` pages all stored comments and their own audit states. A pending or missing audit is never classified as safe. Stable comment-author identities expose account navigation; nicknames never serve as identity keys.
- M2 reuses account overview and activity navigation. `normal_only` matches completed, non-risk activity; `risk_only` retains the existing semantics. Current-report activities can expose a verified original-post reference for M1 detail access.
- Account overview uses explicitly authorized sources, while report cards remain local to the current investigation. M3 pass handoffs add the server-configured authorized legacy report sources through their existing corpus. This is not unrestricted database discovery and does not change A/B report navigation.
- Per-turn process isolation, per-session durable reference checkpoints, generation checks, cursor scoping and idempotent execution remain in effect. The new tool is included in the same execution ledger middleware.

## Verification

`tests/test_pass_investigation.py` covers the published snapshot path, pass search, original/translated ASR, normal and pending comments, comment-account navigation, activity filters, original-post links, durable paging, cross-session reference rejection, authorized multi-report account scope and catalog isolation. The existing M1/M2 and concurrent-session suites remain regression gates.

The frontend only normalizes known query implementation terms in assistant prose and inline labels. It preserves the raw answer, source quotations, code blocks and link targets.

Real acceptance must additionally generate a new report through creation, crawl and audit, then test its questions alongside A/B. Provider first textual delta and final-answer availability are separate measurements; the current UI does not progressively stream report answer text.

## Failed comment coverage

An incomplete comment audit may have a missing, unknown or unavailable risk level. Report generation and published-report loading normalize only those missing verdicts to `unavailable`, preserving `failed`, `pending` or `queued` status. Completed audits still require a valid risk verdict, arbitrary invalid values still fail validation, and existing risk verdicts are never suppressed. Original audit results and failure reasons remain unchanged in the source record.

Pass question tools expose full snapshot comment coverage with mutually exclusive completed, failed, pending and unknown counts. These counts describe stored comments, not the platform total. Failed comments remain accessible and are excluded from normal account activity. Report presentation retains the existing incomplete-audit qualification.

The September 9 real sample published with 300 stored comments: 299 completed and one failed. Recovery used the existing R3.1 checkpoint resume operation; the original interrupted investigation was not restored, per user direction. Its published report was bound directly to real Hermes report sessions for Qwen testing. This verifies report question capability, not recovery of the original workspace UI.

Real Qwen acceptance completed 13 consecutive questions on that report and two additional concurrent rounds alongside A/B. Initial coverage and account-scope interpretation errors prompted richer tool coverage and prompt constraints. Functional availability passed these finite runs; raw answer quality still has internal terminology and incomplete final sentences, so full answer-quality acceptance remains open. Artifacts and per-question measurements are in the external `m3-pass-m1-m2-20260909` acceptance directory.


## Single risk post routing

Published `single_risk_post` snapshots now bind to the existing `ReportTaskInvestigationToolService` and account-activity catalog/prompt. They do not use pass search or pass-only tools. After verifying the original content, payload and relation hashes, the loader exposes the sole review/reject risk post as an in-memory standalone risk entry; it neither invents a grouped investigation finding nor writes a replacement report. Overview and content are read from the single-post template's frozen snapshot. Account occurrences use that snapshot as well, including comment risk types needed by the existing risk-comment consistency check. Legacy A/B loading remains unchanged.

The real interrupted question about the high-risk post was resumed successfully against report-version:349e703fce204eeaa4af9fd1d3f9298e. Regression tests cover binding to the exact existing risk service, post verdict/content, risk comments, account overview, and unchanged source database bytes.
