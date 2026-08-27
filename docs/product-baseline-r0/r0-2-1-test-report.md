# R0.2.1 Test Report

## Results

| Gate | Result |
|---|---|
| Blocking-closure focused suite | `32 passed, 12 subtests passed` |
| Full backend deterministic suite | `320 passed, 4 skipped, 25 subtests passed` |
| Formal API/executor concurrency | PASS; deterministic Barrier overlap with two workers |
| Hermes retry parity | PASS; installed 0.20.4 Agent has `_api_max_retries == 1` |
| Transcript protocol/fail-closed | PASS; legal shapes, invalid pairings, serialization and transaction rollback |
| Real Hermes plugin discovery | 0.20.4; 11 tools; Qwen-visible order/hash matched |
| Hermes critical packaged source hashes | all seven registered packaged files matched |
| Python dependency integrity | 98 installed packages; `pip check` clean |
| Backend startup | formal `HermesInvestigationAgentService`; `/api/config` 200; 68 routes |
| `frontend-v2` frozen install/build | PASS; 79 reused, zero downloaded |
| `Audit_assistant` frozen install/build | PASS; 158 reused, zero downloaded; existing chunk advisory only |
| Secret/path/database/artifact diff scan | PASS |
| Diff whitespace | PASS |

Tests used Python 3.12.10 in candidate-local `.venv-r0-2-1`, temporary Product
Stores and Hermes homes, deterministic fixtures, fake Agents and read-only
runtime discovery. No Provider/API key, Qwen, MediaCrawler, login, collection,
production database write or temporary `PYTHONPATH` was used.

## Concurrency proof

Barrier-based tests force two Agent turns and two Tool dispatches to reach the
same execution point before either can finish. They verify A reads Report A, B
reads Report B, A remains A on reuse, A refs fail in B, unbound sessions fail
closed, one Tool exception leaves both bindings intact, same-Session second
Turn conflicts in SQLite, and completed replay keeps execution count at one.
The primary proof enters through the Product API router and the formal
`InvestigationTurnExecutor`; it does not call the Service directly as a proxy
for the production path. The configured default is two workers and no lock
covers a complete AIAgent Turn.

## Transcript proof

Legal samples are derived from canonical Agent source `0a509057...`, Hermes
0.20.4, and accepted replacement raw trace SHA-256
`e61356c193cbbd7eccb1d52112bd2832c29d4fdd110f1d0c6eb5bbfd83354c56`.
They cover no-Tool answers, one ToolCall/ToolResult, multiple rounds, and a
grouped multi-call Hermes turn. Negative cases cover non-object messages,
unsupported roles, orphaned/duplicate/unmatched/out-of-order ToolResults,
duplicate call IDs, dangling calls, missing final assistant, changed history,
and final-response mismatch.

Service-level tests prove that invalid completed output, JSON serialization
failure, and an injected database failure after transcript insertion all end
as `interrupted` / `hermes_unknown_outcome`, preserve the last completed
transcript, and leave no partial transcript row. Explicit Hermes interruption
uses `hermes_interrupted`. An observer failure after the completed transaction
does not reverse the committed result. Public Message and SSE source projection
contain neither private ToolResult content nor tool-call IDs.

## Dependency provenance

The declared Hermes source remains the official repository commit
`e624e9fde561e1add9388384012b295fde669ade`. A fresh direct install was attempted
and failed with GitHub HTTP/2 framing error. No local Hermes source or
`PYTHONPATH` was substituted. The candidate-local venv instead installed the
preserved non-editable 0.20.4 wheel with SHA-256
`b849e1cc9df474ecb121b8f912621eaae1654b619e068f990d15d6320d48b0f8`;
all seven packaged critical hashes match the recorded runtime manifest.

Product pins for `requests`, `python-dotenv` and `cryptography` were aligned to
the exact Hermes metadata. This removed the prior no-deps environment's 19
metadata incompatibilities. A future clean official fetch remains required to
replace hash-equivalent artifact provenance with fresh Git commit attestation.

## Skips and warnings

The four skips are unchanged environment/fixture guards. Existing warnings are
Starlette multipart and FastAPI lifecycle deprecations. Hermes also warns that
Python's SQLite 3.49.1 predates the upstream WAL-reset fix; isolated test state
is disposable, and production SQLite runtime upgrade remains an operational
prerequisite rather than an R0.2.1 code change.

## Blocking finding disposition

1. Formal Product executor global serialization: **CLOSED** by configurable
   `HERMES_INVESTIGATION_MAX_WORKERS` with a minimum/default of two and formal
   API/executor overlap proof.
2. Canonical Provider retry mismatch: **CLOSED** by product-home configuration,
   the Hermes 0.20.4 version fence, post-construction assertion, and offline real
   constructor test proving `_api_max_retries == 1`.
3. Transcript integrity/persistence boundary: **CLOSED** by full Hermes protocol
   validation, exact history/final-answer checks, atomic transcript/completed
   persistence, and distinct unknown-outcome/interruption tests.

Residual operational risks are unchanged: no live Provider or platform run was
performed, official Git fetch attestation remains pending as described above,
and the deployment SQLite runtime must include the upstream WAL-reset fix.
