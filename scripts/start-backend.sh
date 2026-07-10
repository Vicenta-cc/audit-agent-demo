#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

HOST="${BACKEND_HOST:-127.0.0.1}"
PORT="${BACKEND_PORT:-8000}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_DEPS="${INSTALL_DEPS:-auto}"
RELOAD="${RELOAD:-false}"
VENV_PYTHON="$ROOT/.venv/bin/python"
REQUIREMENTS_MARKER="$ROOT/.venv/.requirements-installed"

mkdir -p "$ROOT/outputs/service-logs"

read_env_value() {
  local key="$1"
  if [[ -n "${!key:-}" ]]; then
    printf '%s\n' "${!key}"
    return
  fi
  if [[ ! -f "$ROOT/.env" ]]; then
    return
  fi
  awk -F= -v key="$key" '
    $1 == key {
      sub(/^[^=]*=/, "")
      gsub(/^"|"$/, "")
      gsub(/^'\''|'\''$/, "")
      print
      exit
    }
  ' "$ROOT/.env"
}

if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "Created .env from .env.example. Please edit .env before running real tasks."
fi

need_install=false
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Creating virtual environment: $ROOT/.venv"
  "$PYTHON_BIN" -m venv "$ROOT/.venv"
  need_install=true
fi

if [[ ! -f "$REQUIREMENTS_MARKER" || "$ROOT/requirements.txt" -nt "$REQUIREMENTS_MARKER" ]]; then
  need_install=true
fi

case "$INSTALL_DEPS" in
  true)
    need_install=true
    ;;
  false)
    need_install=false
    ;;
  auto)
    ;;
  *)
    echo "INSTALL_DEPS must be auto, true, or false." >&2
    exit 2
    ;;
esac

if [[ "$need_install" == "true" ]]; then
  echo "Installing Python dependencies from requirements.txt ..."
  "$VENV_PYTHON" -m pip install --upgrade pip
  "$VENV_PYTHON" -m pip install -r "$ROOT/requirements.txt"
  touch "$REQUIREMENTS_MARKER"
fi

if command -v lsof >/dev/null 2>&1; then
  pids="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | xargs 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "Port $PORT is already in use by pid(s): $pids" >&2
    echo "Stop the old backend or start with another port, for example: BACKEND_PORT=8010 ./scripts/start-backend.sh" >&2
    exit 1
  fi
fi

dashscope_key="$(read_env_value DASHSCOPE_API_KEY || true)"
if [[ -z "$dashscope_key" || "$dashscope_key" == "your_dashscope_api_key" ]]; then
  echo "WARNING: DASHSCOPE_API_KEY is empty or still a placeholder. Qwen analysis will fail until .env is filled."
fi

ffmpeg_path="$(read_env_value FFMPEG_PATH || true)"
if ! command -v ffmpeg >/dev/null 2>&1 && [[ -z "$ffmpeg_path" || ! -x "$ffmpeg_path" ]]; then
  echo "WARNING: ffmpeg is not in PATH. Video audio extraction will fail unless FFMPEG_PATH is set in .env."
fi

use_remote_asr="$(read_env_value USE_REMOTE_ASR || true)"
use_remote_asr="$(printf '%s' "$use_remote_asr" | tr '[:upper:]' '[:lower:]')"
remote_asr_url="$(read_env_value REMOTE_ASR_BASE_URL || true)"
remote_inference_url="$(read_env_value REMOTE_INFERENCE_BASE_URL || true)"
effective_asr_url="${remote_asr_url:-$remote_inference_url}"

if [[ "$use_remote_asr" == "true" ]]; then
  if [[ -z "$effective_asr_url" ]]; then
    echo "WARNING: USE_REMOTE_ASR=true but REMOTE_ASR_BASE_URL and REMOTE_INFERENCE_BASE_URL are empty."
  else
    echo "Remote ASR: $effective_asr_url"
  fi
fi

if [[ "${CHECK_REMOTE_ASR:-false}" == "true" && -n "$effective_asr_url" ]]; then
  "$VENV_PYTHON" - "$effective_asr_url" "$(read_env_value REMOTE_INFERENCE_API_KEY || true)" <<'PY'
import sys
import requests

base_url = sys.argv[1].rstrip("/")
api_key = sys.argv[2]
headers = {"X-Inference-Key": api_key} if api_key else {}
response = requests.get(f"{base_url}/api/inference/health", headers=headers, timeout=10)
response.raise_for_status()
print(f"Remote ASR health ok: {response.json()}")
PY
fi

args=("$VENV_PYTHON" -m uvicorn backend.main:app --host "$HOST" --port "$PORT")
if [[ "$RELOAD" == "true" ]]; then
  args+=(--reload)
fi

echo
echo "Starting backend: http://$HOST:$PORT"
echo "SaaS page:        http://$HOST:$PORT/saas"
echo "Stop with Ctrl+C."
echo
exec "${args[@]}"
