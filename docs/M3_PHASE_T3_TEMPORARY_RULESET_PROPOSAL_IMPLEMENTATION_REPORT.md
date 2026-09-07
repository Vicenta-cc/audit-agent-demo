# M3_PHASE_T3_TEMPORARY_RULESET_PROPOSAL_IMPLEMENTATION_REPORT

Status: **M3_PHASE_T3_PASS**.

## Baseline and Workspace

- Authoritative workspace: `/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-m3-authoritative-wiring`.
- Branch: `codex/m3-authoritative-crawler-account-wiring`.
- Initial HEAD: `59fba20ac3ba19bce20eff29296f59056c05b1c5`, `feat: generalize RuleSet compiler semantics`.
- Initial `git status --short`: empty. Initial `git diff --check`: PASS.
- Prior status: `M3_PHASE_T2_5_PASS`; supplied baseline: 776 passed, 24 skipped, 0 failed, 66 subtests passed.
- The task's initial demo cwd was not the requested authoritative workspace. Its pre-existing changes were left untouched.
- Only Python used: `/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-product-baseline-r0-2-1/.venv-r0-2-1/bin/python`.
- No commit, amend, push, reset, restore, checkout, clean, new venv, or MediaCrawler changes.

## Final Contract and Persistence

`TemporaryRuleSetProposal` directly contains the existing canonical `RuleSetContent`:

| Field | Authority and behavior |
| --- | --- |
| `proposal_id` | Store-generated opaque `ruleset-proposal:<uuid4 hex>` |
| `session_id` | Current authoritative conversation/tool execution identity |
| `version` | Starts at 1; increments once for an actual content change |
| `content_hash` | Application calls existing `backend.rulesets.compiler.content_hash` |
| `content` | Existing `RuleSetContent`; no parallel category/rule schema |
| `created_at` | Server clock; preserved on every update |
| `updated_at` | Server clock; preserved on same-content updates |

Persistence is an additive `ruleset_proposals` table in the existing InvestigationCreationStore SQLite database, with exactly `proposal_id`, `session_id`, `version`, `content_hash`, `content_json`, `created_at`, `updated_at`. Formal RuleSet storage is not used. There is one current row per Proposal, with no Proposal history or revision subsystem. Existing mutation receipts and tool transcripts continue to provide operation audit records.

There is no `status`, `active_proposal_id`, automatic supersession, approval lifecycle, compiler snapshot, prompt profile, runtime prompt, compiler config hash/version, template version, or Run execution data in Proposal rows. Multiple Proposals coexist in one Session.

## Validation and Concurrency

Create validates strict canonical `RuleSetContent`, calculates its existing canonical content hash, runs `compile_ruleset_content` for generic T2.5 compatibility and prompt budget validation, and persists only after success.

Update has the final addendum contract: `proposal_id`, `expected_version`, `content`. It does not accept `expected_content_hash`. Application checks session ownership and the current version, validates the full replacement content, compiles it, calculates the hash, and calls the Store. The Store uses `BEGIN IMMEDIATE`, rereads ownership/version under the write lock, and atomically updates the current row. Concurrent writers with the same expected version have one winner; the other returns `RULESET_PROPOSAL_STALE`.

The existing typed tool argument boundary still precedes receipt creation. Thus invalid typed arguments/schema produce `INVALID_TOOL_ARGUMENTS`, with `receipt_created=false` and no mutation. After that boundary, Application ownership/version checks precede compilation. Compiler failures produce an existing FAILED receipt, with no Proposal mutation; existing receipt recovery semantics are unchanged.

`content_hash` is maintained entirely by Application, using the existing canonical function and only `RuleSetContent`. Identity, Session, version, timestamps, and compiler metadata do not contribute. Qwen neither supplies nor maintains it.

Same-content update is a no-op after successful expected-version and compile validation: return the exact current Proposal; do not change `version`, `updated_at`, or `created_at`. A stale expected version still conflicts even when replacement content matches the current hash. There is no automatic merge or last-write-wins behavior.

T4 approval binding to `proposal_id + version + content_hash` was not implemented.

