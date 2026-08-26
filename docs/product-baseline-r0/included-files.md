# Product Baseline Consolidation R0: Included Files

This is the positive integration list. Every other untracked source file from
the demo is excluded unless it appears in the exclusion manifest.

## Product shell and compatibility facade

- `Audit_assistant/package.json`, `Audit_assistant/pnpm-lock.yaml`
- The reviewed investigation UI files changed in the demo worktree, plus the
  five investigation/report service and type files added there.
- `backend/main.py`, `backend/audit_agent/config.py`, and `requirements.txt`
  from the reviewed demo worktree.
- `backend/api/` and `backend/domain/` from the reviewed demo worktree.
- `backend/investigation/` from the reviewed demo worktree. This is retained
  only as a legacy compatibility facade for the current API/SSE contract; it is
  not the future Agent runtime.

## Formal reporting and Account capability

- The complete `backend/reporting/` implementation from Hermes M2.2 worktree
  `0a5090579c4cfcda1208269814f32fbe77da4c86`, including R3.1, R2 and the
  immutable report store.
- `hermes_m0/` public domain, refs, ledger, repositories, services, schemas,
  runtime, plugin, Account corpus/activity and report-task modules explicitly
  listed in `docs/product-baseline-r0/HERMES_RUNTIME_MANIFEST.md`.
- `hermes_m0/fixtures/account_m22_corpus.json.gz` and
  `hermes_m0/fixtures/report_2272c3692807.json` only.
- `.hermes/plugins/xhs-investigation/`, the project plugin entry point for
  Hermes discovery.
- `backend/hermes_runtime/`, the lazy version-fenced product adapter.

## Deterministic verification

- `tests/test_investigation_agent.py`
- `tests/test_investigation_artifacts.py`
- `tests/test_report_graph.py`
- `tests/test_qwen_report_client.py`
- `tests/test_report_prompts.py`
- `tests/test_stage_3b_api.py`
- `tests/test_domain_evidence_adapter.py`
- `tests/test_domain_query_service.py`
- `tests/test_domain_repository.py`
- `tests/planner_fixture_support.py` and the four `tests/fixtures/planner_shadow_*.json`
  files are retained solely as deterministic Planner oracle fixtures; they are
  not Agent runtime code.

The tests above use temporary databases or mocked model calls. Real-data and
provider tests remain evidence-only and are not part of the clean-room gate.
