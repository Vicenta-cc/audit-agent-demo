# R0.2.1 Test Report

## Results

| Gate | Result |
|---|---|
| R0.2.1 invocation/concurrency/compatibility | `15 passed` |
| Full backend deterministic suite | `311 passed, 4 skipped, 13 subtests passed` |
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
