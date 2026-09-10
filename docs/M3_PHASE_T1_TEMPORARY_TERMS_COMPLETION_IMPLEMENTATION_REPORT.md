# M3_PHASE_T1_TEMPORARY_TERMS_COMPLETION_IMPLEMENTATION_REPORT

Workspace: `/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-m3-authoritative-wiring`

Branch: `codex/m3-authoritative-crawler-account-wiring`

Baseline and final HEAD: `16b400ca0e0cd4ee530043590e3eef626e7be778`

Baseline commit: `feat: establish authoritative M3 creation through phase 2c` (`M3_PHASE_2C_PASS`). No commit was made.

## Implementation and validation

| # | Required item | Result |
|---|---|---|
| 1 | Git status before / after | The designated workspace was clean before edits. Final expected changes are listed below; no baseline or unrelated files were reverted. The initial desktop workspace was a different dirty checkout and was not edited. |
| 2 | Baseline HEAD | Exact match with the supplied 40-character SHA; initial `git log -1 --oneline` and `git diff --check` passed. |
| 3 | Changed files | Six creation backend files, two existing test files, one isolated acceptance script, its evidence JSON, and this report. |
| 4 | Qwen generation behavior | System prompt and create/update descriptions prefer sufficiently suitable existing Lexicons; otherwise conversation authorization permits focused canonical terms inline in the Draft command. No generation tool added. |
| 5 | No authorization | Explain missing Recall, propose temporary term generation, end the turn with no generated terms and no Draft. Even illustrative candidate terms must wait for authorization. Investigation intent alone is insufficient authorization. |
| 6 | Preauthorization | Reuse authorization from the full conversation; with investigation intent and valid Judgement, generate terms and create the Draft in the same turn without asking again. |
| 7 | Schema | Draft configuration remains v4. `temporary_terms` remains the strategy with `terms: list[StrictStr]` and `source_lexicon_ids: list[StrictStr]`; no entries schema, store, or migration added. |
| 8 | Canonical main terms | Prompt asks for focused, concrete core concepts usable as search strings, one canonical wording per concept, without explanatory text, synonymous restatements, broad umbrella-word padding, or mechanical expansion. Semantic relevance remains Qwen/user responsibility. |
| 9 | Variants / query_type | No generation, expansion, new fields, editors, or runtime changes. Tests intentionally mutate source Lexicon variants to prove they never enter temporary execution. |
| 10 | Typed / Application validation | Existing StrictStr, trim, empty removal, stable-order dedupe, authoritative resource checks, ownership, DRAFT status, revision checks, CAS, and receipts remain. No semantic relevance validator or generation permission flag. |
| 11 | Comma ambiguity | `TemporaryTermsRecallPlan.reject_transport_separator` rejects ASCII comma in any individual normalized term. No substitution, splitting, or private error family. Existing Lexicon normalization is unchanged. |
| 12 | Pre-receipt | Create and update tests assert `INVALID_TOOL_ARGUMENTS`, `receipt_created=false`, `mutation_applied=false`, zero receipt rows, no Application command call, and no Draft insertion or revision change. |
| 13 | Term / payload limits | Read-only audit below found no grounded fixed per-term or total-payload product limit. Existing 100 normalized terms and 20 normalized provenance IDs remain. |
| 14 | Magic numbers | No new term length, byte length, payload size, or term-count product limit introduced. The 101-item test exercises the existing 100-term limit. |
| 15 | source_lexicon_ids meaning | Write-time provenance describing formal Lexicons referenced while preparing inline terms. It is not a collection dependency and never contributes terms or variants. |
| 16 | Provenance validation location | `InvestigationCreationService.create_draft/update_draft` compare normalized source lists and call `InvestigationResourceService.validate_temporary_provenance` inside the existing resource fence before store mutation. Create validates all nonempty refs; update validates every ref in a changed nonempty list. Removing all refs is valid. |
| 17 | Shared resolver | `resolve_authoritative_draft` no longer resolves provenance, and has no stage flag. Runtime resource truth continues to include actual existing Lexicon and RuleSet dependencies. |
| 18 | Source deletion | Preview/Confirm still succeed using saved terms. Tests forbid even calling `get_category` during temporary Preview/Confirm after source deletion or drift. Source IDs remain in the frozen provenance snapshot. |
| 19 | Existing Lexicon regression | Existing ID/hash/authoritative enabled_main_terms behavior remains; missing resources block Preview and reject Confirm, while runtime hash drift remains RESOURCE_STALE. |
| 20 | Update | Normalized term edits save one new revision. Title, objective, platform, and term-only edits succeed with unchanged deleted provenance. New fake refs reject without a revision. Draft Card no longer recommends provenance as a runtime Lexicon. |
| 21 | Freeze / runtime | Confirm freezes current inline terms into resolved_search_terms, the recall snapshot, and comma-joined execution.keyword. No term generation, source reread, Draft rewrite, or revision increment during Preview/Confirm. |
| 22 | Worker / MediaCrawler | Unchanged. No worker, crawler, account, RuleSet compiler, or formal Lexicon implementation work. |
| 23 | Offline targeted tests | Final run: **192 passed, 22 subtests passed**, zero failures. Three existing M3 test modules listed below. |
| 24 | Full pytest | Final run: **649 passed, 24 skipped, 66 subtests passed**, zero failures, 5 existing deprecation warnings. Baseline was 632 passed; net increase is 17 (18 added cases, one obsolete provenance-liveness expectation removed). |
| 25 | Frontend | Node v24.19.0 and pnpm 11.19.0 from the existing runtime. `pnpm typecheck` and `VITE_API_PROXY_TARGET=http://127.0.0.1:8010 pnpm build` passed. Existing Vite large-chunk advisory only. No frontend or lockfile changes. |
| 26 | Real Qwen | **PASS**: all four final isolated Hermes/Qwen scenarios completed. Existing Lexicon selected when available; missing/unapproved Recall produced no candidate terms and no Draft; preauthorization created a temporary Draft in the same turn; edit changed revision 1 to 2. See final evidence below. |
| 27 | Residual gaps | No established per-term or total-payload bound; platform/service and process transport limits remain unquantified. Semantic term quality and generation authorization are prompt/conversation responsibilities; deterministic scripted tests do not prove model behavior for all inputs. Live crawling is outside this phase. |
| 28 | Diff review | Final implementation diff checked for the requested scope and `git diff --check` passed. Eight tracked files changed (292 insertions, 36 deletions), plus three new report/evidence/acceptance files. |

