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

The R3.1 report SQLite files and frontend JSON are registered as read-only
evidence in the Preservation Gate archive. They are not runtime fixtures and
are intentionally not copied into the candidate.

No `auth.json`, cookie, provider exchange, raw trace, external venv or live
database is a fixture. A fixture change requires a new hash, oracle review,
and a separate baseline decision.
