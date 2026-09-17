# Investigation streaming baseline (2026-09-17)

## Frozen starting point

- Source commit: `71b29b891737778d7f094e3f928c34f4c780b9ba`.
- Source tag: `douyin-m3-unified-report-appendix-acceptance-20260917`.
- Development branch: `codex/investigation-streaming-activity`.
- Development worktree: `/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-investigation-streaming-activity`.
- The source commit descends from deployment UI commit
  `9788b62370b82579a52fa49052aa5d1e56627dbb`; the product frontend modified by
  this work is `Audit_assistant`, including its collection and analysis
  settings, rather than the older 8027 `frontend-v2`.

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

## Phase 4 shared expandable activity timeline

Phase 4 consumes the optional `onActivity` callback in both published-report
questions and investigation/resource creation conversations. Events with the
same hashed public activity ID update one visible row in first-seen order, so a
real tool moves from `running` to its terminal status without duplicate rows.

While a Turn is running, the timeline is expanded and retains the existing
public Turn-stage text below the real tool rows. When the Turn finishes, the
same safe activity list is stored next to that answer as a collapsed,
user-expandable conversation item. This applies to report A/B/C, pass and
unified reports, task configuration, ruleset/lexicon work, save operations,
confirmation, and Run status reads through the shared backend allowlist.

The UI does not infer completion from assistant prose. It renders only validated
public events already accepted by the shared SSE client. When the feature flag
is off or no activity has arrived, the previous report/creation pending status
is rendered unchanged. Retry and resume keep the accumulated public rows, and
the final answer and artifact recovery paths remain authoritative.

Phase 4 verification used the controller-recorded Node `v24.19.0`:

- TypeScript checking passed.
- Four focused activity tests passed, including browser rendering, live-region
  semantics, collapse behavior, sequence replacement, and answer ordering.
- The Vite production build passed. The existing large-chunk warning remains.
- The broader frontend run reached `87 passed, 2 skipped`; its four failures
  are pre-existing contract expectations in unchanged code. Both mixed-rule
  browser failures were reproduced unchanged at Phase 3 commit `64d225b`.
- No formal or acceptance runtime was started or restarted. The activity flag
  remains default-off.

## Phase gates

1. Default-off configuration and this regression contract. **Complete.**
2. Backward-compatible durable public SSE event protocol. **Complete.**
3. Allowlisted public activity projector for all report/creation/resource tools.
   **Complete.**
4. Shared expandable activity timeline in the investigation UI. **Frozen and accepted.**
5. Filtered, buffered report answer deltas with reset/revision semantics.
   **Frozen and accepted.**
6. Server-side creation-answer filtering followed by creation answer deltas.
   **Frozen and accepted.**
7. Replay, refresh, idempotency, performance, and failure-injection hardening.
   **Stable candidate; deterministic gate complete.**
8. Feature-flagged acceptance in the order: activity, unified report answers,
   A/B/C answers, creation answers.

## Phase 4 frozen acceptance follow-up

The final Phase 4 acceptance was run from source revision `3b8dd6a` after the
two immutable historical archives were recovered from the formal frozen
backup. The archives were used as read-only test inputs; neither the formal nor
acceptance runtime was started, restarted, or written by this run.

- Report A SHA-256:
  `f70d1b9fb6cd85471d3f9e8e3bc2c0d89f390329a6c5a02449d4a3c506b46560`.
- Report B SHA-256:
  `2de176629ac0cd2d06588bc436786dc45819555ec48bc2cad59a84207c8793bf`.
- Both archives passed SQLite `PRAGMA quick_check`.
- Historical report and deletion/restart gate: `16 passed`.
- Activity projection, SSE/flag, Stage 3B API, and creation gate:
  `92 passed`.
- Focused frontend activity and historical recovery gate: `9 passed`.
- TypeScript checking and the Vite production build passed with Node
  `v24.19.0`; the existing large-chunk warning remains.
- Full clean-environment backend gate: `1402 passed, 28 skipped, 67 subtests
  passed` in 243.69 seconds. The only warnings were the five pre-existing
  dependency/lifespan deprecation warnings.

The untracked Phase 5 prototype `backend/investigation/public_answer.py` was
excluded from every commit and remained unchanged during acceptance (SHA-256
`526b6f9804b2ccb185334c6ee637e00210bcda214f723c425c9d4f7f5990de5a`,
mtime `2026-09-17T16:03:31+0800`).