## Length and transport audit

| Layer | Grounded finding |
|---|---|
| Tool/request and Pydantic | Create/update use the existing v4 typed configuration. Terms have max_length=100 as a list; provenance has max_length=20 as a list. Neither individual term nor combined payload has an established business limit. |
| Draft JSON/store | `investigation_creation/store.py` persists configuration_json and confirmed_configuration_json as SQLite TEXT. No custom per-term or payload check. |
| Frozen execution / Job | `ResolvedExecutionConfiguration.keyword` is StrictStr without a length cap; `audit_agent/job_store.py` stores keyword as TEXT. |
| Adapter | `investigation_creation/adapters.py` joins terms using ASCII comma. `audit_agent/crawler_adapter.py` passes the string as the --keywords argument without a product length cap. OS argv limits depend on runtime/environment and do not establish a portable per-term product limit. |
| MediaCrawler CLI | Read-only inspection of `/Users/ext.wanghongtao6/Documents/Codex/projects/MediaCrawler/cmd_arg/arg.py`: --keywords is a string, assigned directly to config.KEYWORDS. |
| Platform search loops | XHS core.py:240, DY core.py:229, KS core.py:234 each use config.KEYWORDS.split(","). This proves the mandatory ASCII-comma constraint. |
| Platform request construction | XHS client.py:get_note_by_keyword, DY client.py:search_info_by_keyword, KS client.py:search_info_by_keyword pass keyword into request parameters without a declared length bound. No claim is made about undocumented remote platform limits. |

## Test coverage

