#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="$ROOT/scripts/asr-guard.sh"
LOG_DIR="$ROOT/outputs/service-logs"
mkdir -p "$LOG_DIR"

SERVER="${SERVER:-root@47.117.134.103}"
SSH_PORT="${SSH_PORT:-6099}"
ASR_TUNNEL_TRANSPORT="${ASR_TUNNEL_TRANSPORT:-ssh}"
CODER_BIN="${CODER_BIN:-$(command -v coder || true)}"
CODER_WORKSPACE="${CODER_WORKSPACE:-}"
CODER_GLOBAL_CONFIG="${CODER_GLOBAL_CONFIG:-}"
LOCAL_ASR_TUNNEL_PORT="${LOCAL_ASR_TUNNEL_PORT:-19001}"
LOCAL_MMS_ASR_TUNNEL_PORT="${LOCAL_MMS_ASR_TUNNEL_PORT:-19004}"
REMOTE_ASR_PORT="${REMOTE_ASR_PORT:-9001}"
REMOTE_MMS_ASR_PORT="${REMOTE_MMS_ASR_PORT:-9004}"
RECONNECT_SECONDS="${RECONNECT_SECONDS:-5}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-5}"
START_REMOTE_ASR_SERVICES="${START_REMOTE_ASR_SERVICES:-false}"
KILL_PORT_LISTENER_ON_STOP="${KILL_PORT_LISTENER_ON_STOP:-true}"

ASR_SESSION="${ASR_SESSION:-xhs-asr-tunnel}"
MMS_SESSION="${MMS_SESSION:-xhs-mms-asr-tunnel}"

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

bool_env() {
  local value
  value="$(printf '%s' "${1:-false}" | tr '[:upper:]' '[:lower:]')"
  [[ "$value" == "1" || "$value" == "true" || "$value" == "yes" || "$value" == "on" ]]
}

USE_REMOTE_MMS_ASR_EFFECTIVE="${USE_REMOTE_MMS_ASR:-$(read_env_value USE_REMOTE_MMS_ASR || true)}"
REMOTE_INFERENCE_KEY_EFFECTIVE="${REMOTE_INFERENCE_API_KEY:-$(read_env_value REMOTE_INFERENCE_API_KEY || true)}"

asr_base_url() {
  printf 'http://127.0.0.1:%s' "$LOCAL_ASR_TUNNEL_PORT"
}

mms_base_url() {
  printf 'http://127.0.0.1:%s' "$LOCAL_MMS_ASR_TUNNEL_PORT"
}

screen_exists() {
  local name="$1"
  local listing
  listing="$(screen -ls 2>/dev/null || true)"
  grep -q "[.]${name}[[:space:]]" <<<"$listing"
}

port_pids() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | xargs 2>/dev/null || true
  fi
}

curl_health() {
  local base_url="$1"
  if [[ -n "$REMOTE_INFERENCE_KEY_EFFECTIVE" ]]; then
    curl -fsS --max-time "$HEALTH_TIMEOUT" \
      -H "X-Inference-Key: $REMOTE_INFERENCE_KEY_EFFECTIVE" \
      "$base_url/api/inference/health" >/dev/null
  else
    curl -fsS --max-time "$HEALTH_TIMEOUT" \
      "$base_url/api/inference/health" >/dev/null
  fi
}

print_health() {
  local label="$1"
  local base_url="$2"
  if curl_health "$base_url"; then
    echo "$label health: ok ($base_url)"
  else
    echo "$label health: failed ($base_url)"
  fi
}

start_remote_services() {
  if ! bool_env "$START_REMOTE_ASR_SERVICES"; then
    return
  fi
  if [[ "$ASR_TUNNEL_TRANSPORT" == "coder" ]]; then
    echo "Coder mode expects the workspace to manage Dolphin; skip remote-stack.sh." >&2
    return
  fi
  if [[ ! -f "$ROOT/scripts/remote-stack.sh" ]]; then
    echo "remote-stack.sh is missing; skip remote service start." >&2
    return
  fi

  local enable_mms="false"
  if bool_env "$USE_REMOTE_MMS_ASR_EFFECTIVE"; then
    enable_mms="true"
  fi

  echo "Starting remote ASR service(s) on $SERVER ..."
  ENABLE_REMOTE_INFERENCE=false \
    ENABLE_DOLPHIN=true \
    ENABLE_MMS_ASR="$enable_mms" \
    ENABLE_HYMT=false \
    ENABLE_OCR=false \
    bash "$ROOT/scripts/remote-stack.sh" start
}

start_tunnel() {
  local session="$1"
  local label="$2"
  local local_port="$3"
  local remote_port="$4"

  if screen_exists "$session"; then
    echo "$label guard already running: $session"
    return
  fi

  local pids
  pids="$(port_pids "$local_port")"
  if [[ -n "$pids" ]]; then
    echo "$label local port $local_port is already listening: $pids"
    return
  fi

  if [[ "$ASR_TUNNEL_TRANSPORT" == "coder" ]]; then
    if [[ -z "$CODER_BIN" || ! -x "$CODER_BIN" ]]; then
      echo "coder CLI is required for ASR_TUNNEL_TRANSPORT=coder" >&2
      exit 1
    fi
    if [[ -z "$CODER_WORKSPACE" ]]; then
      echo "CODER_WORKSPACE is required for ASR_TUNNEL_TRANSPORT=coder" >&2
      exit 1
    fi
    echo "Starting $label tunnel guard: 127.0.0.1:$local_port -> Coder $CODER_WORKSPACE:127.0.0.1:$remote_port"
  else
    echo "Starting $label tunnel guard: 127.0.0.1:$local_port -> $SERVER:127.0.0.1:$remote_port"
  fi
  screen -dmS "$session" bash "$SCRIPT_PATH" _tunnel_loop "$label" "$local_port" "$remote_port"
}

