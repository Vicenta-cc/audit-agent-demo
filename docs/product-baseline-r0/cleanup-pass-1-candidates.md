# Cleanup Pass 1 Candidates

R0 does not delete these items. They are candidates for a later, separately
approved cleanup pass after the baseline has been accepted.

| Candidate | Reason | Required prerequisite |
|---|---|---|
| `backend/investigation_domain/` | Pre-Hermes v2 domain experiment | Confirm no product/Hermes import; preserve bundle first |
| `backend/investigation_runtime/` | Pre-Hermes v3 controller/runtime experiment | Confirm no compatibility dependency; archive manifest |
| `experiments/` and `experiments/results/` | Provider experiments and evaluation outputs | Keep immutable evidence references; remove only by explicit approval |
| Old investigation/controller/planner tests and fixtures | Tests for archival runtimes | Re-run capability matrix and retain unique oracle fixtures |
| Legacy Account A0/A1/A2 worktrees | Superseded by Hermes M2.2 | Verify no external consumer and keep recovery refs |
| Dirty MediaCrawler modifications | Uncommitted external compatibility work | Rebase or publish a reproducible dependency artifact first |

M3 owns directory re-layering and any source deletion. R0 only records these
candidates and their stop conditions.
