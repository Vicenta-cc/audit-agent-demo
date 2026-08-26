# Product Baseline R0.1 Validation Report

## Candidate

- Worktree: `/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-product-baseline-r0-1`
- Branch: `codex/product-baseline-r0-1`
- Base commit: `c3b03689117981ad1a1a67ae62ed54e2e3c585ba`
- Scope: blocker closure only; no M3, cleanup pass, database migration, or
  deletion of preserved worktrees/artifacts.

## Gate results

| Gate | Result | Evidence |
|---|---|---|
| M2.2 artifact manifest | PASS | `fixture-manifest.md`; frozen questions/oracle paths and SHA-256 are registered as read-only acceptance evidence, not runtime dependencies |
| Hermes exact source/install/plugin | **BLOCKED** | `hermes-clean-room-report.md`; fresh official fetch of `e624e9f...` failed under HTTP/2 and an HTTP/1.1 retry failed to connect, before any object was obtained |
| MediaCrawler source-only checkpoint | PASS | `media-crawler-migration-matrix.md`; checkpoint `a77d8f4...` from tracked HEAD `ec56ebf...`; isolated offline tests `8 passed` |
| M1/M2.2/R3.1 deterministic contracts | PASS | `deterministic-test-report.md`: `220` tests passed, `1` guarded skip; reporting boundary `4 passed` |
| R3.1 published fixture read-only load | PASS | Archive-registered published SQLite opened with SQLite `mode=ro`; one or more published versions and the public `frontend-report.json` projection loaded; evidence remains outside source tree |
| Backend import/startup and API compatibility | PASS | no-provider import/startup smoke; `/api/config` and report-version route shapes; full stage 3b API/SSE tests |
| Frontend frozen install/build | PASS | `pnpm install --frozen-lockfile`; `pnpm build` |
| Secrets, path, and dependency scan | PASS with documented exclusions | Runtime source has no literal credential/private-key pattern or foreign runtime worktree path; historical docs/SQL examples are retained and not runtime imports |
| Worktree clean | PASS | Final candidate status is clean; ignored local venv/build/SQLite outputs are not staged |

## Reproducibility record

The candidate product environment used Python `3.12.10` and the install command
`uv pip install --python .venv-r0-1/bin/python -r requirements.txt`. The 70
resolved packages and manifest hash are recorded in
`python-resolved-dependencies-r0-1.txt`. Because the source requirements still
contain range constraints, that manifest records the observed environment but
does not claim future resolver equivalence without a frozen lockfile.

The Hermes requirement is separately pinned to the official repository and
commit in `requirements-hermes.txt`; no package versions can be recorded for
that gate because the fresh exact fetch failed before installation. No local
Hermes worktree, alternate venv, temporary `PYTHONPATH`, provider, or API key
was used as a substitute.

## Acceptance decision

**Not accepted. No annotated tag is created.** Acceptance condition 1 is not
met because Hermes exact-commit acquisition and installation remain blocked by
the network failure. The candidate commit is retained for the narrow retry:
repeat the official fetch in a network-enabled clean room, install only the
candidate requirement, compare the registered manifest files, and run the
adapter/plugin smoke. All other gates remain recorded; this round stops here.
