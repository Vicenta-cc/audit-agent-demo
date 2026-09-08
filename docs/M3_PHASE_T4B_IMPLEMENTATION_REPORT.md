# M3 T4B — Approval semantics correction

Authoritative baseline: `d418d5059007d072dc6eef597b27b8099b20fb25` (`feat: add authoritative RuleSet proposal presentation`).

This report supersedes the original uncommitted T4B report. Its regex-based approval design and its old `M3_PHASE_T4B_PASS` are not the acceptance basis for this correction. No commit, push, T5 runtime, formal RuleSet save, compiler semantic change, M1/M2 change, or MediaCrawler change is included.

## Guarantee and responsibility

Chat approval trusts Qwen's semantic judgement. Qwen interprets adoption, editing, questions, saving, negation, hesitation, comparison and ambiguous references. Only adoption should call `use_ruleset_proposal`; uncertainty should be clarified. Application never classifies the natural-language user message. The former regular expressions, keyword blacklist, name/ordinal matching and bare-assent exception have all been removed.

A model intent error remains possible. A binding audit proves provenance and structurally validated binding; it does not prove Application independently established explicit natural-language consent. Future direct user UI confirmation is outside this stage.

## Minimal presentation identity

`presentation_id` is `ruleset-presentation:` followed by the SHA-256 of canonical JSON for the existing complete T4A presentation record (excluding its own ID). The record includes Session, Proposal ID/version/hash/full snapshot, source user turn/message, durable assistant message, timestamp, text, format and boundary. This is an identity, not a credential or a separate presentation subsystem.

T4A's existing `InvestigationStore.complete_turn` creates the record and atomically persists it with the completed turn and public assistant message. Constructing a Proposal, rendering text, or preparing an artifact grants no authority: adoption only reads records joined to completed turns and their actual durable assistant messages. IDs are stable across recovery. Historical records without an ID remain readable but are excluded from adoption and model ID projection; no latest-version backfill exists.

## Trusted Agent context

Before each creation Agent turn, Application validates completed presentation artifacts against their actual assistant messages and projects their presentation IDs, Proposal IDs/versions, assistant-message identities and timestamps into `completed_public_presentations` metadata on the matching existing Proposal ToolResults in conversation history. Matching requires the original successful ToolResult data to equal the authoritative snapshot. IDs are not appended to user-visible assistant prose. The actual model history argument is tested.

Hermes caches its initial system prompt. A first real-provider attempt exposed that adding changing IDs only to `system_message` did not deliver them on later turns: Qwen supplied a Proposal ID and Application rejected it without binding. The final implementation uses the existing ToolResult history artifact projection, not a second conversation or an extra LLM call. Raw failed-attempt evidence is retained separately. Additional harness corrections distinguish an already-bound no-op from an unbound stale attempt and explicitly supply the platform/search mode required for complete creation; Qwen correctly asked for missing platform instead of inventing it.

Only prior completed durable records are projected. This context is not a claim that the user approved; Qwen must select the particular displayed snapshot intended by the current user.

## Tool contract and injected authority

Model arguments are exactly:

- required `presentation_id`;
- either `draft_id` + `expected_revision`;
- or complete `create_draft` containing title, objective and configuration (platform and investigation/Recall, without judgement).

Strict extra-field rejection excludes Proposal ID/version/hash/content, Session, user turn/message, assistant message, runtime identity and approval flags. Application/runtime supply principal, Session, active real user turn/message, runtime turn and tool-call identity. Runtime turn and Application user turn are recorded separately because they are different identities in Hermes.

## Authoritative write boundary

The existing resource fence and SQLite `BEGIN IMMEDIATE` boundary validate the current active creation Session/user turn, an exact unique presentation ID, completed durable publication, strict earlier-message ordering, complete presentation integrity, current Proposal Session/version/hash/canonical content, Draft ownership/editability/expected revision, and complete create configuration.

