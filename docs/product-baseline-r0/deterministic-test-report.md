# R0 Deterministic Test Report

Clean-room run on 2026-08-26 using the candidate-local `.venv-r0` and no
provider credentials:

```text
Ran 216 tests in 36.0s
OK (skipped=4)
```

The four skips are real-data characterization tests guarded by the absence of
the live audit database. No live database was opened or mutated.

The required suite is listed here so the result is explicit and reproducible:

```text
tests/test_investigation_agent.py
tests/test_investigation_artifacts.py
tests/test_report_graph.py
tests/test_qwen_report_client.py
tests/test_report_prompts.py
tests/test_stage_3b_api.py
tests/test_domain_evidence_adapter.py
tests/test_domain_query_service.py
tests/test_domain_repository.py
tests/test_hermes_m1_m22_offline.py
```

Provider calls, live SQLite databases, real MediaCrawler processes, and
published artifact mutation are outside this deterministic gate.
