# R0.2 Dependency and Runtime Manifest

| Boundary | Record | Verification |
|---|---|---|
| Candidate base | `e03eb2af42439fbabc56c2e1db6af5c3e608b3e3` | independent R0.2 branch/worktree |
| Canonical Agent | `0a5090579c4cfcda1208269814f32fbe77da4c86` | every candidate-integrated `hermes_m0` Python file remains blob-exact; validation-only provider config also exact |
| Hermes requirement | official repository, commit `e624e9fde561e1add9388384012b295fde669ade` | `requirements-hermes.txt` pin retained |
| Hermes installed runtime | 0.20.4 | candidate-local non-editable wheel; public imports and live plugin discovery passed |
| Product prompt | SHA-256 `62768caf1e59d4783c8c8ca362e30dd5bbee05f232898631bd7d31512a360c9e` | exact and consumed by AIAgent |
| Python | 3.12.10 | resolved packages in `python-resolved-dependencies-r0-2.txt` |
| MediaCrawler | checkpoint `a77d8f4ad99b692641711c8e170c73dc7ebdf627` | unchanged R0.1 source-only dependency |
| Frontend | Node 24.19.0, pnpm 11.19.0 | two frozen installs/builds, zero downloads |

All registered Hermes critical hashes match `HERMES_RUNTIME_MANIFEST.md`.
The Hermes build source had no Git metadata, so hash equality and package
version are recorded without inventing a commit-attestation claim. Runtime
imports resolve from the candidate-local virtual environment and do not use
the build-source path.

Known limitation: direct Python requirements contain ranges. The resolved
manifest records this acceptance environment but is not a cryptographic
transitive lock. Python SQLite 3.49.1 triggered Hermes' upstream WAL warning;
the isolated smoke selected DELETE journaling and made no concurrent or
production write.
