# M3 T4A: Authoritative Proposal Presentation

Baseline: `3bab432c32423cf1540a5567f76beac03fc87fa7`.
Result: `M3_PHASE_T4A_PASS`.
Workspace: `/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-m3-authoritative-wiring`.
No commit, approval/use tool, Draft binding, temporary judgement, or execution support is included.

## Production Files

- `backend/investigation_creation/presentation.py`: deterministic human-readable complete RuleSet rendering and snapshot evidence construction.
- `backend/investigation_creation/store.py`: durable receipt-to-conversation binding and authoritative snapshot selection.
- `backend/investigation_creation/tools.py`: explicit Application turn context around Hermes tool execution, without changing receipt identity.
- `backend/investigation_creation/conversation.py`: establishes that context and supplies verified snapshots to final message persistence.
- `backend/investigation/store.py`: commits public answer, existing assistant message identity, and presentation evidence in the same transaction.
- `backend/investigation_creation/public_projection.py`: typed Proposal presentation public artifact, including coexistence with Draft/Run artifacts.
- `Audit_assistant/src/types/investigationCreation.ts`: Proposal artifact compatibility.
- `Audit_assistant/src/types/investigation.ts`: literal Proposal presentation message flag.
- `Audit_assistant/src/features/investigation/workspaceRecovery.ts`: preserves the server message and its real identity through workspace recovery.
- `Audit_assistant/src/features/investigation/InvestigationCenterArea.tsx`: displays the complete message as escaped literal text in the existing message surface.

## Architecture And Boundary

The Application explicitly binds the executing conversation turn to each Proposal mutation receipt.
Real Hermes uses an opaque runtime turn ID different from the conversation turn ID. This mapping is
recorded by the server during execution; neither identifier parsing nor model prose supplies it.
The receipt key remains `(session_id, turn_id, tool_call_id)` with its existing replay behavior.

Successful create/update receipts select the relevant Proposals. The Application rereads each
session-owned Proposal and requires its version/hash to match the selected successful receipt.
For multiple mutations of the same Proposal within one turn, only the last successful snapshot is
presented. An intervening external update causes failure rather than silent substitution.

The deterministic text includes name, domain, audit goal, every category and rule (including disabled
rules), conditions, risk, application stages, adjudication notes, exemptions, ordering, and source
mappings. Full canonical content is also preserved in the presentation snapshot. Qwen's original
explanation remains before the server text; it is not used to select or verify any snapshot.

`InvestigationStore.complete_turn` constructs the public text and evidence using the existing user
message and stable final assistant message IDs. It validates the public artifact schema, persists
the answer, verifies the stored answer matches, and commits the turn artifact in the same SQLite
transaction. Rendering, schema, or commit failure leaves no presented evidence. A successful
Proposal mutation alone leaves no presented evidence. Terminal replay returns the original message.

The publication boundary is the durable public assistant message. Both terminal turn responses
and workspace message responses return that message content and its artifact. The existing frontend
loads workspace state when a turn returns an artifact. Proposal messages bypass the legacy prose
sanitizer and Markdown interpretation, preserving literal URLs, angle brackets, backticks, and long
identifiers. Desktop and mobile browser checks exercise the real recovery and message components.

This boundary proves publication of exact content into the public conversation. It does not claim
browser delivery acknowledgment, that a human read every line, or physical visibility during a
network outage. No client read receipt is implemented. Historical public messages remain recoverable.

## Record Schema

Presentation records are stored in the existing turn's `public_artifact_json.proposal_presentations`:

```text
session_id
source_user_turn_id
source_user_message_id
assistant_message_id
presented_at
proposal_id
proposal_version
content_hash
snapshot: TemporaryRuleSetProposal (including full RuleSetContent)
presentation_format: ruleset-proposal-text-v1
text: complete deterministic human-readable presentation
boundary: durable_public_assistant_message
```

The additional Creation Store table is `ruleset_proposal_conversation_bindings`:
`receipt_id` (primary key, receipt reference), `application_turn_id`.
It is execution bookkeeping, not presentation evidence. Existing receipt rows supply session and
runtime execution identity. No second conversation system or Proposal lifecycle state is added.

## Create And Update

- Create v1: persist Proposal, then publish v1 with the actual final assistant message.
- Update v2: publish a separate v2 record and message; historical v1 evidence remains unchanged.
- Stale selected snapshot: fail closed with no presentation record or silent latest-version adoption.
- Unbound tool-only operations and get: retain T3 behavior; they do not independently assert presentation.
- Existing Draft content, revisions, Formal RuleSets, Runs, Jobs, Workers, and Reports are untouched by presentation.

## Validation

Only the user-specified `.venv-r0-2-1/bin/python` was used. Pytest was run with isolated data/output
directories set before import, including the M3 module's existing isolation checks.

- Focused T4A plus T3: 46 passed (11 new T4A tests, 35 T3 tests).
- Combined T4A, T3, M3 creation/conversation, RuleSet compiler/generalization, M1/M2 offline: 190 passed, 22 subtests passed.
- TypeScript: `tsc --noEmit` passed.
- Focused frontend: 3 passed, including actual Chrome at 1280x900 and 390x844, literal-content checks, wrapping checks, and screenshots.
- Full pytest: 822 passed, 24 skipped, 66 subtests passed, 5 deprecation warnings (79.60 seconds).
- `git diff --check`: passed.

