# T3_AUTHORING_QUALITY_FIX_REPORT

Status: **T3_AUTHORING_QUALITY_FIX_PASS**.

## Scope

This follow-up changes only generic Temporary RuleSet Proposal authoring guidance in production:

- `backend/investigation_creation/conversation.py`: the existing M3 creation system prompt.
- `backend/investigation_creation/tools.py`: descriptions of `create_ruleset_proposal` and `update_ruleset_proposal`.

The existing opt-in `tests/m3_t3_qwen_acceptance.py` runner adds an `--authoring-quality` mode, reusing its disposable database, separate Sessions, real Hermes/Qwen execution, receipt checks, compiler/hash checks, and side-effect assertions. Existing deterministic tests and the original five-case acceptance path are unchanged.

Runtime changed: **no**. Compiler routing, exemption enforcement, RuleSetContent and Proposal schemas, create/update/get implementations and argument contracts, persistence, evidence filtering, fusion logic, M1/M2, Streaming and T4 are unchanged by this follow-up. No Application semantic validator, domain-specific generation implementation, forced tool sequence or tool gate was added.

The existing uncommitted T3 implementation was preserved. A start/end SHA-256 comparison across the 600 existing tracked and untracked workspace files confirmed that only the two guidance files and acceptance runner changed during this follow-up; the report and two focused evidence files are new. HEAD remains `59fba20ac3ba19bce20eff29296f59056c05b1c5`. No commit was created.

## Guidance

The prompt lists the four canonical stage identifiers and explains that image/video stages both extract evidence and apply business rules; video includes OCR and ASR. Comment auditing concerns comments themselves, and fusion matches rules against existing evidence rather than rereading all media.

Stage selection follows the modalities in which a risk can independently appear. The prompt discourages both a blanket comment-plus-fusion assignment and a blanket four-stage assignment. Advance-payment recruitment content illustrates a risk that can appear in all source modalities. Comment participation and pure cross-evidence rules illustrate narrower assignments.

A rule intended to support a final finding must explicitly include `fusion_audit`, including independently sufficient single-modality risks. The compiler does not add it automatically. Discovery-only assignments are reserved for intentional prerequisites covered by explicit final rules.

Both exemption fields are described as strong conditions that remove the corresponding risk. They must negate risk, rather than supply background, confidence adjustments or small downgrades. General exemptions must work across the rules they can exempt. Enterprise certification, blue verification badges, official accounts, matching business scope, institutional reputation and real-name verification alone cannot negate explicit harmful behavior or justify a general exemption. This is generic guidance; recruitment is only an explanatory example.

## Final Real Qwen Acceptance

Primary evidence: [m3-t3-authoring-quality-qwen-verified.json](evidence/m3-t3-authoring-quality-qwen-verified.json).

Model: `qwen3.7-plus`; runtime: Hermes `0.20.4`. The two cases used separate new Sessions and temporary state. User messages requested domain coverage without specifying stage values or desired exemption outputs. Generated tool arguments were persisted and inspected without postprocessing or repair by the harness.

| Case | Rules | Image | Video | Comment | Fusion | LLM calls | Successful create receipts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Recruitment fraud | 4 | 4 | 4 | 4 | 4 | 3 | 1 |
| Gambling promotion | 9 | 6 | 6 | 8 | 9 | 3 | 1 |

Both turns completed on their first attempt under the final guidance. Each called `create_ruleset_proposal` once, compiled successfully, matched the Application content hash and had a matching durable SUCCEEDED receipt. Drafts and formal resources were unchanged. Run, Job and report counts were zero. No Proposal was approved, published, bound or executed.

### Recruitment

The final candidate has four behavior rules:

| Rule ID | Behavior | Stages |
| --- | --- | --- |
| `pre_employment_fee` | Explicit charges before employment, including deposits and training fees | All four |
| `training_loan` | Recruitment-related training loans or installment/payment demands | All four |
| `sensitive_info_request` | Requests for verification codes, bank/payment passwords or other sensitive financial credentials | All four |
| `transfer_scam` | Recruitment-related demands for transfers or remittances | All four |