## Tools and Identity

| Tool | Model arguments | Result |
| --- | --- | --- |
| `create_ruleset_proposal` | `content: RuleSetContent` | Complete Proposal projection |
| `update_ruleset_proposal` | `proposal_id`, `expected_version`, `content: RuleSetContent` | Complete updated/current Proposal projection |
| `get_ruleset_proposal` | `proposal_id` | Complete current Proposal projection |

Existing Hermes `{status, data}` / structured error envelopes are preserved. The JSON schemas expose the canonical `RuleSetContent` definition, with extra fields forbidden. `expected_version` is a strict positive integer. Authoritative create fields, aliases, wrappers, and the removed update hash argument are rejected. No list, approve, use, formal save, or publish tool was added.

The existing dynamic Hermes M3 catalog registers the three additional tools, after the six existing tools, without modifying M1/M2 registration logic. Get receives the dispatched authoritative Session; create/update receive the Session from the required durable tool execution identity. The conversation principal resolver continues to authorize Session access. Knowing another Session's Proposal ID does not permit get/update, including for the same principal.

Create/update reuse `investigation_creation_tool_receipts` and the existing `session_id`, `turn_id`, `tool_call_id` boundary. Replay returns the original authoritative envelope even after a later edit. Create replay does not create a second row; update replay does not increment twice. Reusing identity with a different payload/tool returns the existing `IDEMPOTENCY_CONFLICT`. Get creates no mutation receipt. The existing per-turn, per-tool receipt convention is unchanged.

## Conversation and Product Boundaries

The same Agent Qwen authors full structured `RuleSetContent` directly in tool arguments. There is no second backend Qwen generation call or natural-language parser/merge engine. Prompt guidance supports generation when requested, full-content edits, stable category/rule IDs and ordering, and independent regeneration without overwriting earlier candidates.

Proposal tools create no Formal RuleSet Draft, revision, published resource, formal identity, or publish receipt. Proposal tools do not create/change Investigation Draft Judgement, inline content into Draft configuration, or add a temporary Judgement variant. Published RuleSet options remain unchanged and never include Proposals. No backend eligibility engine, semantic relevance/ID validator, authorization flag, or tool-order gate was added.

**PRODUCT DUAL-MISSING CONTRACT:** Draft v4 requires `judgement: ExistingRuleSetJudgement`. It cannot persist a search Draft with temporary Recall and absent Judgement. When both matching formal resources are absent, the requested Proposal can be generated; an incomplete Draft cannot be saved. The Draft schema was not changed.

**TOOL-LOOP CAPACITY SMOKE:** A separate isolated scenario has no Recall lexicon, uses an existing matching published gambling RuleSet as Draft Judgement, and requests an additional comparison Proposal. This allows temporary terms in Draft Recall and Proposal creation in the same turn. Proposal creation does not supply the Draft's Judgement.

Deterministic integration exercises both orders: Proposal then Draft, and Draft then Proposal. Both mutations persist with distinct receipts and no Run. The product has no one-generation-per-turn restriction, forced sequence, query-before-create gate, or new Application orchestration state machine.

## Deterministic Validation

| Suite | Result |
| --- | --- |
| Proposal contract/store/tools/conversation | 35 passed |
| M3 creation, conversation, Draft v4, authoritative provider closure | 246 passed, 22 subtests passed |
| Existing temporary terms (`-k 't1 or temporary'`) | 25 passed, 129 deselected |
| RuleSet content compiler, generic compiler, category identity, gambling foundation | 176 passed, 19 subtests passed |
| M1/M2 offline plus Finding comment provenance | 12 passed, 7 skipped |
| Full pytest | 811 passed, 24 skipped, 0 failed, 66 subtests passed; 78.56 seconds |

New tests cover Recruitment Fraud create/get/compile/persistence, canonical hashing, v1-to-v2 update, same-content timestamp preservation, stale v2 against current v3, session isolation, create/update replay, payload conflicts, concurrent writers, multiple candidates, schema/category/budget/compiler failures, no compiler snapshot persistence, schema projection/strictness, real dispatch identity, unchanged formal resources/options, unchanged existing Draft Judgement, and both same-turn mutation orders.

