> Historical phase-end record. The later final review and integrated commit gate are recorded in [M3 T1–T5 final review](M3_T1_T5_FINAL_REVIEW.md). Earlier status and failure history below are preserved.

# M3 T5 — Temporary RuleSet Confirm, freeze and execution

Baseline: `ad47e537013c38fe031ec1bab9bd7486767e49cb`. Implementation is uncommitted. No push.

Status: **M3_PHASE_T5_PASS**. Required deterministic validation and real A–H acceptance are complete. P0 = 0; P1 = 0. No commit or push.

## Frozen source and Confirm

The existing v4 confirmed snapshot now accepts exactly one source: the historical `ruleset_revision`, or `temporary_ruleset` containing strategy, Proposal ID/version, content hash and full inline `RuleSetContent`. The inactive source is omitted from serialization. Formal snapshots do not acquire a null temporary field, and their canonical hash payload is unchanged. v2/v3 readers remain supported.

Temporary Confirm uses the persisted expected Draft revision's inline judgement. Proposal identity is provenance only. No Confirm, Run, Job or execution helper reads the live Proposal or looks up a formal RuleSet for the temporary branch. Formal resolution retains published revision identity/version/hash/current publication checks and the original formal compiler wrapper.

The temporary adapter calls the existing `compile_ruleset_content` without changing compiler semantics. Shared execution packaging freezes compiled rules, prompts, routes/category identity, compiler/template/prompt versions, audit configuration and the existing compilation hash. For RuleSet-direct v4 execution, the Run's outer configuration hash and compilation hash are recomputed against their own canonical payloads, never compared with one another. Historical policy-based execution retains its original hash contract: its task revision payload and saved hash must equal the frozen Run. Missing historical policy inputs are not invented or fetched live to reconstruct a different hash.

Before Run INSERT, the creation-store transaction checks current/expected revision, the durable revision row against the current Draft, exact source content/provenance/hash, platform and investigation/Recall parameters, and compiler output against the proposed execution. Validation or INSERT failure rolls back Run creation and Draft queueing. The successful creation transaction remains the freeze boundary.

## Run, Job and audit configuration

Run is the authoritative frozen configuration. Job is an execution materialization. Create/reuse compares every frozen execution field represented by Job, including collection and analysis parameters, capabilities, exact rule/prompt payloads and source identity. The task-audit revision must belong to this Job and match every frozen audit-config field and compiled hash.

Mismatches reject execution. Neither Run nor Job snapshots are rewritten or merged. A missing audit revision can be created by the existing recovery flow; attaching it uses a conditional update so a concurrently attached revision is never overwritten. The final reread is validated before allowing execution. A conflicting attachment fails closed.

Run and Job are still separate database commits. A valid frozen Run can exist after Job or revision creation fails. Worker records failure/interruption according to existing semantics and does not start Pipeline. This implementation makes no distributed transaction or crash exactly-once claim.

## Validate and consume

The shared verifier reads Run, Job and task-audit revision, validates them and returns detached copies of the exact Job rule/prompt and revision payload it just checked. Pipeline's M3 first-run entry invokes this verifier at consumption, then uses that returned copy. It does not validate Job A and subsequently reread rule/prompt B.

Pending-analysis resume invokes the same boundary, including direct M3 Job resume outside the Worker entry. Resume uses Run's frozen runtime parameters and validated Job rule/prompt/revision, including the frozen analysis limit. Worker recovery and completion classification also reject current Job/revision mismatches before accepting results. Ordinary non-M3 pipeline behavior is retained. Successful confirmed M3 execution ends at `AUDIT_COMPLETED`; no automatic Report branch was added.

The helper is a local canonical comparison/packaging seam, not a new execution identity resource, registry, lifecycle, compiler, Worker or Pipeline. Frozen execution does not recompile against a potentially changed compiler during consumption; Confirm establishes the source-to-compiled proof before freeze.

## Preview and frontend

The three unconditional temporary execution blockers were replaced with authoritative source/compilation validation. Preview performs readiness checks without Run/Job writes. A valid temporary Draft with satisfied existing configuration/account prerequisites can confirm; invalid content/hash/compiler/configuration still fails closed. Both HTTP Confirm and the existing tool call use the same application command. Corrupt persisted content/hash now returns a JSON-serializable 422 response: the M3 HTTP error mapper excludes exception objects from Pydantic error contexts. Real case F exposed this transport defect; both Preview and Confirm have regression tests and still create no Run/Job. The Qwen system prompt no longer says execution is blocked until T5; it requires separate execution confirmation and uses the bound Draft snapshot.

Existing frontend rendering already supports the backend `can_confirm` contract and temporary label. Two desktop/mobile tests add ready-state Confirm and refresh coverage; no redesign or second approval control is included. Historical blocked-state rendering remains tested.

## Validation

Only the requested `.venv-r0-2-1/bin/python` interpreter was invoked for backend tests and acceptance harnesses. Raw test summaries and frontend comparison are in [validation evidence](evidence/m3-t5-validation.json).

| Check | Result |
| --- | --- |
| T5 deterministic cases | 41 passed |
| T5/T4B/T4A/T3/M3 creation/conversation/provider focused suite | 389 passed, 22 subtests |
| Compiler/category/foundation and M1/M2 offline | 188 passed, 7 skipped, 19 subtests |
| Full pytest | 919 passed, 24 skipped, 0 failed, 66 subtests; 5 dependency/deprecation warnings |
| Baseline formal v4 reader/hash roundtrip | PASS against unchanged contract loaded directly from baseline git object |
| TypeScript | PASS |
| All frontend | 71 passed (47 relevant), 2 skipped, 5 existing failures |
| Frontend baseline comparison | All five failure names and full assertion messages equal saved baseline evidence |
| Final self-review | P0 = 0; P1 = 0; real acceptance PASS |
| Diff checks, including new-file whitespace | PASS |