Phase 4 is therefore the frozen functional baseline for Phase 5. Future
non-blocking hardening should add bounded or lazy historical activity recovery
and observability for terminal-time `running` backfill; neither changes the
accepted Phase 4 protocol or behavior.

## Phase 5 report answer typewriter stream

Phase 5 connects the existing Hermes/Qwen `stream_delta_callback` to the
durable public SSE protocol for report A/B/C and unified reports. It composes
the answer observer with the Phase 3 activity observer, so the real tool
lifecycle and provisional answer use the same ordered Turn sequence without
changing tool definitions, arguments, execution count, provider retry limits,
grounding retry behavior, mutation receipts, or final-answer persistence.

The public draft is deliberately not a second answer source. Model text is
sanitized cumulatively on the server, buffered before persistence, and emitted
as revisioned `answer_delta` events. A tool boundary, a later model iteration,
an interruption, or a process-resume boundary emits `answer_reset` before a new
draft can replace the old one. The canonical, server-cleaned answer is
reconciled into the current revision immediately before the Turn is completed;
the existing `completed.answer` remains authoritative.

The `Audit_assistant` frontend merges deltas idempotently by message, revision,
and sequence, and renders the in-progress answer as plain text with a live
typewriter cursor. Incomplete Markdown is never rendered. Completion removes
the provisional draft and continues to render the existing canonical answer
through `AssistantMarkdown`. Refresh recovery returns only the latest running
Turn's collapsed draft, not all historical answer events, which keeps workspace
state bounded while preserving `Last-Event-ID`/`after_sequence` replay.

The answer switch remains default-off. With it disabled, Hermes retains the
previous no-op stream callback, no answer events or draft state are exposed,
and the UI falls back to the Phase 4 pending/final-answer behavior. Projection
and persistence failures remain fail-open and cannot fail the underlying Turn.

Phase 5 candidate verification used the immutable A/B archives and the pinned
Douyin acceptance crawler as read-only dependencies:

- Full clean-environment backend gate: `1408 passed, 28 skipped, 67 subtests
  passed` in 202.45 seconds; only five pre-existing dependency/lifespan
  deprecation warnings remained.
- Focused answer/activity/creation backend gate: `98 passed`; the final API
  recovery rerun added `7 passed` after the latest draft-state assertions.
- Focused frontend logic gate: `46 passed`; system-Chrome answer/activity
  rendering gate: `5 passed`.
- Broader frontend gate: `89 passed, 2 skipped`, with the same four pre-existing
  presentation/selector contract failures documented at Phase 4 and two new
  Phase 5 tests passing.
- TypeScript checking and the Vite production build passed; the existing
  large-chunk warning remains.
- Neither the formal 3198/8198 runtime nor the independent 8027 acceptance
  runtime was started, restarted, or modified.

### Phase 5 live Qwen and browser acceptance

The final gate ran from source revision `eb32e56` in the isolated temporary
runtime
`/Users/ext.wanghongtao6/Documents/Codex/acceptance/investigation-answer-stream-20260917.UZxv0Q`
on frontend/backend ports 3299/8299. Its databases were SQLite backup copies,
all of which passed `PRAGMA quick_check`. The runtime read the frozen report
archives and model configuration without writing to them. The answer and
activity switches were enabled; the creation-answer switch remained disabled.

Four independent real-Qwen Turns passed:

| Report | Answer deltas | Activity events | First delta | Total time | Running draft recovered |
| --- | ---: | ---: | ---: | ---: | ---: |
| Historical A | 16 | 2 | 28.481s | 36.422s | 19 chars |
| Historical B | 38 | 2 | 25.670s | 39.234s | 2 chars |
| Historical C | 38 | 2 | 26.194s | 38.729s | 16 chars |
| Unified, 5 posts | 35 | 2 | 26.480s | 39.091s | 2 chars |

Each case deliberately disconnected immediately after its first answer delta,
then reconnected using both `after_sequence` and `Last-Event-ID`. The combined
stream retained strictly increasing, unique sequence numbers; no boundary
event was repeated, no later event was lost, and terminal replay exactly
matched the live event sequence. Workspace state exposed the current draft
while the Turn was still running. In every case the accumulated draft exactly
matched the final canonical `completed.answer`, and the real tool activity
moved from `running` to a terminal status once.

