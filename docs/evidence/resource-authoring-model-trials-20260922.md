# Resource authoring model trials — 2026-09-22

## Decision

The dedicated company-GPT resource-authoring path was an experiment and is not part of the selected product path. It was removed from the active branch after the DMX Qwen/Hermes path produced acceptable live results. The experiment remains recoverable from:

- tag: `checkpoint/multi-user-resource-authoring-20260922`
- commit: `a48ddbb517243b9432fb31424c0da8f4e02972b3`

No API key is recorded in this evidence file.

## Company-GPT experiment

The retired path handled only audit-rule and lexicon generation. It used a dedicated Responses-compatible client, strict JSON Schema output, provider-error classification, timeout/output-token controls, validation before tool writes, and periodic progress messages. When enabled, it intercepted resource-generation turns before the normal Hermes/Qwen conversation path.

Automated coverage exercised structured response parsing, nested output parsing, provider error classification, invalid-output rejection, resource creation without partial writes, replay idempotency, and enabled-variant trimming. This path was useful as a feasibility check but added a second generation architecture and configuration surface without improving the accepted Qwen result enough to justify keeping it.

Removed active-code surface:

- `backend/investigation_creation/resource_authoring.py`
- `RESOURCE_AUTHORING_*` settings in `backend/audit_agent/config.py`
- the special routing and progress-feedback branch in `backend/investigation_creation/conversation.py`
- company-GPT-specific tests

## Qwen/Hermes live results

Provider/model used for the accepted reproduction:

- OpenAI-compatible endpoint: `https://www.dmxapi.cn/v1`
- model: `qwen3.7-plus`
- normal Hermes conversation and tool path

First reproduction:

| Case | Status | Duration | Finish reason | Provider/content failure |
| --- | --- | ---: | --- | --- |
| Sexual-service solicitation lexicon | completed | 43.394 s | stop | none |
| Uyghur–Han marriage lexicon | completed | 51.255 s | stop | none |

Neither run reported `DataInspectionFailed`, `content_filter`, output truncation, or tool-call failure.

Final 5–7-term prompt trial:

- status: `completed`
- duration: 39.414 s
- finish reason: `stop`
- generated enabled variants: 7
- generated disabled variants: 0
- saved automatically: no
- Drafts created automatically: 0
- Runs created automatically: 0
- search terms: `sp服务`, `约拍私聊`, `同城可约`, `全套过夜`, `陪玩陪聊`, `资源主页看`, `加V详谈`

The retained prompt behavior is:

- topic main terms are semantic grouping and do not count toward the default limit;
- default output contains 5–7 enabled search variants;
- low-value, overly broad, direct, duplicate, or otherwise unsuitable candidates are omitted instead of emitted as disabled entries;
- fewer than five variants are allowed when the quality bar cannot be met;
- existing disabled entries remain editable and explicit requests for alternatives remain supported.

## Incidents and lifecycle verification

One earlier run created a valid lexicon edit but the turn later failed while producing the natural-language summary. The failure was after the structured tool call, not in JSON Schema validation, persistence, Drawer projection, or the database. Adding “authorized investigation” wording was not reliable and was not retained as a bypass strategy.

The later lifecycle error was test-harness-only:

```text
TypeError: 'dict' object is not an iterator
```

`m3_stack.__wrapped__(...)` already returned a dictionary, but the acceptance script called `next()` on it. After correcting the script, the lifecycle checks passed:

- Draft revision `1 → 2`;
- stale revision rejected;
- latest revision saved;
- repeated save idempotent;
- persisted `search_terms` matched the Drawer;
- persisted lexicon JSON matched the final Drawer content.

## Active product path after cleanup

Resource generation now has one product route: Hermes/Qwen plus the existing structured resource tools. The keyword Drawer, Draft/save lifecycle, projection rules, and the 5–7 enabled-variant prompt constraints remain active. The frozen tag is unchanged and can be used to inspect or recover the retired experiment.
