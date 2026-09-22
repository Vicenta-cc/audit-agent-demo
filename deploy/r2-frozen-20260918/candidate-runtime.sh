#!/usr/bin/env bash
set -euo pipefail

RUNTIME_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${DOUYIN_CANDIDATE_CONFIG:-$RUNTIME_ROOT/runtime.json}"

exec /usr/bin/python3 "$RUNTIME_ROOT/candidate_runtime.py" \
  --config "$CONFIG" "${1:-status}"
