#!/usr/bin/env bash
set -euo pipefail
LOCAL_AUDIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_AUDIT_CONFIG="${LOCAL_AUDIT_CONFIG:-/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-local-30-20260923/runtime.json}"
LOCAL_AUDIT_PYTHON="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["python"])' "$LOCAL_AUDIT_CONFIG")"
exec "$LOCAL_AUDIT_PYTHON" "$LOCAL_AUDIT_ROOT/scripts/local_audit_runtime.py" --config "$LOCAL_AUDIT_CONFIG" "${@:-status}"