stop_tunnel() {
  local session="$1"
  local label="$2"
  local local_port="$3"

  if screen_exists "$session"; then
    echo "Stopping $label guard: $session"
    screen -S "$session" -X quit || true
    sleep 0.5
  fi

  if bool_env "$KILL_PORT_LISTENER_ON_STOP"; then
    local pids
    pids="$(port_pids "$local_port")"
    if [[ -n "$pids" ]]; then
      echo "Stopping $label listener(s) on port $local_port: $pids"
      kill $pids 2>/dev/null || true
    fi
  fi
}

status_one() {
  local session="$1"
  local label="$2"
  local local_port="$3"
  local base_url="$4"
  local pids

  if screen_exists "$session"; then
    echo "$label guard: running ($session)"
  else
    echo "$label guard: stopped ($session)"
  fi

  pids="$(port_pids "$local_port")"
  if [[ -n "$pids" ]]; then
    echo "$label listener: port $local_port pid(s) $pids"
  else
    echo "$label listener: port $local_port not listening"
  fi

  print_health "$label" "$base_url"
}

tunnel_loop() {
  local label="$1"
  local local_port="$2"
  local remote_port="$3"
  local log_name
  log_name="$(printf '%s' "$label" | tr '[:upper:] ' '[:lower:]-')"
  exec >>"$LOG_DIR/$log_name.guard.log" 2>&1

  echo "[$(date '+%F %T')] $label guard started"
  while true; do
    if [[ "$ASR_TUNNEL_TRANSPORT" == "coder" ]]; then
      echo "[$(date '+%F %T')] opening Coder tunnel 127.0.0.1:$local_port -> $CODER_WORKSPACE:127.0.0.1:$remote_port"
      coder_args=("$CODER_BIN")
      if [[ -n "$CODER_GLOBAL_CONFIG" ]]; then
        coder_args+=(--global-config "$CODER_GLOBAL_CONFIG")
      fi
      if "${coder_args[@]}" port-forward "$CODER_WORKSPACE" --tcp "$local_port:$remote_port"; then
        code=0
      else
        code="$?"
      fi
    else
      echo "[$(date '+%F %T')] opening SSH tunnel 127.0.0.1:$local_port -> $SERVER:127.0.0.1:$remote_port"
      if ssh \
        -p "$SSH_PORT" \
        -o ConnectTimeout=15 \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=3 \
        -o ExitOnForwardFailure=yes \
        -N \
        -L "$local_port:127.0.0.1:$remote_port" \
        "$SERVER"; then
        code=0
      else
        code="$?"
      fi
    fi
    echo "[$(date '+%F %T')] tunnel exited with code $code; reconnecting in ${RECONNECT_SECONDS}s"
    sleep "$RECONNECT_SECONDS"
  done
}

case "$ACTION" in
  _tunnel_loop)
    tunnel_loop "${2:?label required}" "${3:?local port required}" "${4:?remote port required}"
    ;;
  start)
    start_remote_services
    start_tunnel "$ASR_SESSION" "Dolphin ASR" "$LOCAL_ASR_TUNNEL_PORT" "$REMOTE_ASR_PORT"
    if bool_env "$USE_REMOTE_MMS_ASR_EFFECTIVE"; then
      start_tunnel "$MMS_SESSION" "MMS ASR" "$LOCAL_MMS_ASR_TUNNEL_PORT" "$REMOTE_MMS_ASR_PORT"
    else
      echo "MMS ASR guard: disabled (USE_REMOTE_MMS_ASR is not true)"
    fi
    sleep 1
    "$SCRIPT_PATH" status
    ;;
  stop)
    stop_tunnel "$ASR_SESSION" "Dolphin ASR" "$LOCAL_ASR_TUNNEL_PORT"
    stop_tunnel "$MMS_SESSION" "MMS ASR" "$LOCAL_MMS_ASR_TUNNEL_PORT"
    ;;
  restart)
    bash "$SCRIPT_PATH" stop
    bash "$SCRIPT_PATH" start
    ;;
  status)
    echo "ASR tunnel transport: $ASR_TUNNEL_TRANSPORT"
    status_one "$ASR_SESSION" "Dolphin ASR" "$LOCAL_ASR_TUNNEL_PORT" "$(asr_base_url)"
    if bool_env "$USE_REMOTE_MMS_ASR_EFFECTIVE"; then
      status_one "$MMS_SESSION" "MMS ASR" "$LOCAL_MMS_ASR_TUNNEL_PORT" "$(mms_base_url)"
    else
      echo "MMS ASR: disabled (USE_REMOTE_MMS_ASR is not true)"
    fi
    ;;
  check)
    curl_health "$(asr_base_url)"
    if bool_env "$USE_REMOTE_MMS_ASR_EFFECTIVE"; then
      curl_health "$(mms_base_url)"
    fi
    echo "ASR health ok"
    ;;
  *)
    echo "Usage: $0 [start|stop|restart|status|check]" >&2
    echo "Optional: START_REMOTE_ASR_SERVICES=true $0 start" >&2
    echo "Coder: ASR_TUNNEL_TRANSPORT=coder CODER_WORKSPACE=<workspace> REMOTE_ASR_PORT=19001 $0 start" >&2
    exit 2
    ;;
esac
