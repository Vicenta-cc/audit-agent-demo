# Event-place programmatic test evidence

## Latest complete result

The original 13-file suite after the historical-success replay fix ran once: **448 passed, the same 4 failed, 23 subtests passed, 68.56 s**. Pytest exit code is 1 because the pre-existing failures remain visible. `final-command.json`, `final.log`, `final-result.json` are this latest run. `comparison.json` compares it with the baseline and intermediate run. All 64 newly added checks passed; no new failing nodes.

## Preserved earlier runs

- Before production edits: `baseline-command.json`, `baseline.log`, `baseline-result.json`: 384 passed, 4 failed, 23 subtests, 57.57 s.
- Before replay compatibility fix: `pre-replay-final-command.json`, `pre-replay-final.log`, `pre-replay-final-result.json`, `pre-replay-comparison.json`: 437 passed, the same 4 failed, 23 subtests, 67.96 s. Original files were renamed, not overwritten.
- First targeted pass: `targeted-command.json`, `targeted.log`: 128 passed, 29.67 s.
- After historical replay fix, four targeted files: `replay-command.json`, `replay.log`, `replay-result.json`, `replay-summary.json`: 147 passed, 25.54 s.

The four creation_prompt_isolation failures already existed before this change. No skip/xfail or unrelated assertions were changed. New tests cover missing/invalid/null/contradictory create decisions, incomplete apply payloads, create-only fields rejected on update, all four formal/temporary resource combinations without partial adoption on validation/source failure, and failed validation corrected then replayed under the same identity with one successful creation. Existing legal tool calls explicitly declare none/apply. No injection into scripted runtime automatically repairs missing arguments.

Eleven historical replay cases prove legacy successful configure-create arguments replay only with complete matching durable identity and unchanged SQLite state; argument/owner/session/turn/call/tool mismatches and FAILED/STARTED receipts using old/new argument fingerprints cannot bypass current validation. Exact replay neither executes tools nor begins/rebinds receipts. Changed files passed AST parse and git diff --check.

All runs use isolated temporary database, outputs and Hermes directories, no real model/collection requests or production database writes. These are programmatic checks, not proof of model intent recognition or frontend rendering.
