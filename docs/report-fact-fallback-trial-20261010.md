# Offline factual presentation trial

The user requires non-thinking DeepSeek, autonomous report exploration, low
latency and no expanded System Prompt or answer-repair loop. This trial only
demonstrates deterministic rendering of three explicitly selected saved tool
responses. It does not change production conversation behavior.

Run with an already authorized local acceptance directory:

```sh
python scripts/report_fact_fallback_prototype.py \
  --acceptance-root /path/to/acceptance \
  --output /path/to/new-output
```

The output contains an HTML comparison and JSON provenance with exact original
calls, input hashes, calculated facts, unchanged old answers and manual findings.
No new model request is made. The existing short System Prompt, thinking setting,
tool schemas, permissions and runtime are unchanged.

Three cases:

- Stable comment identity: one original comment, two completed assessments,
  separate A/B risks. Old report-local references do not identify new originals.
- Unresolved identity: no confirmation or exclusion of a target account. The
  cause is not available in the historical response and must not be invented.
- Account filtering: zero post authors only within the actual `high` query.
  This is a previously correct control answer, not a newly corrected failure.

Historical common-account `unique_evidence_count=5` is a known tool defect and
is not used as a trusted count. The prototype derives the first case from the
selected account's complete observations, with explicit completeness checks.
Missing evidence keys never merge equal text. The last page alone is not called
the full result; account preview truncation is independent of account pagination.

Four targeted tests passed. They cover identity versus assessment, unknown
identity, missing stable evidence, last-page coverage, pending assessments,
filters and truncated observations. HTML is escaped; the output contains no
executable script. No full conversation acceptance was run for this offline demo.

**Decision:** useful as a partial product pattern, not a proven answer-quality
fix. Correct facts beside contradictory prose still constitute a failed answer.
The original erroneous prose is deliberately preserved and marked unresolved.
Do not call this an automatic scope selector, a guarantee of correct analysis,
or a production-ready fallback. If adopted later, live authorization, evidence
selection, streaming and follow-ups without a new query require separate design
and acceptance. Main factual results could be rendered by the application while
the Agent retains control of exploration and interpretation.

The prototype is on `codex/report-fact-fallback-trial`. No cloud changes,
frontend integration, prompt expansion, model switch or GitHub push.