The browser gate used the production `Audit_assistant` through a streaming
Node proxy. A real Report A question visibly showed the expandable activity
timeline, a growing plain-text answer, and the typewriter cursor before the
Turn completed. The provisional draft was then replaced by the existing
Markdown-rendered canonical answer. A reload that completed after the Qwen
Turn finished restored one final answer bubble and one activity timeline,
without duplication. A running-draft refresh was independently asserted in
all four live runtime cases above; Report A suffices for the visual gate because
all report types use the same frontend stream component.

All public events and browser-visible text passed a forbidden-field probe for
raw tool arguments, tool-call IDs, internal account references, snapshots,
fingerprints, filesystem paths, and internal receipts. Receipts retain only
counts, timings, booleans, and answer hashes:

- `receipts/live-answer-stream.json`: four real-Qwen runtime cases.
- `receipts/browser-answer-stream.json`: visible typewriter and replacement
  observations.

Neither formal 3198/8198, the previous 3199/8199 acceptance runtime, nor the
independent 8027 runtime was started, restarted, or modified. Phase 5 is
therefore frozen and accepted at the revision carrying this record. Phase 6
was implemented separately after explicit authorization and is recorded below.

## Phase 6 creation and resource answer typewriter stream

Phase 6 connects the existing creation/resource Hermes stream callback to the
same durable answer protocol used by reports. This covers read-only platform,
ruleset and lexicon queries; ruleset Proposal and lexicon Edit generation or
updates; formal save operations; investigation Draft create/update; explicit
confirm-and-queue; and Run status reads without changing any tool definition,
argument, receipt, idempotency fence, provider retry, fallback retry, or
business mutation order.

Creation text has a separate server-side projection. It cumulatively removes
internal field names, tool-call and receipt identities, Draft/Run/Proposal
identities, hashes, UUIDs, and filesystem paths before an unfinished fragment
can become public. Tool and later-model-iteration boundaries reset the
provisional revision. The same projection is applied to the final assistant
answer and transcript before persistence, and the provisional draft is
reconciled with that canonical answer before the Turn becomes terminal.
Authoritative public artifacts and mutation receipts remain separate from the
natural-language answer and are not inferred from model prose.

The frontend consumes creation `answer_delta` and `answer_reset` events through
the existing shared SSE client. The growing plain-text answer and the Phase 4
activity timeline occupy one pending assistant card. On completion, the draft
is removed and the existing canonical Markdown answer plus any authoritative
artifact are rendered. Workspace recovery exposes only the latest running
creation Turn's collapsed draft. The creation-answer switch remains
independent and default-off; disabling it restores the previous creation
pending/final-answer behavior.

Phase 6 candidate verification used the immutable A/B archives and pinned
Douyin crawler as read-only dependencies:

- Full clean-environment backend gate: `1411 passed, 28 skipped, 67 subtests
  passed` in 226.22 seconds; only the five existing dependency/lifespan
  deprecation warnings remained.
- The new creation stream, filtering, canonical reconciliation, and recovery
  tests passed. The preceding focused answer/activity/creation gate passed
  `89` tests, and the final new focused gate passed `4` tests.
- TypeScript checking and the Vite production build passed; the existing
  large-chunk warning remains.
- The focused system-Chrome creation/activity rendering gate passed `6` tests.
- The broader frontend gate reached `91 passed, 2 skipped`; its four failures
  are the same pre-existing presentation/selector expectations documented in
  Phases 4 and 5. Both new Phase 6 tests passed.

### Phase 6 live Qwen and browser acceptance

The final gate ran from candidate revisions `a404d14` and `a696ac4` in the
isolated temporary runtime
`/Users/ext.wanghongtao6/Documents/Codex/acceptance/investigation-creation-answer-stream-20260917.99uhLP`
on frontend/backend ports 3399/8399. All databases were copies and passed
SQLite `PRAGMA quick_check`; the formal environment and previous acceptance
copies were not written.

Two independent real-Qwen creation Turns passed:

| Case | Answer deltas | Activity events | Complete lifecycles | Draft delta | Run delta | First delta | Total time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Read existing resources | 64 | 2 | 1 | 0 | 0 | 19.319s | 31.832s |
| Create editable Draft | 47 | 6 | 3 | 1 | 0 | 24.188s | 44.846s |

