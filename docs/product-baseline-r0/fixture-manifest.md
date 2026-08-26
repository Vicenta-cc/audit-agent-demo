# R0 Fixture Manifest

Only deterministic,脱敏 fixtures are copied into the candidate. They are
package inputs, not live data and not credentials.

| Candidate path | SHA-256 | Source / purpose |
|---|---|---|
| `hermes_m0/fixtures/account_m22_corpus.json.gz` | `e17a5ac81447a6c800e8017ede9ca55542d07b36509fe815625440b923ab440` | Hermes M2.2 frozen Account corpus |
| `hermes_m0/fixtures/report_2272c3692807.json` | `b8769eaeaf23e3f95cfa71c9cc243902d1834fd711e51d9595f0ec6b2872c675` | Hermes M1 report fixture |
| `tests/fixtures/planner_shadow_gold.json` | `0ad3e7f11ca6e701eec417ea961acb3be2569fc3a6271db3068a1490b328ced6` | deterministic oracle fixture |
| `tests/fixtures/planner_shadow_v1_1_blind.json` | `05d0e5d3174885872538219375f4c20770df92492c92ec70d5ccc4c0112eca8d` | deterministic held-out fixture |
| `tests/fixtures/planner_shadow_v1_2_regression.json` | `424ec8a679b667581bf903ea50f5b5bc7df769684d678f9347df43d6bc708659` | deterministic regression fixture |
| `tests/fixtures/planner_shadow_v1_2_blind.json` | `8e346a329e4d2221bf4de0faaf6ee3eaa007d14fda8f9ffb6923cf64f959d1ce` | deterministic held-out fixture |
| `artifacts/m22_unified_account_risk_navigation_20260826/frozen/questions.json` | `4c3110f330d5edf6d8df4ad3aabeeb64e5b42cc387c2b4846c953ad19a87` | read-only M2.2 acceptance evidence; not a production runtime dependency |
| `artifacts/m22_unified_account_risk_navigation_20260826/frozen/oracle.json` | `9ddb428c1c5be2dc20527f865f690401b47486c5c41eb5593a5473949f177d05` | read-only M2.2 acceptance evidence; not a production runtime dependency |

The R3.1 report SQLite files and frontend JSON are registered as read-only
evidence in the Preservation Gate archive. They are not runtime fixtures and
are intentionally not copied into the candidate.

No `auth.json`, cookie, provider exchange, raw trace, external venv or live
database is a fixture. A fixture change requires a new hash, oracle review,
and a separate baseline decision.

The M2.2 questions/oracle paths above remain in their source evidence
worktree and are not copied into the product source tree. Their hashes are
registered for audit and recovery only; the frozen content and held-out
results are unchanged.
