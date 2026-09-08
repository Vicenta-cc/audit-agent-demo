# M3 T5 final review and T1–T5 integrated acceptance

Starting authoritative HEAD: `ad47e537013c38fe031ec1bab9bd7486767e49cb` (`feat: bind approved RuleSet presentations to investigations`). This record supplements the historical [T5 phase implementation report](M3_PHASE_T5_IMPLEMENTATION_REPORT.md); its earlier uncommitted state and failed attempts are retained as history.

## Current pre-commit decision — 2026-09-08

**T1–T5 PRE-COMMIT GATE PASS; P0 = 0, P1 = 0.** The user explicitly accepts Integrated B as a known external collection residual, not a commit blocker. B remains **not PASS** and has not reached fresh `AUDIT_COMPLETED`; acceptance criteria are unchanged. The historical blocked decision below is superseded only for commit authorization.

Later standalone Douyin diagnostics observed HTTP 200 / business status 0 with empty data and `search_nil_info.search_nil_type` / `search_nil_item` equal to `verify_check`. The original B evidence recorded empty lists, not those raw verification fields. The external search limitation explains the outstanding collection investigation; its precise trigger is not proven. Existing evidence does not support an existing Recall / formal RuleSet / T5 wiring regression, but does not replace missing successful B acceptance. Standalone controls and account retries are not B acceptance. Their private diagnostic scripts, auth and crawler output are excluded from this commit.

The subsequent adoption hardening changes only public adoption artifact authority: current session / Application turn / principal / tool-bound `SUCCEEDED` mutation receipt → persisted response → durable Draft identity/revision/binding checks → current Application Draft view. Transcript supplies no adoption success fact. Existing replay/no-op and historical-turn recovery are preserved; `STARTED`, forged text and mismatched identities fail closed. No Hermes runtime, receipt schema, execution or crawler redesign was introduced by this hardening.

Added 21 receipt regressions; latest targeted **180 passed** and full **948 passed / 24 skipped / 0 failed / 66 subtests**. See [latest validation summary](evidence/m3-adoption-hardening-validation.json). No code changes followed these runs; this gate only updates documentation. Existing TypeScript PASS and frontend results below remain applicable. The current narrow gate supersedes historical stopping-state language below; no new architecture review or live acceptance rerun is claimed. Commit is authorized without push.

## Final code review

The tracked diff and new production/test files were read directly. Scope is temporary execution, its shared frozen-consumption boundary, required compatibility tests, and sanitized evidence. No Deep-link, streaming, claim citation, formal save/publish, Proposal cleanup, compiler semantic changes, MediaCrawler edits, automatic Report generation or unrelated UI work is included.

| Boundary | Review finding |
| --- | --- |
| Temporary authority | Confirm reads the expected persisted Draft revision's `TemporaryRuleSetJudgement.content`. The store checks its durable revision row, full content, hash and provenance inside the Run write transaction. Proposal is not read during Confirm or execution. |
| Formal compatibility | The original published/current revision/version/content-hash/compiler checks remain. Inactive temporary fields are omitted from formal serialization and its hash payload. The unchanged T4 baseline reader/hash validator is exercised against current formal execution evidence; v2/v3 paths remain supported. |
| Compiler | Temporary packaging calls the existing `compile_ruleset_content`; no alternate compiler, semantic change or domain-specific runtime branch was added. |
| Freeze | Resolution, inline/hash/compile proof and snapshot validation precede Run INSERT. Failure rolls back Run creation and Draft queueing. The successful creation-store transaction is the freeze boundary. |
| Run / Job / revision | Run is authoritative. Existing/candidate Job fields and its owned task audit revision must match the frozen payload. Conditional revision attachment never overwrites a concurrent pointer. Mismatches stop execution without configuration repair or merge. |
| Same validated snapshot | First run and pending-analysis/direct resume consume detached Job rules/prompts and revision returned by the verifier, with runtime parameters from Run. No rule/prompt reread occurs between validation and consumption. Recovery/classification validates the relationship too. |
| Preview / Confirm | Preview compiles and checks readiness without Run/Job writes. HTTP and creation tool use the same application command. Adoption alone never starts execution. |
| Existing execution | Ordinary non-M3 behavior and original formal compiler/hash payload remain; M1/M2 offline regressions pass. M3 success ends at `AUDIT_COMPLETED`, without generating a Report. |

