#!/bin/bash
set -euo pipefail
M3_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
M3_CONFIG="$M3_ROOT/outputs/m3-environment/config.json"
if [[ ! -f "$M3_CONFIG" ]]; then
  echo '缺少实验环境配置；参见 docs/M3_EXPERIMENT_ENVIRONMENT.md。' >&2
  exit 1
fi
M3_PYTHON="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["python"])' "$M3_CONFIG")"
exec "$M3_PYTHON" "$M3_ROOT/scripts/m3_environment.py" "${@:-status}"
