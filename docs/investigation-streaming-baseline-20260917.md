# Investigation streaming baseline (2026-09-17)

## Frozen starting point

- Source commit: `71b29b891737778d7f094e3f928c34f4c780b9ba`.
- Source tag: `douyin-m3-unified-report-appendix-acceptance-20260917`.
- Development branch: `codex/investigation-streaming-activity`.
- Development worktree: `/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-investigation-streaming-activity`.

The streaming work is additive. With all three rollout switches disabled, the
accepted answer, tool execution, persistence, authorization, and UI behavior
remain the rollback path.

## Rollout switches

All switches default to `false` and are exposed read-only by `/api/config`:

| Environment variable | Runtime field | Scope |
| --- | --- | --- |
| `INVESTIGATION_ACTIVITY_STREAM_ENABLED` | `activity_stream_enabled` | Public tool activity events and timeline |
| `INVESTIGATION_ANSWER_STREAM_ENABLED` | `answer_stream_enabled` | Report A/B/C and unified-report answer deltas |
| `INVESTIGATION_CREATION_ANSWER_STREAM_ENABLED` | `creation_answer_stream_enabled` | Task/resource creation answer deltas after server-side filtering |

No switch is consumed by the execution or UI paths in this baseline commit.
Adding the configuration surface therefore cannot change current behavior.

## Behavior that must not regress

### Report A/B/C

- Preserve the authorized frozen report and Session boundary.
- Preserve risk-only post and comment scope where the report contract requires it.
- Preserve evidence listing and evidence reads.
- Preserve account search, overview, occurrences, occurrence detail, and account post reads.
- Do not grant access to pass posts or another report merely because an activity is displayed.
- Keep the final persisted assistant answer authoritative and free of internal account references.

### Unified reports

- Preserve full post access, including pass posts, and full saved comment access.
- Preserve authorized cross-report account comparison.
- Preserve report-version, snapshot, and account authorization boundaries.
- Preserve the current report, account association, and appendix presentation.

### Investigation creation and resource management

- Preserve option and published resource queries.
- Preserve ruleset proposal create/read/update/adopt semantics.
- Preserve lexicon edit create/read/update semantics.
- Generation and editing must not be presented as a formal save.
- Preserve real save receipts, version conflict handling, and idempotency.
- Preserve investigation Draft create/read/update semantics.
- Draft creation must not be presented as starting a Run.
- Only a successful `confirm_and_queue_investigation` receipt may be presented as started.
- Preserve Run status reads and the one-published-Run-per-creation-Session boundary.

## Streaming safety invariants

- Existing `completed.answer` remains the canonical answer and replaces any provisional draft.
- Activity events are projections of real tool callbacks; model prose cannot mark an action successful.
- Public events never contain raw tool arguments/results, tool call IDs, database IDs, hashes, stack traces, or model reasoning.
- Unknown, malformed, duplicate, or disabled streaming events do not execute a tool or mutate business state.
- Disconnect and replay do not repeat tool execution, resource saves, or investigation starts.
- A streaming failure falls back to the existing stage/final-answer path.
- Events are buffered; the store must not receive one row per model token.
- Refresh recovery must restore or replay the provisional draft before advancing its sequence cursor.

## Baseline verification

The initial run with the system Python was invalid because that interpreter did
not contain OpenCV or Hermes. The controlled rerun used the Python recorded by
the frozen formal runtime and immutable A/B archives.

- Broad backend run: `1371 passed, 30 skipped, 8 failed, 67 subtests passed`.
- The eight failures were environmental: two old historical-report assertions
  were run with Report C injected even though they explicitly expect A/B only;
  six scheduler/subprocess cases used the repository placeholder
  `external/MediaCrawler` instead of the pinned acceptance crawler.
- The focused rerun uses only A/B for the historical suite and pins
  `MEDIACRAWLER_DIR` plus its Python to the recorded Douyin acceptance runtime.
  All eight affected cases plus the other historical cases passed: `18 passed`.
- The new feature-flag tests and the existing SSE replay, process isolation,
  unified-report redaction, and creation non-start regression gate passed:
  `11 passed` across the two focused runs.
- `Audit_assistant` TypeScript checking and the Vite production build passed
  with the controller-recorded Node `v24.19.0`; Vite retained its existing
  large-chunk warning.
- Final clean-environment backend gate after the Phase 1 changes:
  `1387 passed, 28 skipped, 67 subtests passed` in 219.28 seconds. The only
  output was five pre-existing dependency/lifespan deprecation warnings.

The existing Douyin acceptance controller remains read-only reference
infrastructure. This branch does not change or restart the 8027 or 3198/8198
runtimes.

## Phase gates

1. Default-off configuration and this regression contract.
2. Backward-compatible durable public SSE event protocol.
3. Allowlisted public activity projector for all report/creation/resource tools.
4. Shared expandable activity timeline in the investigation UI.
5. Filtered, buffered report answer deltas with reset/revision semantics.
6. Server-side creation-answer filtering followed by creation answer deltas.
7. Replay, refresh, idempotency, performance, and failure-injection hardening.
8. Feature-flagged acceptance in the order: activity, unified report answers,
   A/B/C answers, creation answers.