The additional existing frontend suites returned 29 passed / 5 failed. An isolated archive of
baseline `3bab432` returned 28 passed / the same 5 failed (the new T4A test accounts for the extra
passing test). Baseline failures are stale expectations for `auditPolicy`, `policyName`, and
management/diagnostic copy. They were not changed in this phase.

## Real Qwen Acceptance

Final successful evidence: `m3-t4a-qwen-acceptance-bound.json`.
Runtime: Hermes 0.20.4; model: qwen3.7-plus; disposable Session and Stores.

- Case A: "帮我生成一套招聘诈骗研判规则给我看看。" called `create_ruleset_proposal`; public v1 has 5 categories and 8 rules.
- Case B: "第二条严格一点。" called `update_ruleset_proposal`; the same Proposal was published as v2 with a different content hash.
- Both public message payloads and evidence snapshots match the authoritative Proposal exactly.
- Both cases leave Drafts unchanged and have zero Runs, Jobs, Reports, and no Formal RuleSet mutation.
- Deliberately wrong Qwen counts/descriptions are covered deterministically; presentation construction never reads those claims.

Two earlier diagnostic runs are retained as `m3-t4a-qwen-acceptance.json` and
`m3-t4a-qwen-acceptance-verified.json`. They are not passing acceptance evidence. They exposed the
runtime/conversation identity mismatch; the final bound run verifies the explicit mapping fix.

## Residual Limits

No T4B approval, Draft binding, or T5 execution is implemented. Future approval must validate the
exact stored presentation identity and snapshot, current Proposal version/hash, and an actual later
user approval turn. Existing receipt behavior does not provide a stronger exactly-once crash guarantee.
Frontend baseline failures and the 24 optional/environment-dependent pytest skips remain documented.

## Git Status

Current `git status --short` after the P1 fix in the authoritative workspace (no commit):

```text
 M Audit_assistant/src/features/investigation/InvestigationCenterArea.tsx
 M Audit_assistant/src/features/investigation/TaskSuggestionCard.tsx
 M Audit_assistant/src/features/investigation/workspaceRecovery.ts
 M Audit_assistant/src/types/investigation.ts
 M Audit_assistant/src/types/investigationCreation.ts
 M Audit_assistant/tests/investigationCreation.spec.tsx
 M backend/investigation/store.py
 M backend/investigation_creation/conversation.py
 M backend/investigation_creation/public_projection.py
 M backend/investigation_creation/store.py
 M backend/investigation_creation/tools.py
 M tests/m3_t3_qwen_acceptance.py
 M tests/test_ruleset_proposals.py
?? Audit_assistant/tests/mixedProposalPresentation.spec.ts
?? Audit_assistant/tests/proposalPresentation.spec.ts
?? backend/investigation_creation/presentation.py
?? docs/evidence/m3-t4a-presentation.md
?? docs/evidence/m3-t4a-qwen-acceptance-bound.json
?? docs/evidence/m3-t4a-qwen-acceptance-verified.json
?? docs/evidence/m3-t4a-qwen-acceptance.json
?? tests/test_ruleset_proposal_presentation.py
```

## Mixed Artifact P1 Fix Review

Result: `T4A_P1_FIX_PASS`. P0: 0. P1: 0. No commit.

The previous final review blocked on an early Proposal presentation return in
`workspaceRecovery.ts`: a mixed Draft/Proposal message became `type: "text"`, losing
its Draft card. Presentation content and the literal-rendering flag are now additive
to the existing Draft projection. The original message ID, `task_proposal` or
`task_confirmation` type, proposal data, and creation binding remain intact.

This fix changes only three production files: `workspaceRecovery.ts`,
`InvestigationCenterArea.tsx`, and `TaskSuggestionCard.tsx`. The suggestion card uses
its existing assistant text area for the complete escaped message. Confirmation and
configuration cards compose that text with the existing card under the same message.
No duplicate conversation message or new message model is introduced.

Tests are added in `investigationCreation.spec.tsx` and
`mixedProposalPresentation.spec.ts`; this report is the only documentation change
in the P1 fix. Backend code and the Real Qwen evidence are unchanged by this fix.

Both first artifact receipt and route refresh already call
`restoreInvestigationWorkspace` in `InvestigationPage.tsx`. Browser component tests
exercise that shared projection and the real `InvestigationCenterArea`, initially
and again after page reload. They cover Draft-only and mixed suggestion/confirmation
messages, exact literal content, escaping, one message identity, visible cards, and
clickable platform, ruleset/configuration drawer, search-term update, generate-preview,
and confirmation controls. Callback assertions verify the existing action dispatch;
these tests do not claim a live backend execution or browser read receipt.

Validation after the production fix:

- Mixed/Draft-only browser tests plus existing Proposal literal tests: 6 passed.
- Creation, presentation, and runtime-boundary frontend suites: 32 passed, 5 failed.
  This includes two new mixed recovery tests. All five failures match the baseline
  reproduction at `/tmp/m3-t4a-baseline.lPZy5o/results.json` (28 passed, 5 failed).
  No baseline failure was changed or suppressed.
- TypeScript: `tsc --noEmit` passed.
- Backend T4A + T3: 46 passed.
- Full pytest: 822 passed, 24 skipped, 66 subtests passed, 5 warnings (79.73 seconds),
  using the required Python and isolated data/output directories.
- Refreshed suggestion/confirmation screenshots inspected; literal text and existing
  controls remain composed without horizontal text overflow.
- `git diff --check`: passed.

The working tree retains the uncommitted T4A implementation plus this focused fix.
No T4B/T5 behavior, frontend redesign, commit, or push is included.
