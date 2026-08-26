# Legacy Cleanup Matrix (R0 Planning Only)

This matrix updates the prior cleanup plan without deleting or moving anything.
Every row remains recoverable through the Preservation Gate archive.

| Legacy area | R0 disposition | Why it remains / is excluded | Cleanup Pass 1 action |
|---|---|---|---|
| Demo `backend/investigation/` | Included as legacy compatibility | Current API/SSE imports it directly | Keep facade; no new features |
| Demo `backend/investigation_domain/` | Excluded, archival candidate | No current product or Hermes import | Re-audit imports, then archive only |
| Demo `backend/investigation_runtime/` | Excluded, archival candidate | Pre-Hermes v3 runtime; Hermes is canonical | Preserve oracle/fixtures and recovery refs |
| Controller / Planner / Focus / Dossier / Search experiments | Excluded, archival candidate | No irreplaceable Hermes/product dependency | Retain deterministic fixtures only where registered |
| Account A0/A1/A2 worktrees | Excluded, archival candidate | Superseded by Hermes M2.2 Account path | Keep branches/bundle until consumers are disproven |
| Demo `experiments/` and results | Excluded, evidence/archive only | Provider traces and one-off evaluation outputs | Do not delete evidence in R0 |
| Dirty MediaCrawler worktree | External compatibility blocker | Required changes are uncommitted and not reproducible | Publish source-only checkpoint before integration |
| External `hermes-agent-main` source tree | External dependency, not copied | No `.git`; version/source hashes recorded only | Verify install/commit in a fresh environment |

The matrix is a plan, not an authorization to run `git worktree remove`, delete
branches/tags, or delete artifacts/SQLite files.

## R0.1 closure note

No Cleanup Pass 1 action was performed. Hermes remains the canonical future
Agent route, while pre-Hermes runtimes remain archival candidates or narrowly
marked compatibility facades. The MediaCrawler dependency now has a recoverable
source-only checkpoint; the original dirty worktree remains untouched. The
candidate is retained without a baseline tag until the exact Hermes clean-room
gate passes.