Each case disconnected after the first answer delta and reconnected with both
`after_sequence` and `Last-Event-ID`. Sequences remained strictly increasing
and unique, full replay exactly matched the live stream, and the running
workspace state restored the current creation draft. The collapsed answer
exactly matched the terminal canonical answer. Reposting the same
`client_message_id` returned the same Turn and did not repeat a business
mutation. The read-only case created no Draft or Run; the mutation case created
exactly one editable Draft and no Run, confirming that streaming and replay do
not turn Draft creation into task startup.

All creation answer/activity projections passed a forbidden-field scan for raw
tool parameters, tool-call IDs, internal receipts, Draft/Run/Proposal IDs,
hashes, and local paths. The browser gate independently showed the real
resource-query activity, a growing plain-text answer with a typewriter cursor,
canonical Markdown replacement, and one final answer/activity timeline after
reload.

Acceptance receipts:

- `receipts/live-creation-answer-stream.json`: real Qwen, disconnect/reconnect,
  refresh recovery, exact replay, idempotent resubmission, and mutation counts.
- `receipts/browser-creation-answer-stream.json`: visible typewriter, activity,
  canonical replacement, reload, and leak observations.

The temporary frontend and backend were stopped after the gate. Neither formal
3198/8198, the previous 3199/8199 acceptance runtime, the independent 8027
runtime, nor the Phase 5 3299/8299 runtime was started, restarted, or modified.
Phase 6 is therefore frozen and accepted at the revision carrying this record;
at the time of that freeze, Phase 7 had not started.

## Phase 7 replay and failure hardening

Phase 7 starts from the frozen Phase 6 tag
`investigation-creation-answer-stream-phase6-acceptance-20260917` and remains
on the isolated `codex/investigation-streaming-hardening` worktree. It does not
change report or creation tool definitions, authorization, provider retries,
grounding retries, mutation receipts, or canonical final-answer persistence.

The workspace recovery payload now reads public activity only for the most
recent 20 Turns by default. The bound is configurable with
`INVESTIGATION_ACTIVITY_RECOVERY_TURN_LIMIT` (1 through 100), while the current
Turn answer draft and canonical message history retain their existing recovery
paths. Store queries filter activity/answer/Turn event types in SQLite instead
of materializing unrelated public events.

Durable SSE replay now reads at most 256 events per batch by default, controlled
by `INVESTIGATION_STREAM_REPLAY_BATCH_SIZE` (1 through 1,000). Backlog batches
drain without the live-poll delay. A terminal event closes the stream only after
the cursor is caught up with the Turn's latest durable sequence, so an older
interruption at a batch boundary cannot hide a later resume attempt. The
`Last-Event-ID` lookup remains scoped to the requested Turn and has an explicit
cross-Turn regression test.

Projection-storage failures were injected into both report and creation paths.
In each case streaming failed open: the canonical Turn completed, report tools
executed once, creation produced exactly one Draft and no Run, and no partial
display event changed business execution. Two report Sessions also streamed in
parallel without sharing answer text or revisions. A 6,000-character answer
delivered one character at a time was coalesced before persistence, reconstructed
exactly, and kept every public delta within the 4,096-character contract.

The browser stream consumer now rejects otherwise valid activity, answer, reset,
and terminal events whose `turn_id` differs from the requested Turn. Existing
sequence and revision idempotency remains in place.

### Phase 7 deterministic gate

The tested source revision is `c957dc7d435aefffded665e98ca2f459c7efb6f3`.
The formal runtime, the 3199/8199 acceptance runtime, the independent 8027
runtime, and the Phase 5/6 temporary runtimes were not started or modified.

- Focused streaming/activity/creation backend gate: `108 passed`.
- Full backend gate with the immutable A/B archives: `1418 passed, 28 skipped,
  67 subtests passed` in 200.11 seconds.
- TypeScript checking: passed.
- Frontend logic gate: `14 passed`.
- Focused activity/typewriter browser gate: `6 passed`.
- Vite production build: passed; the pre-existing large-chunk advisory remains.
- Report A/B archive hashes remained
  `f70d1b9fb6cd85471d3f9e8e3bc2c0d89f390329a6c5a02449d4a3c506b46560` and
  `2de176629ac0cd2d06588bc436786dc45819555ec48bc2cad59a84207c8793bf`;
  both read-only `PRAGMA quick_check` results were `ok`.

The structured receipt is
`docs/evidence/investigation-streaming-phase7-hardening.json`. Phase 7 is a
stable candidate, not a rollout acceptance. Phase 8 has not started and no
Phase 7 freeze tag is created by this record.
