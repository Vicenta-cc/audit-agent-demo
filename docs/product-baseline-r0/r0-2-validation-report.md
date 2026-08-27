# Product Baseline R0.2 Validation Report

Base: `e03eb2af42439fbabc56c2e1db6af5c3e608b3e3` on
`codex/product-baseline-r0-2`. Canonical Agent source:
`0a5090579c4cfcda1208269814f32fbe77da4c86`.

| Acceptance gate | Result |
|---|---|
| Canonical Agent source/schema exact | PASS |
| Canonical product prompt exact and consumed | PASS |
| Real plugin discovery and final Tool catalog | PASS |
| Formal Investigation API uses Hermes adapter/AIAgent | PASS |
| Formal R3.1 read and generation entry | PASS |
| Legacy API/SSE compatibility | PASS |
| Current deterministic contracts | PASS |
| Frontend frozen installs/builds | PASS |
| No experiment worktree or `PYTHONPATH` runtime dependency | PASS |
| Secret/SQLite/artifact/absolute-path staged scan | PASS |
| Clean worktree | PASS after the acceptance evidence commit |

No tag is claimed by this document. The acceptance decision and annotated tag
are made only after final commits, committed-tip scans, and a clean status.
