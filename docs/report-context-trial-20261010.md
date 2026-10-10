# Report context trial — 2026-10-10

Decision: leave `REPORT_COLLECTION_COMPACT_HISTORY=0`. The prototype reduces
older tool-result text but has not demonstrated a reliable improvement in answer
quality. Do not enable it as a factual-accuracy fix.

`663b4e0` adds an optional model view of older replayable tool results. The last
two actual completed turns remain intact; older detail can be read through the
existing result reader. Durable history stays complete. Database turn boundaries
avoid treating Hermes network-continuation messages as additional user turns.
Authorization, query freedom and the existing short system prompt are unchanged.

The corrected trial used `deepseek-v4-1-flash-260910` at the configured provider,
two identical frozen synthetic reports, and paired 19-turn conversations through
the actual local authenticated conversation API. All 38 turns completed, with
79 SDK calls. The last compact turn's same-history projection reduced 80,811 to
58,187 characters (28%). Total elapsed time was 165.1s full versus 163.9s compact;
this is not evidence of a stable speed improvement. Runtime cumulative uncached
input tokens were 30,826 versus 83,132; no billing claim is supported.

Both modes correctly answered the targeted original-comment/assessment count,
unknown identity, high-risk filtering, frozen configuration and follow-up cases.
Both still volunteered facts outside a B-only answer's intended scope. The
compact mode omitted an explicitly requested excluded-report version, then
claimed it had shown it. Old result readback worked. Actual provider inputs
matched the expected projection on every turn, durable history was preserved,
and outside-conversation/other-owner test secrets were absent. This is a small
synthetic test, not production or browser acceptance.

An earlier 76-turn trial had an incorrect user-message-based projection boundary.
Its failures and records are retained separately and not pooled with the repaired
version. Repeatedly changing JSON or history based on a single stochastic answer
is not a justified next step.

## Independent statistics correction

`dbfa10a` fixes `unique_evidence_count` for account queries: count evidence attached
to the matched accounts, before pagination and preview truncation. Previously a
common-account intersection returned two matching accounts but counted all five
scanned pieces of evidence instead of their two. Unresolved identity observations
remain disclosed separately; they are not evidence attributable to a known
account and must not generally be added to a deduplicated count.

After the context trial was sealed, this correction was checked separately with
compaction disabled: three real model questions, eight SDK calls, correct core
answers of two common-account evidence items, four all-account evidence items,
and one original comment/two assessments. This does not guarantee every sentence
the model generates. In particular, an answer's extrapolation about the total
including unresolved observations still requires a complete evidence query.

Final related regression suite: **181 passed, 6 warnings**. New statistics tests
cover constrained/agent modes, paging, risk filters, unknown identity and truncated
observation previews. No new report lock, forced object inheritance, answer gate
or expanded System Prompt was introduced. No cloud changes or GitHub push.

## Reproduction

Use isolated output directories and the actual conversation provider credentials.
Never run the acceptance harness against a production data directory.

```sh
python scripts/report_collection_flexibility_acceptance.py \
  --output /tmp/report-context-paired-new --mode agent --context-paired
python scripts/report_collection_flexibility_acceptance.py \
  --output /tmp/report-statistics-new --mode agent --statistics-probe
```

The context comparison above predates the statistics correction; reproduce its
exact code at `663b4e0`. Automated trace checks are diagnostic, not a substitute
for reviewing final answers. Local acceptance artifacts are saved under
`report-context-20261010-{paired,repaired,statistics,review}` in the task's
acceptance directory, with full answers, tool results, manifests, provider input
audits and an HTML review.
