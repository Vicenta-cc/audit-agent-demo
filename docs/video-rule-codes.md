# Video rule codes

RuleSet v2 video sheet requests use request-local `R01`, `R02`, … codes in the frozen `video_frame_evidence` rule order. Prompt assembly replaces complete rule tokens without changing rule descriptions or evidence/exemption IDs. The request record retains the exact code-to-stable-ID mapping. This protocol is scoped to video sheet judgments, including their visual, OCR and ASR risk items; other stages keep their existing identifiers.

The model selects codes from the supplied list. Decoding is exact: unknown codes, case variants, joined codes and full stable IDs are rejected. The program restores full rule IDs before native rule, exemption, score and evidence validation. Grades, reasons and source references remain unchanged by decoding. Missing codes on non-none judgments continue to fail native validation.

`video_review_requests/` stores each request's mapping, prompt, parsed response, captured provider response when available, and validation result. Contract failures also retain the input sheet under `video_review_failures/`. One corrective request includes the original instructions, validation error and allowed codes/references. A second failure propagates to the existing job failure boundary. Existing provider transport handling remains in effect; this helper adds no transport retries.

Validation on 2026-09-10: 318 regression tests and 19 subtests passed. After adding protection for malformed arrays during failure logging, the final affected suite passed 160 tests and 19 subtests. Tests cover frozen mapping order, positive risk preservation, exact rejection, exemption/reference token preservation, corrective success and two failures stopping.

One isolated live replay of failed post `7683517950170985114` completed in 132.84 seconds: two video sheets passed without correction, all 80 comments completed (72 model-reviewed and eight empty comments processed locally), and fusion passed on its first call with thinking enabled. Both video sheets returned no risk items, so this live case did not exercise a positive returned rule code; positive decoding is covered by tests. ASR/OCR were regenerated, so this is not an identical-request causal experiment. The original failed response was not retained and its exact invalid ID remains unknown.

Detailed artifacts are in the main project's `artifacts/diagnostics/video-rule-codes-7683517950170985114-20260910/`. The replay started no crawler, wrote no production task data, and used no task-level retry. The production collection remains stopped.
