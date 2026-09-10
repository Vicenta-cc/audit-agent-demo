# R0 Deterministic Test Report

Clean-room run on 2026-08-26 using the candidate-local `.venv-r0-1` and no
provider credentials:

```text
Ran 220 tests
OK (skipped=1)
pytest reporting boundary: 4 passed
```

The one skip is a real-data characterization test guarded by the absence of
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
tests/test_r31_contracts.py
tests/test_crawler_adapter_execution_settings.py
```

The migration decisions for Hermes M2.2 source tests are recorded in
`test-migration-matrix.md`. The selected tests are candidate-local semantic
ports; historical implementation-specific tests, provider traces, one-shot
runners and external artifact paths remain excluded.

Provider calls, live SQLite databases, real MediaCrawler processes, and
published artifact mutation are outside this deterministic gate.