| Cases | Evidence |
|---|---|
| A | Existing resource conversation and authoritative snapshot regressions; real existing-resource acceptance. |
| B/C | Deterministic missing-resource scripted conversations plus real Qwen without/with preauthorization. Scripts exercise the real conversation/tool/Application boundary; they do not simulate independent model reasoning. |
| D/E/F | Flat strict schema, normalization and stable dedupe, forbidden extra variants/query_type, comma rejection on create and update before receipt. |
| G/H | No invented length/payload failure; existing list-count limit tested. |
| I | Existing all-empty test preserves editable NO_SEARCH_TERMS and Confirm refusal. |
| J/K/N | Valid create provenance succeeds; missing create refs and changed fake update refs reject; changed ref list rechecks all refs. |
| L/M/O | Ten combinations of source delete/drift with no edit, title, objective, collection, and terms edits; Preview/Confirm are independent of source liveness/content. |
| P/Q | Existing deletion/hash drift regressions retained; temporary execution contains exactly normalized inline terms, no source main terms or variants. |
| R/S | No implicit Run; explicit confirmation, receipt replay, idempotency, expected revision, CAS, ownership and DRAFT-state regressions pass in targeted/full suites. |

Only Python used: `/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-product-baseline-r0-2-1/.venv-r0-2-1/bin/python`.

Targeted invocation: that interpreter with `-m pytest tests/test_investigation_draft_configuration_m3.py tests/test_investigation_creation_conversation.py tests/test_investigation_creation_m3.py -q`. Full invocation: the same interpreter with `-m pytest -q`.

The real acceptance runner uses the actual Hermes creation runtime and Qwen model, disposable `/tmp` databases/home, synthetic account state, and the six existing business tools. It reads only provider key/base URL from the supplied credentials file, never preserved resource DBs or crawler authentication. No Worker is started. The evidence records public answers, actual business mutation dispatch names (`tools`), outer Hermes tool names (`hermes_tools`), Draft recall plans, revisions, zero Run count, and unchanged formal Lexicon tables. Read-tool calls are represented by the outer Hermes trace, not the mutation list.

## Final changed-file inventory

```text
 M backend/investigation_creation/contracts.py
 M backend/investigation_creation/conversation.py
 M backend/investigation_creation/ports.py
 M backend/investigation_creation/resources.py
 M backend/investigation_creation/service.py
 M backend/investigation_creation/tools.py
 M tests/test_investigation_creation_conversation.py
 M tests/test_investigation_draft_configuration_m3.py
?? docs/M3_PHASE_T1_TEMPORARY_TERMS_COMPLETION_IMPLEMENTATION_REPORT.md
?? docs/evidence/m3-t1-qwen-acceptance.json
?? scripts/m3_t1_qwen_acceptance.py
```

No commit, reset, restore, checkout, clean, new venv, lockfile modification, or whole-file rollback. No T2 implementation.

## Final real acceptance

Evidence: `docs/evidence/m3-t1-qwen-acceptance.json`. Runner: `scripts/m3_t1_qwen_acceptance.py`.

| Case | Final observed behavior |
|---|---|
| Existing resource | Selected existing_lexicon `gambling`, authoritative enabled_main_terms snapshot, published RuleSet v2; created one unconfirmed Draft. |
| Missing Recall, no authorization | Explained the missing resource and offered generation. Public answer contains no candidate/example terms, no Draft artifact, and no mutation dispatch. |
| Missing Recall, preauthorized | Generated 12 inline canonical strings describing specific activities/offers and created a temporary_terms Draft at revision 1 in the same turn. No variants, query_type, or formal resource save. |
| Edit | Deleted the selected first term, retained the other 11 in order, and updated the same Draft to revision 2 through update_investigation_draft. |

All four final cases had zero Runs and unchanged formal Lexicon tables. No confirmation command was dispatched. The real runner performs structural checks; the public no-authorization reply and canonical-term focus were additionally reviewed against the T1 behavior requirements.

Acceptance work exposed two issues before this final run: the harness initially mistook Hermes's outer `tool_call` name for a business command, and exploratory model replies volunteered example terms before authorization or padded the generated list with broad synonyms. The harness now records actual mutation dispatches; the final prompt removes concrete sample terms, supplies a term-free authorization proposal, and directs one specific canonical wording per concept. The final run passed with the original controlled user messages. This is evidence for those scenarios, not a guarantee of identical model behavior across all future conversations; no server-side permission or semantic filter was added.

Reproduction uses the required interpreter with:

```text
scripts/m3_t1_qwen_acceptance.py --credentials-env <provider-credentials-env> --output <sanitized-evidence-json>
```

## Final status

**M3_PHASE_T1_PASS**

Next phase recommendation only: **T2 - RULESET CONTENT-ONLY COMPILER SEAM**. T2 was not implemented. Work stopped without a commit.