No fallback to the latest Proposal or latest presentation occurs. Draft writes, Draft revision insertion and binding audit insertion commit atomically. Failure rolls back all three. Ordinary create/update/HTTP paths cannot introduce or replace temporary judgement; legal title/platform/Recall edits remain available. Existing Draft targeting and no-placeholder/no-duplicate creation protections remain.

`TemporaryRuleSetJudgement` is unchanged: `strategy=temporary_ruleset`, `proposal_id`, `proposal_version`, `content_hash`, inline `RuleSetContent`. Later Proposal changes do not affect bound Draft reads, recovery, edits or preview.

## Stale, conflict and replay

Stale presentation fails with a structured reason/recovery and no Draft/audit mutation. A newly prepared or same-turn presentation cannot be used; after authoritative re-presentation a later real user message and a fresh semantic decision are required. Draft revision conflict asks the Agent to read and assess the latest Draft instead of blindly replacing the expected revision.

Identical binding is a no-op. Existing durable receipt replay does not duplicate Draft revision/audit; conflicting arguments fail closed. The receipt protocol is unchanged: a crash between mutation commit and receipt completion can leave an unknown result. This report makes no crash exactly-once claim.

## Audit semantics

The existing audit table and timestamp field are retained. New columns add presentation ID, tool-call ID and runtime turn ID; additive migration supports existing local databases. Legacy rows are not retroactively assigned authority.

Each new binding records Session, current actual user turn/message, runtime turn/call, selected presentation, its assistant message, Proposal ID/version/hash, target Draft, resulting revision and binding time (`approved_at`, retained field name). It records Agent selection plus Application structural verification, not independent natural-language approval classification.

## Errors and UI fallback

Existing structured tool errors carry a stable code, safe reason and recovery direction for missing/invalid/stale presentations, Draft conflicts, unauthorized or non-editable targets and incomplete configuration. Missing and cross-Session IDs share a safe unavailable-in-current-Session response, avoiding resource disclosure.

The existing conversation completion path appends a safe binding-failure notice based on mutation receipts, with validated ToolResults covering pre-receipt argument failures. It therefore survives an empty model explanation and workspace recovery. Existing failed/interrupted-turn messages include receipt-backed reasons. No new Error UI framework exists. Desktop/mobile tests verify the fallback remains visible after refresh.

Temporary Draft preview remains available with `can_confirm=false`. Direct Confirm is blocked before configuration resolution/freeze, Run insertion, Job creation or Worker invocation. Existing RuleSet execution paths remain supported.

## Validation

Final results and real Qwen per-case tool sequences are recorded in the companion correction validation evidence. Earlier T4B results and original three-turn evidence are historical only, not proof of the corrected contract.

Only Python used: `/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-product-baseline-r0-2-1/.venv-r0-2-1/bin/python`. Backend suites start with isolated data/output directories. Real-provider fixtures use isolated databases and do not export credentials or provider logs.

**T4B_APPROVAL_SEMANTICS_CORRECTION_PASS**

- T4B deterministic contract tests: **56 passed** (included in focused and full suites).
- Corrected T4B/T4A/T3/M3 creation/conversation/provider focused regression: **348 passed, 22 subtests passed**.
- Compiler/category/foundation and M1/M2 offline regression: **188 passed, 7 skipped, 19 subtests passed**.
- Full pytest on final implementation: **878 passed, 24 skipped, 0 failed, 66 subtests passed**, 5 dependency/deprecation warnings, 89.03 seconds.
- TypeScript: **PASS** (`pnpm run typecheck`).
- Relevant frontend suites: **45 passed, 5 pre-existing failures**. All frontend suites: **69 passed, 2 skipped, 5 pre-existing failures**.
- All five failure identities AND complete assertion messages equal the saved run on baseline `d418d505`. Baseline relevant suites: 38 passed / 5 failed. No old failure was repaired or suppressed. New desktop/mobile fallback and recovery tests pass.
- `git diff --check`: **PASS**.
- Real Qwen: **A–J PASS**, with no execution or formal resource mutation. The three-turn path explicitly establishes 小红书/search in its first user message; independent semantic cases use controlled, durable T4A setup followed by actual real Qwen decisions. No fake Agent is used to prove semantic decisions.

