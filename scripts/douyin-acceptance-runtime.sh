#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="${DOUYIN_ACCEPTANCE_RUNTIME:-/Users/ext.wanghongtao6/Documents/Codex/acceptance/collection-reliability-live-20260915}"
CONFIG="${DOUYIN_ACCEPTANCE_CONFIG:-$RUNTIME/runtime.json}"

if [[ ! -f "$CONFIG" ]]; then
  echo "Missing Douyin acceptance runtime config: $CONFIG" >&2
  exit 1
fi

exec /usr/bin/python3 "$ROOT/scripts/douyin_acceptance_runtime.py" \
  --config "$CONFIG" "${@:-status}"