Run and Job use separate database commits. A frozen Run can survive Job/revision creation failure; the pipeline must not start. There is no distributed-transaction or crash exactly-once guarantee. Historical policy execution compares saved revision payload/hash with Run; missing original policy inputs are not invented or fetched live to recompile historical execution.

## Integrated defect and correction

The first integrated attempt exposed one P1 in conversation artifact projection: `json.loads` assumed every Hermes tool message was a pure JSON envelope. A diagnostic with trailing text raised `JSONDecodeError: Extra data` during adoption and changed the turn to unknown outcome. It produced zero Run/Job/Report. This was a product correctness failure, not an environmental retry.

The corrected boundary ignores non-envelope diagnostics as authority, never extracts a successful result from a JSON prefix, and preserves a basic adoption failure notice. A subsequent valid result still passes all existing application identity checks. Eight regressions exercise diagnostic suffixes, plain error text, non-object JSON and a forged success prefix, both with and without a later legitimate adoption. They verify completed turn handling, exact presented content, and no unauthorized Draft/approval/Run/Job.

The [first failed attempt](evidence/m3-t1-t5-integrated-transcript-failure.json) remains `pass: false`. Its original harness retained the first two turns and final counts, but did not export failing third-turn events before isolated teardown; this limitation is explicit. The runner now records failed-turn events as well. Real acceptance was restarted against the corrected production code without changing its acceptance criteria or bypassing Worker/Pipeline.

A subsequent authoring attempt also produced broad synonymous Recall terms (for example, 招聘诈骗 / 招聘骗局 / 求职陷阱). Passing the list schema is insufficient for T1 canonical-term acceptance, so that temporary journey is not accepted as the final integrated A. Its frozen Run is not modified. The final A journey adds a genuine user correction before adoption: Qwen must remove broad topics/variants and generate one concrete canonical search concept, then bind that resulting term and the presented rules. This is an explicit pre-freeze configuration edit; no model decision, term value or execution response is scripted. The runner asserts the corrected single-term contract. Initial authoring quality is not claimed to be reliably correct in one turn.

## Test isolation and validation

`tests/conftest.py` establishes independent data/output/Hermes directories before test-module imports. Plain real-acceptance runners separately set isolation before application imports. The earlier default-store incident, private backup, precisely attributed cleanup and lack of byte-identical pre-incident restoration proof remain in the [observation record](evidence/m3-t5-default-store-observation.json). After the final fixed tests, all 22 default data/output file fingerprints still match the recorded post-cleanup baseline. No skip or assertion relaxation was used.

| Final validation | Result |
| --- | --- |
| T5 including integrated-defect regressions | 49 passed |
| Focused T1/T3/T4/T5, creation and provider closure | 397 passed across two invocations (297 + 100), 22 subtests |
| Full pytest after the production fix | 927 passed / 24 skipped / 0 failed / 66 subtests |
| Compiler/formal/category/M1/M2 offline | 188 passed / 7 skipped / 19 subtests; unchanged compiler/runtime semantics, prior final evidence reused |
| TypeScript | PASS, rerun in this review |
| Relevant frontend changed file | 12 passed, rerun in this review |
| Full frontend | 71 passed / 2 skipped / 5 pre-existing failures; all five names and full assertion messages exactly match saved baseline evidence |

All Python invocations use `/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-product-baseline-r0-2-1/.venv-r0-2-1/bin/python`. Full pytest was run once after this correction. No further full rerun is needed without another code change.

## Historical integrated evidence and former commit gate

**M3_T1_T5_INTEGRATED_BLOCKED. No commit or push.**

Final code review: P0 = 0; remaining P1 = 0. One integrated conversation defect was fixed and tested. The remaining commit blocker is **Integrated B real acceptance**, which has not reached `AUDIT_COMPLETED` with an audit result. No new authoritative baseline is claimed.

### Integrated A — PASS