## Real Qwen sequences on final implementation

The full actual arguments/results, selected presentation IDs, public artifacts/answers, Draft before/after, binding audits and execution counts are in [verified real-provider evidence](evidence/m3-t4b-semantics-qwen-verified.json). [Validation evidence](evidence/m3-t4b-semantics-validation.json) provides compact summaries and frontend baseline comparison.

| Case | Actual tool sequence | Outcome |
| --- | --- | --- |
| I, generate v1 | query_investigation_options → query_investigation_options → create_ruleset_proposal | Authoritative v1, no Draft/audit |
| I, edit v2 | update_ruleset_proposal | Authoritative v2, no Draft/audit |
| I, adopt v2 | use_ruleset_proposal | Exact v2 presentation ID and inline content; one Draft/audit |
| A, “可以，就用这套。” | use_ruleset_proposal | Correct presentation ID; one Draft/audit |
| B, “第二条再改一下。” | none | Asks which change is desired; no use or Draft/audit mutation |
| C, “这套能直接用吗？” | none | Answers question; no use or Draft/audit mutation |
| D, “就用这套创建招聘诈骗调查，还是先暂停调查” | none | Explicitly asks user to choose adoption vs pause; no use or Draft/audit mutation |
| E, “先不要用这套。” | none | Acknowledges retaining candidate; no use or Draft/audit mutation |
| F, “把这套保存下来。” | none | Explains formal save is unavailable; no use or Draft/audit mutation |
| G, multiple / “用那个。” | none | Asks which of the two candidates; no use or Draft/audit mutation |
| H, multiple / “用招聘诈骗那套。” | use_ruleset_proposal | Correct recruitment presentation ID; one Draft/audit |
| J, unseen update then adoption | use_ruleset_proposal → get_ruleset_proposal | `PROPOSAL_PRESENTATION_STALE`; no Draft/audit change and no same-turn use of newer snapshot |

Every case has Run=0, Job=0, Report=0. Final cumulative temporary Drafts=3 and binding audits=3. Formal RuleSet/revision/publication and shared Recall tables are unchanged after fixture setup.

In J the model read the updated Proposal and described it in prose; it did not publish a new authoritative T4A presentation. That prose cannot authorize subsequent use. The safe fallback correctly states that authoritative re-presentation and a later user message are still needed. Deterministic recovery tests separately prove a newly completed T4A presentation becomes usable only in a later user turn.

## Evidence history and remaining limits

Original `m3-t4b-qwen-acceptance*.json` and `m3-t4b-validation.json` belong to the pre-correction design. Correction attempts ending in `semantics-qwen-acceptance.json`, `semantics-qwen-acceptance-verified.json`, and `semantics-qwen-final.json` retain the system-prompt cache discovery and harness precondition failures; their `pass` values are false. They are not presented as accepted runs. The final accepted evidence is **`m3-t4b-semantics-qwen-verified.json`**.

Chat approval trusts Qwen semantic judgement, so model intent misclassification risk is nonzero. Smoke acceptance is not proof that Qwen never misinterprets intent. Application performs no natural-language approval classification; it guarantees authoritative snapshot identity, ownership, integrity, ordering, concurrency and write correctness. The 24 backend skips, 2 frontend skips and 5 baseline frontend failures remain explicit limitations.

No commit or push was performed. HEAD remains `d418d5059007d072dc6eef597b27b8099b20fb25`. Working tree intentionally contains uncommitted T4B work; exact final status is in [status evidence](evidence/m3-t4b-semantics-git-status.txt). No T5, formal save, worker execution, compiler semantics, M1/M2 behavior or MediaCrawler changes were implemented.
