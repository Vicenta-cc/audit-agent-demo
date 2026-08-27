# R0.2.1 Session-Scoped Hermes Runtime

## Authority and binding

The Product Store remains the sole Session and Turn authority. Hermes receives
the Product Session identifier only as server execution context and has no
`SessionDB`; `session_db=None` is explicit. Tool schemas contain no Session,
ReportVersion, task-scope, or authorization-switch parameter.

The worker-local registry maps one Product `session_id` to one independent
`ReportTaskInvestigationToolService`. Its identity fences the anchor
ReportVersion, FrozenSnapshot, content hash, database hash, Account Corpus,
server-authorized additional Report sources, refs and ledger. A different
anchor is rejected. Cache loss is recoverable because every Turn validates the
Product Store relationship and rebuilds the entry when absent.

The anchor Report permission is checked every Turn. Additional Account sources
come only from the server setting `HERMES_AUTHORIZED_REPORT_VERSION_IDS`; they
cannot be supplied by Qwen or Tool arguments. The configured contexts are
resolved again every Turn. A changed authorization fingerprint replaces only
that worker cache entry while preserving the anchor.

Registry locks cover dictionary operations only. AIAgent construction has a
short creation lock, but `run_conversation` and Tool execution have no global
lock. SQLite's unique running-Turn index remains the cross-worker same-Session
concurrency guard.

## Canonical invocation

The product path matches the final M2.2 held-out runner:

| Input | Value |
|---|---|
| provider / model | `alibaba` / `qwen3.7-plus` |
| API mode | `chat_completions` |
| max iterations | `12` |
| enabled toolsets | `investigation` |
| context files / memory / soul / background | skipped / skipped / not loaded / skipped |
| Prompt injection | `run_conversation(system_message=...)` |
| history | latest completed private `result.messages`, in original order |
| persistent Hermes Session | disabled |

The raw prompt file hash is
`62768caf1e59d4783c8c8ca362e30dd5bbee05f232898631bd7d31512a360c9e`;
the held-out-normalized injected text hash is
`c3e5d2d96d0beed0d803cefaf454d2e6c5f404474d1e022de895aaedc3cc78c1`.
The 11-source-schema hash remains
`8ff81c67592b9313b9368af0a846aafcefff9f8844f267c6bb2c8fb6eda0af7f`.
Hermes 0.20.4 returns the Qwen-visible definitions in name order; their exact
serialized hash is
`5d4b9a27e004ef8c9043b3d4c3cd9ee09831634877ad79ab6cb10a180d10a821`.

## Transcript and compatibility

Every completed Turn atomically stores the full private Hermes transcript,
including user, assistant tool calls, ToolResult and ordering, with a SHA-256
integrity field. The next Turn reads this private JSON directly. It never
reconstructs Hermes history from the public Message DTO. Returned history must
preserve the supplied transcript prefix and current user message or the Turn is
marked unknown-outcome and no transcript is written.

Completed replay returns the persisted Product result without Agent or Tool
execution. Interrupted and unknown outcomes do not replace the latest completed
transcript. Public Message/API/SSE projection is unchanged and excludes private
ToolResult bodies and tool-call identifiers.

## Intentional canonical adaptations

The M2.2 domain services, Prompt content and 11 Tool schemas are unchanged.
The only `hermes_m0` adaptations are the worker registry and product-mode
fail-closed lookup in `runtime.py`/`plugin.py`. They replace the held-out
runner's one-process/one-report harness assumption; they do not change Tool
business behavior. R3.1 business logic is unchanged.
