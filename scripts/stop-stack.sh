#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/outputs/service-logs"

LOCAL_INFERENCE_TUNNEL_PORT="${LOCAL_INFERENCE_TUNNEL_PORT:-${LOCAL_TUNNEL_PORT:-19000}}"
LOCAL_ASR_TUNNEL_PORT="${LOCAL_ASR_TUNNEL_PORT:-19001}"
LOCAL_MMS_ASR_TUNNEL_PORT="${LOCAL_MMS_ASR_TUNNEL_PORT:-19004}"
LOCAL_TRANSLATION_TUNNEL_PORT="${LOCAL_TRANSLATION_TUNNEL_PORT:-19002}"
LOCAL_OCR_TUNNEL_PORT="${LOCAL_OCR_TUNNEL_PORT:-19003}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

stop_pid_file() {
  local name="$1"
  local pid_path="$LOG_DIR/$name.pid"
  if [[ ! -f "$pid_path" ]]; then
    return
  fi

  local pid
  pid="$(cat "$pid_path")"
  if kill -0 "$pid" 2>/dev/null; then
    echo "Stopping $name pid=$pid ..."
    kill "$pid" 2>/dev/null || true
    sleep 0.5
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$pid_path"
}

stop_port_listener() {
  local name="$1"
  local port="$2"

  if ! command -v lsof >/dev/null 2>&1; then
    return
  fi

  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | xargs 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return
  fi

  echo "Stopping stale $name listener(s) on port $port: $pids"
  kill $pids 2>/dev/null || true
  sleep 0.5

  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | xargs 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    kill -9 $pids 2>/dev/null || true
  fi
}

for name in frontend backend ssh-inference-tunnel ssh-asr-tunnel ssh-mms-asr-tunnel ssh-translation-tunnel ssh-ocr-tunnel ssh-tunnel; do
  stop_pid_file "$name"
done

stop_port_listener "backend" "$BACKEND_PORT"
stop_port_listener "frontend" "$FRONTEND_PORT"
stop_port_listener "ssh-inference-tunnel" "$LOCAL_INFERENCE_TUNNEL_PORT"
stop_port_listener "ssh-asr-tunnel" "$LOCAL_ASR_TUNNEL_PORT"
stop_port_listener "ssh-mms-asr-tunnel" "$LOCAL_MMS_ASR_TUNNEL_PORT"
stop_port_listener "ssh-translation-tunnel" "$LOCAL_TRANSLATION_TUNNEL_PORT"
stop_port_listener "ssh-ocr-tunnel" "$LOCAL_OCR_TUNNEL_PORT"

echo "Stopped local stack. Remote GPU services are unchanged."
echo "Use ./scripts/remote-stack.sh stop to stop remote services and release GPU memory."