One focused invocation loaded default stores before module-local isolation. The legacy API test created two test Jobs, and startup recovery marked two prior test Jobs interrupted. After a private SQLite backup, the two new Jobs/revisions/logs were removed and the two prior statuses were restored from explicit recovery-log evidence. The prior rows’ execution fields exactly matched the corresponding legacy API test payloads; their preceding log timestamps were retained. This is not a byte-identical pre-test database restoration claim. Details are in [default-store observation](evidence/m3-t5-default-store-observation.json).

`tests/conftest.py` now establishes isolated data/output/Hermes storage before any test-module imports. Standalone T5 (41), the formerly failing focused import order (389), and full pytest (919) all pass. Default data/output file fingerprints are unchanged across these final runs. No test assertion was weakened or skipped.

Tests cover corrupt content/hash, stale revision, forged resolutions/snapshots, compiler and Run INSERT failure, replay/conflict, exact existing Job reuse, rule/prompt/runtime/revision mismatches, Job/revision creation failures, concurrent revision attachment, detached-copy consumption on first run and resume, recovery/classification, HTTP/tool Confirm, formal shape/hash and execution, and Proposal changes/unavailability after binding.

## Real execution evidence

The runner `tests/m3_t5_execution_acceptance.py` uses isolated Session/database/data/output storage, a real Qwen authoring conversation, HTTP Confirm and the unchanged real Worker/Pipeline/crawler/provider. It copies existing authorized encrypted accounts into the isolated database; credentials and provider logs are not exported. B–D use controlled durable T4B setup; their actual execution is not scripted. E–G inject explicit failures. H executes the formal branch.

Acceptance combines per-case evidence from retained attempts. All five positive execution cases reached `AUDIT_COMPLETED`, each with one durable real audit result. For every positive case, Run execution, Job rules/prompts, task-audit revision and the Pipeline's observed consumption were compared exactly. Result revision IDs and Prompt versions also match. Temporary A–D all freeze approved v2 inline content; H preserves the formal published revision identity, version, hash and shape.

| Case | Verified result | Evidence |
| --- | --- | --- |
| A — real Qwen generate/edit/present/adopt; HTTP Confirm; real Worker | PASS; v2; `AUDIT_COMPLETED`; one result | [A/B batch](evidence/m3-t5-real-execution-dy.json) |
| B — Proposal updated to v3 before Confirm | PASS; Draft/Run/Worker remain v2; one result | [A/B batch](evidence/m3-t5-real-execution-dy.json) |
| C — Proposal reads unavailable | PASS; real Confirm/Worker complete; one result | [C/D/E batch](evidence/m3-t5-real-execution-retry.json) |
| D — Proposal updated after Confirm | PASS; frozen v2 executed; one result | [C/D/E batch](evidence/m3-t5-real-execution-retry.json) |
| E — stale expected Draft revision | PASS; HTTP 409; zero new Run/Job | [C/D/E batch](evidence/m3-t5-real-execution-retry.json) |
| F — corrupt inline hash | PASS; HTTP 422; zero new Run/Job | [F/G/H batch](evidence/m3-t5-real-execution-final.json) |
| G — compiler failure | PASS; HTTP 400; zero new Run/Job | [F/G/H batch](evidence/m3-t5-real-execution-final.json) |
| H — existing formal RuleSet | PASS; `AUDIT_COMPLETED`; one result | [F/G/H batch](evidence/m3-t5-real-execution-final.json) |

The [A/B result provenance observation](evidence/m3-t5-real-audit-result-provenance.json) supplements their public result projections with the actual persisted revision/Prompt-version relationship. Later batches record that provenance directly. C/D/E and F/G/H explicitly verify unchanged formal RuleSet/revision/publication tables and zero Reports. The first A/B batch exited on C's provider failure before its final whole-database comparison; its individual successful execution records remain complete.

Retained history includes an initial Qwen confirmation reask, an XHS `crawler_account_login_required` failure, and a first C `audit_provider_failed` result. The next batch completed C/D/E and exposed F's non-serializable validation-error context. That defect was fixed, covered by two HTTP regression cases, and F/G/H then passed. Earlier whole-batch `pass: false` values are retained. The final F/G/H batch is `pass: true`; [validation evidence](evidence/m3-t5-validation.json) identifies the accepted record for each A–H case and verifies its source/consumption/result relationship.

The final Qwen prompt change removes obsolete execution-blocked wording. It leaves T4B semantic adoption and its structural authority checks unchanged. Real execution uses the original Worker/Pipeline/crawler/providers with no fake execution response, no temporary runtime fork and no rule replacement.

## Scope and residual boundaries

No compiler semantic changes, MediaCrawler edits, formal save/publish, Proposal cleanup/delete, streaming, M1/M2 behavior change, report redesign or automatic M3 Report generation are included. The existing T4B semantic approval contract remains unchanged.

The 24 backend skips, 2 frontend skips and five existing frontend failures remain visible limitations. Provider/account availability is an independent real-execution prerequisite; recorded transient failures are not a reliability guarantee. No commit has been made; the authoritative baseline remains the T4 SHA above. The working tree intentionally contains T5 implementation/tests/evidence/report changes; final status is recorded in [git status evidence](evidence/m3-t5-git-status.txt).
