#!/bin/bash
set -euo pipefail
FORMAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export XHS_MANAGED_RUNTIME="${XHS_MANAGED_RUNTIME:-${HOME}/Documents/Codex/runtime/xhs-audit-agent-formal-20260911}"
FORMAL_CONFIG="$XHS_MANAGED_RUNTIME/config.json"
if [[ ! -f "$FORMAL_CONFIG" ]]; then
  echo '缺少正式环境配置；参见 docs/runtime/FORMAL-BASELINE-20260911.md。' >&2
  exit 1
fi
FORMAL_PYTHON="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["python"])' "$FORMAL_CONFIG")"
exec "$FORMAL_PYTHON" "$FORMAL_ROOT/scripts/m3_environment.py" "${@:-status}"
