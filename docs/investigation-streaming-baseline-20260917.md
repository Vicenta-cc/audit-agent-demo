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

## Phase 2 durable public event protocol

Phase 2 adds three display-only event types alongside the existing `turn`
event. It does not connect Hermes/Qwen callbacks, project tool calls, or render
new UI:

| SSE event | Public payload | Purpose |
| --- | --- | --- |
| `activity` | hashed public activity ID, status, label, safe summary, optional count | Expandable business activity |
| `answer_delta` | hashed public answer ID, revision, buffered text delta | Provisional typewriter answer |
| `answer_reset` | hashed public answer ID and new revision | Discard a provisional revision before replacement |

All four event types share the existing per-Turn monotonic sequence and
`Last-Event-ID` replay path. Existing databases are migrated in place: legacy
rows default to `event_type=turn`, and their IDs, sequence numbers, and replay
behavior are retained.

The extension writer requires strict allowlisted payloads and hashed public
identifiers. Raw tool arguments/results, tool-call IDs, database IDs, and
unknown fields are rejected before persistence. Event retries are idempotent:
the same key and payload return the original row even after the Turn becomes
terminal, while a conflicting payload is rejected. A new display event can
only be appended while the Turn is running.

Only `turn` events can terminate SSE or determine the status projection.
Activity and provisional-answer events therefore cannot complete, fail,
interrupt, resume, save, or start anything. The frontend accepts the new named
events through optional callbacks, ignores malformed or replayed sequences,
and retains the existing polling fallback. The final persisted
`completed.answer` remains authoritative.

Phase 2 verification used the controller-recorded Python and Node runtimes,
the immutable A/B archives, and the pinned Douyin acceptance crawler:

- Full backend gate: `1392 passed, 28 skipped, 67 subtests passed` in 188.03s.
- Focused protocol/creation/recovery gate: `101 passed, 12 subtests passed`.
- Historical A/B gate: `11 passed`.
- `Audit_assistant` TypeScript checking and Vite production build passed; the
  existing large-chunk warning remains.
- No runtime was started or restarted, and all rollout switches remain off.

## Phase 3 allowlisted public activity projection

Phase 3 connects Hermes tool lifecycle callbacks to the durable `activity`
event without changing tool schemas, arguments, results, retry configuration,
mutation receipts, or execution order. The projector covers every tool in the
published report A/B/C, pass-report, unified-report, task creation, and resource
management catalogs through one explicit allowlist.

Each public activity contains only a hashed activity ID and fixed product copy.
The projector never persists callback arguments, callback results, Hermes tool
call IDs, database IDs, hashes, stack traces, or model reasoning. Unknown tools
are ignored. A success state is emitted only after the canonical tool callback
returns an explicit success envelope; an unfinished callback is closed as
`interrupted` when the Turn exits.

The projection is fail-open. Callback or persistence failures are logged and
cannot fail the underlying tool or Turn. Ambiguous concurrent Turn bindings are
suppressed instead of being attached to the wrong Turn. With the activity flag
off, agents are constructed exactly as before and no activity row is written.

The deterministic creation runtime uses the same callback boundary. Its
integration gate verifies that enabling activities and replaying the same
`client_message_id` still creates exactly one Draft, starts no Run, and emits no
duplicate activity. The existing Hermes provider retry fence remains unchanged
at `CANONICAL_API_MAX_RETRIES = 1`.

Phase 3 verification used the frozen runtime paths and did not start or restart
any service:

- Activity projector and creation replay gate: `7 passed`.
- A/B/C, unified-report, creation, process-isolation, and session-runtime gate:
  `118 passed, 12 subtests passed`.
- Full clean-environment backend gate: `1399 passed, 28 skipped, 67 subtests
  passed` in 189.64 seconds.
- The only output was five pre-existing dependency/lifespan deprecation
  warnings; all rollout switches remain default-off.

## Phase gates

1. Default-off configuration and this regression contract. **Complete.**
2. Backward-compatible durable public SSE event protocol. **Complete.**
3. Allowlisted public activity projector for all report/creation/resource tools.
   **Complete.**
4. Shared expandable activity timeline in the investigation UI.
5. Filtered, buffered report answer deltas with reset/revision semantics.
6. Server-side creation-answer filtering followed by creation answer deltas.
7. Replay, refresh, idempotency, performance, and failure-injection hardening.
8. Feature-flagged acceptance in the order: activity, unified report answers,
   A/B/C answers, creation answers.
