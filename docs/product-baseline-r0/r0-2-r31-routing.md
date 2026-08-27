# R0.2 R3.1 Routing Report

Formal read path:

```text
GET /api/report-versions/{report_version_id}/structured-report
  -> R31ReportRuntime.get_frontend_report()
  -> ReportStore.get_frontend_report()
  -> structured-report-r3.1/v1 fence
  -> ordered_sections
  -> persisted account_model consistency check
  -> safe ReportAccountEntry projections
```

The public report document keeps the canonical flattened Account fields. The
schema fence compares those fields with the persisted `body.account_model`
rather than inventing a new public shape. Private IDs and absolute paths fail
closed.

Formal generation entry:

```text
R31ReportRuntime.generate()
  -> AccountOverviewReportGraph(...)
  -> generate(task_id)
  -> close()
```

No generation or Provider call was run. The published evidence SQLite with
SHA-256 `f70d1b9fb6cd85471d3f9e8e3bc2c0d89f390329a6c5a02449d4a3c506b46560`
was opened read-only by the canonical repository and copied only to a temporary
directory for `ReportStore` restart validation. Results: schema v1, 14 ordered
sections, deterministic zero-standalone section, 1345 Account index entries,
stable pagination, preserved title/published time, and legacy
`human-report-v1` availability. The evidence source was not modified.