Actual product journey: real user investigation intent → Qwen queries available resources → generates temporary Recall and a full Proposal → real user requests Recall correction → Qwen retains only **入职前收费** → user requests rule edit → Application presents v2 → later user adopts that completed presentation → exact Draft binding → ready Preview → HTTP Confirm → frozen Run → real Job/revision/Worker/Pipeline → `AUDIT_COMPLETED`, one durable audit result.

All source, hash, presentation, adoption and consumption relationships were independently checked. Proposal reads raise throughout Confirm/execution in this case. No formal RuleSet mutation or automatic Report occurred. The single-term correction is explicit; one-turn initial Recall quality is not claimed.

Evidence: [final temporary A](evidence/m3-t1-t5-integrated-temporary-final.json). Run `investigation-run:da70c3320b424da082c7f5b4c410bc57`; Job `m3-77f537385f77c5b32be2`; audit revision `audit-config-revision:5f96516cbd45712d00b6db67a015e97f`.

Approved source: `{"proposal_id": "ruleset-proposal:27b2c792d2e44cf6bd24a1ebbb5429ec", "proposal_version": 2, "content_hash": "334738c1704aa3dde572f5b826ed5ecd40829128c404341834239d7b12627e68", "presentation_id": "ruleset-presentation:0997832c26441c2a49d265ab808d9a642601af91608165c3c69c884729b9abbc"}`. Draft `investigation-draft:58cb708c19fb48ffb1553fa8ad1c9d9a`, revision 1.

Actual Qwen sequence (rejected calls are retained):

- A-generate: query_investigation_options [ok] → query_investigation_options [ok] → query_investigation_options [ok] → create_ruleset_proposal [ok] → create_investigation_draft [error: INVALID_TOOL_ARGUMENTS] → create_investigation_draft [error: PROPOSAL_APPROVAL_REQUIRED]
- A-refine-recall: no tool call; real user/model Recall correction
- A-edit: get_ruleset_proposal [ok] → update_ruleset_proposal [ok]
- A-adopt: create_investigation_draft [error: PROPOSAL_APPROVAL_REQUIRED] → use_ruleset_proposal [ok]

### Integrated B — BLOCKED

The initial fixture Recall (`上分`, `盘口`) returned no usable content. The next attempt used the authorized source database’s already-saved gambling Recall: 盘口, 回血, 上分, bc料, bc车队, 跑分车队. These entries were copied read-only into isolation before Draft creation; the enabled-main-term list and runtime hash match the source exactly. No term was generated or changed after freeze.

Two runs with that existing saved Recall on Douyin returned no collected content. The final search diagnostics explicitly show an empty list for each of the six terms. A separate Xiaohongshu control attempt failed with `crawler_account_login_required`. These are recorded collection/data and authentication outcomes; the precise reason for Douyin’s empty search responses is not established, and is not described as a compiler or configuration defect.

Run, Job, audit revision and Pipeline observed rule/prompt matched exactly even in the failed B. The unchanged T4 baseline reader/hash validator accepted its exact formal snapshot, with no temporary null field. Formal identity/version/hash matched the Draft selection. These contract checks do **not** substitute for the missing successful real execution.

Formal source: `{"available": true, "content_hash": "f33abf75bc81b6ad2754e04c2201fa0d8cde364fba649c54c89ee77adf8d55cc", "domain": "gambling", "enabled_rule_count": 7, "id": "ruleset-revision:gambling:v2", "name": "赌博博彩风险规则集", "ruleset_id": "ruleset.gambling", "version": 2}`.

Evidence: [final DY search diagnostics](evidence/m3-t1-t5-integrated-formal-final.json), [prior DY empty attempt](evidence/m3-t1-t5-integrated-formal-dy-empty.json), [XHS authentication failure](evidence/m3-t1-t5-integrated-formal-xhs-login.json).

The noncanonical earlier A was stopped through existing Job control and ended `INTERRUPTED / audit_job_stopped`; its frozen configuration was untouched. Its one already-written result does not make that attempt accepted. See [rejected Recall journey and first B](evidence/m3-t1-t5-integrated-recall-review.json).

