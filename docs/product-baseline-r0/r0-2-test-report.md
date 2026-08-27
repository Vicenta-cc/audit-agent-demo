# R0.2 Test Report

| Gate | Result |
|---|---|
| Full backend suite | `304 passed, 4 skipped, 13 subtests passed` |
| Focused formal runtime/API set after final session-binding fix | `29 passed` |
| Hermes 0.20.4 discovery | plugin enabled; 11 tools; one middleware |
| AIAgent constructor | canonical prompt hash; no Provider call |
| Published R3.1 read-only smoke | schema v1; 14 sections; 1345 Account entries; restart equal |
| API/startup smoke | formal Hermes service; five required routes; config 200; missing structured report 404 |
| `frontend-v2` frozen install/build | PASS; zero downloads |
| `Audit_assistant` frozen install/build | PASS; zero downloads; existing chunk advisory only |
| Canonical source equality | all candidate-integrated `hermes_m0` Python files exact against `0a50905` |
| Diff whitespace | `git diff --check` PASS |

The four skipped tests are existing environment/fixture guards and are not R0.2
contract failures. Tests used temporary databases, deterministic fixtures,
fake Agents, or read-only published evidence. No API key, Provider, crawler,
login, production database, or live platform request was used.