The old stale-content-hash input case is superseded by the final addendum: a supplied `expected_content_hash` is rejected as an extra argument before receipt creation. Hash correctness remains covered through Application-generated canonical hashes and same-content no-op tests.

The offline three-turn conversation creates P1 v1, reads and edits P1 to v2, then creates P2 v1; both remain independently readable and no Proposal-only turn creates a Draft artifact.

Intermediate failures were test expectation/setup issues: an old six-tool registration assertion, and a new test accidentally reusing the same tool call ID for different mutation tools. They were corrected without changing receipt semantics. The first full run had 810 passed, 24 skipped, 1 failed, 66 subtests passed because it had already loaded that test before the ID correction. The corrected Proposal suite passed all 35 tests; the full suite was rerun. No crawler cleanup flake has been observed.

## Real Qwen Acceptance

Runner: `tests/m3_t3_qwen_acceptance.py`.

Initial evidence: `docs/evidence/m3-t3-qwen-acceptance.json`.

Final receipt-verified evidence: `docs/evidence/m3-t3-qwen-acceptance-verified.json` (PASS).

The runner uses a disposable `/tmp` directory, isolated data/output/Hermes paths, conversation Sessions, resource DB, Proposal/Draft DB, and transcript DB. Only provider credentials are read from the existing credentials environment file; they are never printed or recorded. Third-party logs are captured privately. Existing synthetic fixture resources are used; no formal publish action, login, crawler, Worker, report generation, or preserved/production database write is performed. The acceptance harness rejects confirm/start calls before execution; it does not change the product contract.

Cases: recruitment Proposal generation, next-turn targeted fee-rule edit, independent regeneration, real temporary terms alone, then same-turn temporary terms plus comparison Proposal. At most one controlled dual-generation retry is permitted. Each case records tool sequence/arguments/status, model answer/shape, LLM round trips, receipts, and Draft/Proposal side effects. Hashes are recomputed and content is compiled during acceptance.

Initial real acceptance passed all five cases:

| Case | Application tool sequence | LLM round trips | Outcome |
| --- | --- | --- | --- |
| Recruitment Proposal | query options, query options, create Proposal (invalid typed args), create Proposal (success) | 7 | P1 v1; no Draft |
| Targeted edit | update Proposal | 3 | P1 v2; only `fee_upfront` hit condition and adjudication notes changed; IDs preserved |
| Regeneration | create Proposal | 2 | P2 v1; P1 v2 retained |
| Temporary terms alone | query options, create Draft | 6 | Temporary terms persisted in Draft Recall |
| Same-turn dual generation | query options, query options, create Draft, create Proposal | 7 | Both branches succeeded on the first attempt |

The initial invalid Proposal payload omitted required rule names and misplaced a rule under categories. Strict typed validation rejected it with no receipt or mutation. Qwen repaired the payload in the same turn. The persisted Recruitment Fraud content was relevant to the requested topic and compiled successfully; no backend parser or extra generation call repaired it.

LLM round trips include the existing Hermes `tool_search` / `tool_describe` discovery layer. The table lists actual Application tool dispatch, not those metadata operations. The corrected runner also records the full Hermes tool-name sequence.

All initial cases left formal RuleSets and lexicons unchanged, with zero Runs, Jobs, and reports. The dual case used existing published Judgement and persisted a separate Proposal; it did not implement T4. The first dual attempt succeeded, so no orchestration-failure retry was needed.

Evidence review found a harness collection defect: it filtered receipt rows by the Application turn ID, while the existing Hermes runtime generates a separate execution turn ID from Session, task ID, and a nonce. Consequently the initial JSON's `mutation_receipts` arrays are empty despite successful persisted mutations and receipts. This was not a product/receipt contract failure. The harness now collects receipt-row deltas, captures the actual tool execution identity, and requires every successful mutation to match a SUCCEEDED receipt. A single verification rerun uses `--dual-attempts 1`, limiting total real dual attempts across both runs to two. No production code was changed for this evidence correction.