To reopen the commit gate, restore a working collection/account path for the existing Recall resource and obtain a genuine B `AUDIT_COMPLETED` result with the same snapshot comparisons. Do not replace existing Recall with temporary terms, fake content, relax validators or mark status successful manually. A has already passed; it need not be rerun unless a relevant code change requires it.

## File scope, evidence convention and historical stopping state

No files are staged and no commit was created. HEAD remains `ad47e537013c38fe031ec1bab9bd7486767e49cb`. The working tree intentionally retains T5 implementation, regressions, real acceptance runner, reports and sanitized evidence. Post-commit sanity is not applicable because the commit gate is blocked.

Under the repository’s existing convention, sanitized JSON evidence and normalized test summaries are intended for the eventual T5 commit, including failed history. SQLite backups, default database copies, provider/private debug logs, credentials, cookies, account ciphertext, crawler media/output and unrelated files are excluded. No unrelated modified or untracked files were identified.

### Production

- `backend/api/investigation_creation.py`
- `backend/audit_agent/pipeline.py`
- `backend/investigation_creation/adapters.py`
- `backend/investigation_creation/contracts.py`
- `backend/investigation_creation/conversation.py`
- `backend/investigation_creation/frozen.py`
- `backend/investigation_creation/resources.py`
- `backend/investigation_creation/service.py`
- `backend/investigation_creation/store.py`
- `backend/investigation_creation/worker.py`

### Tests and acceptance runner

- `tests/test_adoption_artifact_receipts.py`
- `tests/conftest.py`
- `tests/m3_t5_execution_acceptance.py`
- `tests/test_ruleset_proposal_approval.py`
- `tests/test_temporary_ruleset_execution.py`

### Frontend compatibility tests

- `Audit_assistant/tests/mixedProposalPresentation.spec.ts`

### Documentation and evidence

- `docs/evidence/m3-adoption-hardening-validation.json`
- `docs/M3_PHASE_T5_IMPLEMENTATION_REPORT.md`
- `docs/M3_T1_T5_FINAL_REVIEW.md`
- `docs/evidence/m3-t1-t5-final-deterministic.txt`
- `docs/evidence/m3-t1-t5-final-draft-config.txt`
- `docs/evidence/m3-t1-t5-final-focused.txt`
- `docs/evidence/m3-t1-t5-final-frontend.txt`
- `docs/evidence/m3-t1-t5-final-full-pytest.txt`
- `docs/evidence/m3-t1-t5-final-typescript.txt`
- `docs/evidence/m3-t1-t5-integrated-formal-dy-empty.json`
- `docs/evidence/m3-t1-t5-integrated-formal-final.json`
- `docs/evidence/m3-t1-t5-integrated-formal-xhs-login.json`
- `docs/evidence/m3-t1-t5-integrated-recall-review.json`
- `docs/evidence/m3-t1-t5-integrated-temporary-final.json`
- `docs/evidence/m3-t1-t5-integrated-transcript-failure.json`
- `docs/evidence/m3-t1-t5-integrated.json`
- `docs/evidence/m3-t5-compiler_m1_m2.txt`
- `docs/evidence/m3-t5-default-store-observation.json`
- `docs/evidence/m3-t5-deterministic.txt`
- `docs/evidence/m3-t5-focused.txt`
- `docs/evidence/m3-t5-formal-golden.json`
- `docs/evidence/m3-t5-full_pytest.txt`
- `docs/evidence/m3-t5-git-status.txt`
- `docs/evidence/m3-t5-real-audit-result-provenance.json`
- `docs/evidence/m3-t5-real-execution-dy.json`
- `docs/evidence/m3-t5-real-execution-final.json`
- `docs/evidence/m3-t5-real-execution-retry.json`
- `docs/evidence/m3-t5-real-execution-verified.json`
- `docs/evidence/m3-t5-real-execution.json`
- `docs/evidence/m3-t5-typescript.txt`
- `docs/evidence/m3-t5-validation.json`

All untracked files listed above are intentional implementation or validation additions. No amend, reset, restore, checkout, clean, stash, tag, squash or push was performed. No Deep-link/Feature A, streaming, formal save, claim citation, MediaCrawler modification or automatic Report generation is included.