The original 9/9 comment-plus-fusion pattern is absent: zero of the new four rules use that pair alone. These four rules describe independently observable behaviors, so their four-stage distribution fits the evidence sources. `pre_employment_fee` explicitly includes `image_evidence`, `video_frame_evidence`, `comment_audit` and `fusion_audit`.

New `general_exemptions`: `[]`. All four `rule_exemptions` arrays are also empty. There is no certified-enterprise, blue-badge, official-account or matching-business-scope exemption that can erase advance fees, training loans, sensitive-information requests or fraudulent transfer demands.

### Second Domain

The final gambling candidate demonstrates three distinct stage assignments:

- Six rules use all four stages: platform links, QR/group entrances, deposit/withdrawal instructions, funding channels, agent recruitment and referral codes.
- Two comment-related rules use `comment_audit` plus `fusion_audit`: `comment_recruitment` and `comment_coordinated_promotion`.
- `cross_modal_gambling_chain` uses only `fusion_audit`.

All independently sufficient non-comment behavior rules are available to fusion. The cross-modal rule does not require copying every rule to all four stages.

Its sole general exemption, `legal_lottery_official`, requires content to concern lawful official welfare/sports lottery publicity, draw information or public-interest material AND excludes offshore gambling, private/underground lotteries and illegal platforms. It is a content-scope condition, not an official-account identity exemption alone. All rule-level exemption arrays are empty.

The candidate remains a Proposal, not approved business policy. In particular, `comment_coordinated_promotion` mentions multiple comments: any aggregate coordination conclusion belongs to fusion; the comment stage must remain limited to each comment's evidence. This wording merits candidate review before future approval. The smoke establishes improved stage selection and exemption authoring, not exhaustive business-condition correctness or measured media recall.

## Preserved Initial Evidence

[m3-t3-authoring-quality-qwen.json](evidence/m3-t3-authoring-quality-qwen.json) preserves the first guidance iteration. Recruitment already improved to eight four-stage rules with no exemptions. The ten-rule gambling candidate varied its discovery stages, but seven independently meaningful rules omitted fusion, leaving only comment rules and a full closed-loop rule available for final matching.

That observation led to the generic explicit-fusion instruction above. Both domains were then regenerated in fresh Sessions with the final guidance. No compiler/runtime change, manual Proposal rewrite or hidden retry was used to obtain the verified results.

## Regression and Reproduction

Only this Python executable was used:

`/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-product-baseline-r0-2-1/.venv-r0-2-1/bin/python`

Final guidance validation:

| Check | Result |
| --- | --- |
| Existing Proposal, compiler, M3 conversation/Draft and M1/M2 targeted suites | 377 passed, 7 skipped, 19 subtests passed; 21.95 seconds |
| Full pytest | 811 passed, 24 skipped, 66 subtests passed; 82.55 seconds |
| Focused Real Qwen generation | 2/2 completed; both candidates reviewed for stages and exemptions |
| `git diff --check` | PASS |
| Existing deterministic tests, compiler/runtime/schema and M1/M2 file hashes | Unchanged from this follow-up's starting state |

Reproduction commands, using the executable above as `PY` and the existing local provider environment file as `LOCAL_CREDENTIALS_ENV`:

```sh
"$PY" -B tests/m3_t3_qwen_acceptance.py --credentials-env "$LOCAL_CREDENTIALS_ENV" --output /tmp/t3-authoring-quality-qwen.json --authoring-quality
"$PY" -B -m pytest -q tests/test_ruleset_proposals.py tests/test_ruleset_content_compiler.py tests/test_ruleset_compiler_generalization.py tests/test_ruleset_category_identity.py tests/test_ruleset_foundation_gambling.py tests/test_investigation_creation_conversation.py tests/test_investigation_draft_configuration_m3.py tests/test_hermes_m1_m22_offline.py tests/test_m1_finding_comment_provenance.py
"$PY" -B -m pytest -q
git diff --check
```

Generation records, arguments, model answers, stage statistics, exemptions and mutation receipts are retained in the evidence files. Credentials are not included. No further implementation or commit follows this report.