The receipt verification rerun also passed all five cases:

| Case | Application tool sequence | LLM round trips | SUCCEEDED receipts |
| --- | --- | --- | --- |
| Recruitment Proposal | query options, create Proposal | 6 | 1 |
| Targeted edit | get Proposal, update Proposal | 3 | 1 |
| Regeneration | create Proposal | 2 | 1 |
| Temporary terms alone | query options, create Draft | 6 | 1 |
| Same-turn dual generation | query options, query options, create Draft, create Proposal | 7 | 2 |

In the verification run, the first authored Proposal payload was valid. P1 again advanced from v1 to v2 with only the requested advance-fee rule changed (`fee_collection.upfront_fee`), unchanged IDs preserved, and unrelated rules unchanged. P2 v1 was independent. All six mutation receipts were matched to the actual execution identities. The two dual-generation receipts share one Hermes execution turn ID and have distinct tool call IDs and tool names. Their Draft and Proposal writes coexist. All five cases preserved formal resources and had zero Runs, Jobs and reports.

Dual-generation failure classification: **not applicable, both real attempts passed**. No `SYSTEM_CONTRACT_FAILURE` or `MODEL_ORCHESTRATION_INSTABILITY` was observed in the dual-generation outcome. **`REAL_QWEN_DUAL_GENERATION_STABILITY_GAP=false`**. This is bounded real acceptance evidence, not a statistical claim about all future model calls. The initial recoverable malformed Proposal payload is retained in the evidence.

## Scope, Residuals, and Final State

- Frontend: unchanged; no Proposal card or partial rendering work.
- Worker, Pipeline, crawler, MediaCrawler, Run execution, and reports: unchanged.
- M1/M2 business logic, tools and prompts: unchanged.
- Streaming, Hermes delta callbacks, SSE, `answer_delta`, and `user_visible_final`: unchanged.
- T2/T2.5 compiler semantics: reused without production compiler edits.
- T4/T5/T6, approval/use, freeze, runtime snapshots, formal save and publish: not implemented.
- `RESIDUAL_MEMBERSHIP_VALIDATION_GAP` remains P2 and out of scope.
- `RUN_JOB_RULE_PROMPT_CONSISTENCY_GAP` remains out of scope.
- Residual T3 gaps: no unresolved core implementation blocker or dual-generation stability gap observed. Normal model argument validation/recovery was observed during the initial standalone generation.
- Final `git diff --check`: PASS. New source/document files were also checked for whitespace errors.

Changed production files: `backend/investigation_creation/contracts.py`, `conversation.py`, `errors.py`, `service.py`, `store.py`, `tools.py`.

Changed existing tests: `tests/test_investigation_creation_conversation.py`, `tests/test_investigation_draft_configuration_m3.py`. New tests/acceptance runner: `tests/test_ruleset_proposals.py`, `tests/m3_t3_qwen_acceptance.py`. This report and the real acceptance evidence are new documentation artifacts.

Final HEAD remains `59fba20ac3ba19bce20eff29296f59056c05b1c5`. The implementation is intentionally uncommitted. Final `git status --short`:

```text
 M backend/investigation_creation/contracts.py
 M backend/investigation_creation/conversation.py
 M backend/investigation_creation/errors.py
 M backend/investigation_creation/service.py
 M backend/investigation_creation/store.py
 M backend/investigation_creation/tools.py
 M tests/test_investigation_creation_conversation.py
 M tests/test_investigation_draft_configuration_m3.py
?? docs/M3_PHASE_T3_TEMPORARY_RULESET_PROPOSAL_IMPLEMENTATION_REPORT.md
?? docs/evidence/m3-t3-qwen-acceptance-verified.json
?? docs/evidence/m3-t3-qwen-acceptance.json
?? tests/m3_t3_qwen_acceptance.py
?? tests/test_ruleset_proposals.py
```

After PASS, the only recommended next phase is T4: Temporary RuleSet Proposal approval into Investigation Draft Judgement. T4 is not part of this implementation.
