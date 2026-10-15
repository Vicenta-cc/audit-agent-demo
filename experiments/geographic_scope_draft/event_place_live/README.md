# Event-place independent live experiment

Expectations are frozen in `cases.json` and `cases.sha256` before model execution. E21 is repeated as E21a/E21b; there are 24 conceptual cases and 25 required runs. Frozen P0 evidence is unchanged.

- Runtime: `/Users/ext.wanghongtao6/Documents/Codex/runtime/geographic-p1-event-place-20261015`.
- API 8993, Vite 3993 (managed by parent), encrypted loopback CONNECT relay 8994.
- Launch with the existing `audit-agent-demo/.venv/bin/python`; the main project's interpreter lacks `openai`. The first initialization failures remain in `results/primary` (zero model calls), separated from `results/native`.
- Credentials load only into the server environment from the existing private profile; they are neither copied nor exported. Pro authoring and coordination use the same existing profile. No transport monkeypatch is installed.
- Only synthetic generic published rules and two lexicons are seeded. No ready temporary resources, geographic drafts, prior conversation, account credentials or tool references are injected. Ordinary cases explicitly select these existing test resources after their original wording; E21/E22/E23 actually generate temporary resources.
- All case sessions use an isolated test principal. Temporary references remain session-bound. Model conversations and receipts are persisted by normal production services in the isolated DB.
- No confirmation/start endpoint is called. Pending-only model clarification without a saved draft counts as incomplete, not a saved-scope success.

Run from the P1 worktree:

```sh
python3 experiments/geographic_scope_draft/event_place_live/run_live.py --cases E21a,E21b --workers 2 --output experiments/geographic_scope_draft/event_place_live/results/native
python3 experiments/geographic_scope_draft/event_place_live/run_live.py --cases E01,E02,E03 --workers 2 --output experiments/geographic_scope_draft/event_place_live/results/native
python3 experiments/geographic_scope_draft/event_place_live/refresh_evidence.py experiments/geographic_scope_draft/event_place_live/results/native
```

The refresh command only reads observed SQLite records. It unwraps native deferred `tool_call` arguments to expose actual `configure_investigation_draft` inputs; it does not call models, repair model arguments or alter expected results. Each model turn records call count, tokens, elapsed time, public final answer and safe actual configure arguments. Schema descriptions are exported only when present in persisted actual tool receipts; absence is an explicit evidence limit.

`summary.json` is each runner invocation's progress; use `combined-summary.json` from refresh when several invocations share an output directory. Never interpret a subset progress denominator as the full acceptance denominator.
