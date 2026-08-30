# Historical Report Demo QA Acceptance Evidence

## Baseline and provenance

- Accepted baseline: `c47f5bfa014ad6d14dc2e8e345fd1ec5eae9970e`
- Baseline parent: `b8533e48f38a6a0f970945546d36bdead2281ba6`
- Implementation branch: `codex/historical-report-demo-qa`
- Gate result: M1 PASS, M2 PASS, Overall PASS
- Canonical corpus revision: `6853831242a9b59ced78ba80ee9c17e03058df239fa063ed90a4e672f883897f`
- Report A archive SHA-256: `f70d1b9fb6cd85471d3f9e8e3bc2c0d89f390329a6c5a02449d4a3c506b46560`
- Report B archive SHA-256: `2de176629ac0cd2d06588bc436786dc45819555ec48bc2cad59a84207c8793bf`

The importer verifies the archive SHA-256 plus the published ReportVersion content hash and FrozenSnapshot hash before copying only immutable report tables. It never imports or creates generation runs, provider exchanges, Jobs, Worker state, crawler state, AuditPipeline state, or ReportGraph state.

## Implemented contracts

- Two explicit, idempotent historical workspace/run registrations use `PUBLISHED` and `historical` semantics.
- Listing, opening, refreshing, and reading either report creates zero internal report Sessions.
- The first real question creates exactly one anchored internal Session per workspace. The first Hermes conversation history is empty; subsequent turns restore only completed real Hermes transcript messages.
- The public workspace, Turn, SSE, and report APIs expose no internal Session identifier.
- Replaying the same `client_message_id` with identical content reuses the Turn. Reusing it with different content returns HTTP 409 before Agent or Tool execution.
- Display Timeline messages and the report card are frontend presentation data and never enter the Hermes transcript.
- M1 remains bound to the workspace's single ReportVersion/FrozenSnapshot. M2 additional report contexts are available only to `historical-report:` anchored sessions and are limited to Reports A+B; ordinary and M3 report sessions receive none.
- Browser pending recovery stores exactly `client_message_id`, `turn_id`, and `after_sequence` in `sessionStorage`.
- The accepted R3.1 report UI files were not modified. Historical data is projected through the existing server presentation contract.
- The existing M3 flow retains explicit Draft/confirmation separation and its formal `max_notes = 1` execution contract. No parallel workflow or hardcoded collection result was added.

## Deterministic verification

- Backend: `470 passed, 17 skipped, 67 subtests passed`.
- Frontend Playwright: `27 passed, 2 skipped`.
- TypeScript and production Vite build: passed; only the existing bundle-size warning remains.
- Python compileall: passed.
- `git diff --check`: passed.
- Browser QA: both workspace timelines and report cards rendered; direct report navigation worked; reads kept Session count at zero; A and B first questions created one Session each; refresh restored one copy of each completed exchange; A conversation was absent from B; desktop and 390x844 mobile layouts had no incoherent overlap; no browser errors were recorded. Existing React Router v7 migration warnings remain.

## Real Qwen smoke

The Gate-frozen eight questions ran against `qwen3.7-plus` after deterministic verification. All A1-A4 and B1-B4 Turns completed and passed grounding validation. Each workspace produced a real Tool trace; later questions were allowed to reuse the completed Hermes history without deleting any frozen question.

- Internal Sessions after the smoke: A=1, B=1.
- `report_generation_runs`: before=0, after=0.
- `report_provider_exchanges`: before=0, after=0.
- Sanitized trace scan: no Session IDs, stable Account keys, source namespaces, corpus/snapshot revisions, ReportVersion IDs, receipt fingerprints, credentials, prompts, or filesystem paths.
- Detailed sanitized answers and Tool names: `docs/evidence/historical-report-qwen-smoke.json`.

## Independent Review Gate

The review covered import provenance and side effects, Principal/workspace authorization, M1/M2 scope, Turn idempotency, SSE recovery, browser storage, frontend routing, public payload leakage, and M3 non-regression.

One P2 finding was identified and fixed: A+B additional Hermes contexts were initially configured on the shared report service without an anchor restriction, which could have made them available to an M3 report session. The service now applies those contexts only to `historical-report:` anchors, with a deterministic regression test proving an unscoped report session receives none. No P0-P3 findings remain open.
