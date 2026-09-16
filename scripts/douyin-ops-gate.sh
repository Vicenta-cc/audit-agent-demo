#!/bin/sh
# Local development gate only. Never starts or stops a runtime.
set -eu
OPS_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OPS_NODE=/Users/ext.wanghongtao6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
OPS_PYTHON=/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-investigation-report-integration/.venv/bin/python
cd "$OPS_ROOT"
"$OPS_PYTHON" -m pytest -q \
  tests/test_task_settings.py tests/test_pipeline_verify_rotation.py tests/test_execution_parity.py \
  tests/test_investigation_creation_m3.py tests/test_authoritative_m3_provider_closure.py \
  tests/test_comment_alias_and_failure_isolation.py \
  tests/test_analyze_limit_ingestion.py tests/test_job_phase_state.py \
  tests/test_crawl_resume_control.py tests/test_job_request_validation.py \
  tests/test_crawler_adapter_execution_settings.py \
  tests/test_investigation_draft_configuration_m3.py \
  tests/test_investigation_creation_conversation.py
cd "$OPS_ROOT/Audit_assistant"
export VITE_API_PROXY_TARGET=http://127.0.0.1:8199
"$OPS_NODE" node_modules/typescript/bin/tsc --noEmit
"$OPS_NODE" node_modules/vite/bin/vite.js build
