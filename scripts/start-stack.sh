#!/usr/bin/env bash
set -euo pipefail

SERVER="${SERVER:-root@47.117.134.103}"
SSH_PORT="${SSH_PORT:-6099}"

LOCAL_INFERENCE_TUNNEL_PORT="${LOCAL_INFERENCE_TUNNEL_PORT:-${LOCAL_TUNNEL_PORT:-19000}}"
LOCAL_ASR_TUNNEL_PORT="${LOCAL_ASR_TUNNEL_PORT:-19001}"
LOCAL_MMS_ASR_TUNNEL_PORT="${LOCAL_MMS_ASR_TUNNEL_PORT:-19004}"
LOCAL_TRANSLATION_TUNNEL_PORT="${LOCAL_TRANSLATION_TUNNEL_PORT:-19002}"
LOCAL_OCR_TUNNEL_PORT="${LOCAL_OCR_TUNNEL_PORT:-19003}"
REMOTE_INFERENCE_PORT="${REMOTE_INFERENCE_PORT:-9000}"
REMOTE_ASR_PORT="${REMOTE_ASR_PORT:-9001}"
REMOTE_MMS_ASR_PORT="${REMOTE_MMS_ASR_PORT:-9004}"
REMOTE_TRANSLATION_PORT="${REMOTE_TRANSLATION_PORT:-9002}"
REMOTE_OCR_PORT="${REMOTE_OCR_PORT:-9003}"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

ENABLE_REMOTE_INFERENCE="${ENABLE_REMOTE_INFERENCE:-true}"
ENABLE_DOLPHIN="${ENABLE_DOLPHIN:-true}"
ENABLE_MMS_ASR="${ENABLE_MMS_ASR:-false}"
ENABLE_HYMT="${ENABLE_HYMT:-false}"
ENABLE_ASR_TRANSLATION="${ENABLE_ASR_TRANSLATION:-true}"
ENABLE_OCR="${ENABLE_OCR:-true}"
ENABLE_REMOTE_OCR="${ENABLE_REMOTE_OCR:-false}"

ASR_ENGINE="${ASR_ENGINE:-}"
if [[ -z "$ASR_ENGINE" ]]; then
  if [[ "$ENABLE_DOLPHIN" == "true" ]]; then
    ASR_ENGINE="dolphin"
  else
    ASR_ENGINE="whisper"
  fi
fi
ASR_LANGUAGE="${ASR_LANGUAGE:-ug}"
DOLPHIN_MODEL="${DOLPHIN_MODEL:-small}"
DOLPHIN_MODEL_DIR="${DOLPHIN_MODEL_DIR:-/mnt/workspace/models/dolphin}"
DOLPHIN_LANG_SYM="${DOLPHIN_LANG_SYM:-auto}"
DOLPHIN_REGION_SYM="${DOLPHIN_REGION_SYM:-CN}"
DOLPHIN_AUDIO_LOADER="${DOLPHIN_AUDIO_LOADER:-soundfile}"
DOLPHIN_WORD_TIMESTAMP="${DOLPHIN_WORD_TIMESTAMP:-true}"
DOLPHIN_PREDICT_TIME="${DOLPHIN_PREDICT_TIME:-$DOLPHIN_WORD_TIMESTAMP}"
MMS_MODEL="${MMS_MODEL:-facebook/mms-1b-all}"
MMS_TARGET_LANG="${MMS_TARGET_LANG:-uig-script_arabic}"
MMS_DEVICE="${MMS_DEVICE:-cuda}"
MMS_DTYPE="${MMS_DTYPE:-float16}"
MMS_HF_ENDPOINT="${MMS_HF_ENDPOINT:-https://hf-mirror.com}"
HYMT_MODEL="${HYMT_MODEL:-/mnt/workspace/models/HY-MT1.5-1.8B}"
OCR_ENGINE="${OCR_ENGINE:-vlm_ocr_translate}"
OCR_CONCURRENCY="${OCR_CONCURRENCY:-5}"
VIDEO_MOMENT_CONCURRENCY="${VIDEO_MOMENT_CONCURRENCY:-1}"
OCR_LANGUAGE_HINT="${OCR_LANGUAGE_HINT:-ug}"
OCR_SAMPLE_FPS="${OCR_SAMPLE_FPS:-1.0}"
ASR_TRANSLATE_ENGINE="${ASR_TRANSLATE_ENGINE:-qwen_text}"
ASR_TRANSLATE_MODEL="${ASR_TRANSLATE_MODEL:-qwen3.7-max}"
OCR_PADDLE_DEVICE="${OCR_PADDLE_DEVICE:-gpu:0}"
OCR_PADDLE_PIPELINE_VERSION="${OCR_PADDLE_PIPELINE_VERSION:-v1.6}"
OCR_PADDLE_PROMPT_LABEL="${OCR_PADDLE_PROMPT_LABEL:-ocr}"
OCR_PADDLE_PROMPT_PRESET="${OCR_PADDLE_PROMPT_PRESET:-uyghur-strong-en}"
OCR_PADDLE_PROMPT_COMPOSE="${OCR_PADDLE_PROMPT_COMPOSE:-replace}"
OCR_PADDLE_MAX_NEW_TOKENS="${OCR_PADDLE_MAX_NEW_TOKENS:-1024}"
OCR_PADDLE_TEMPERATURE="${OCR_PADDLE_TEMPERATURE:-0}"
OCR_PADDLE_REPETITION_PENALTY="${OCR_PADDLE_REPETITION_PENALTY:-1.05}"
PADDLEOCR_API_JOB_URL="${PADDLEOCR_API_JOB_URL:-https://paddleocr.aistudio-app.com/api/v2/ocr/jobs}"
PADDLEOCR_API_TOKEN="${PADDLEOCR_API_TOKEN:-}"
PADDLEOCR_API_MODEL="${PADDLEOCR_API_MODEL:-PaddleOCR-VL-1.6}"
PADDLEOCR_API_POLL_SECONDS="${PADDLEOCR_API_POLL_SECONDS:-5}"
PADDLEOCR_API_TIMEOUT_SECONDS="${PADDLEOCR_API_TIMEOUT_SECONDS:-300}"
PADDLEOCR_API_REQUEST_RETRIES="${PADDLEOCR_API_REQUEST_RETRIES:-3}"
PADDLEOCR_API_RETRY_BACKOFF_SECONDS="${PADDLEOCR_API_RETRY_BACKOFF_SECONDS:-1}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/outputs/service-logs"
export PATH="$HOME/.local/bin:$ROOT/tools/node/bin:$PATH"
mkdir -p "$LOG_DIR"
SSH_OPTS=(-p "$SSH_PORT" -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes)

start_logged_process() {
  local name="$1"
  shift
  local workdir="$1"
  shift

  local stdout="$LOG_DIR/$name.out.log"
  local stderr="$LOG_DIR/$name.err.log"
  echo "Starting $name ..."
  (
    cd "$workdir"
    nohup "$@" >"$stdout" 2>"$stderr" &
    echo $! >"$LOG_DIR/$name.pid"
  )
  local pid
  pid="$(cat "$LOG_DIR/$name.pid")"
  sleep 0.3
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "Failed to start $name. Recent stderr:" >&2
    tail -40 "$stderr" >&2 || true
    exit 1
  fi
  echo "  pid=$pid, logs=$stdout"
}

ensure_venv() {
  if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    echo "Creating macOS virtual environment ..."
    rm -rf "$ROOT/.venv"
    python3 -m venv "$ROOT/.venv"
  fi

  echo "Installing/updating local Python dependencies ..."
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$ROOT/.venv/bin/python" -r "$ROOT/requirements.txt"
  else
    "$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"
  fi
}

ensure_venv
sleep 1

if [[ "$ENABLE_REMOTE_INFERENCE" == "true" ]]; then
  start_logged_process \
    "ssh-inference-tunnel" \
    "$ROOT" \
    ssh "${SSH_OPTS[@]}" -N -L "$LOCAL_INFERENCE_TUNNEL_PORT:127.0.0.1:$REMOTE_INFERENCE_PORT" "$SERVER"
fi

if [[ "$ENABLE_DOLPHIN" == "true" ]]; then
  start_logged_process \
    "ssh-asr-tunnel" \
    "$ROOT" \
    ssh "${SSH_OPTS[@]}" -N -L "$LOCAL_ASR_TUNNEL_PORT:127.0.0.1:$REMOTE_ASR_PORT" "$SERVER"
fi

if [[ "$ENABLE_MMS_ASR" == "true" ]]; then
  start_logged_process \
    "ssh-mms-asr-tunnel" \
    "$ROOT" \
    ssh "${SSH_OPTS[@]}" -N -L "$LOCAL_MMS_ASR_TUNNEL_PORT:127.0.0.1:$REMOTE_MMS_ASR_PORT" "$SERVER"
fi

if [[ "$ENABLE_HYMT" == "true" ]]; then
  start_logged_process \
    "ssh-translation-tunnel" \
    "$ROOT" \
    ssh "${SSH_OPTS[@]}" -N -L "$LOCAL_TRANSLATION_TUNNEL_PORT:127.0.0.1:$REMOTE_TRANSLATION_PORT" "$SERVER"
fi

if [[ "$ENABLE_REMOTE_OCR" == "true" ]]; then
  start_logged_process \
    "ssh-ocr-tunnel" \
    "$ROOT" \
    ssh "${SSH_OPTS[@]}" -N -L "$LOCAL_OCR_TUNNEL_PORT:127.0.0.1:$REMOTE_OCR_PORT" "$SERVER"
fi

sleep 2

export USE_REMOTE_VLM="$ENABLE_REMOTE_INFERENCE"
export USE_REMOTE_LLM="$ENABLE_REMOTE_INFERENCE"
export USE_REMOTE_ASR="$ENABLE_DOLPHIN"
export USE_REMOTE_WHISPER="$ENABLE_DOLPHIN"
export USE_REMOTE_MMS_ASR="$ENABLE_MMS_ASR"
export USE_REMOTE_TRANSLATION="$ENABLE_HYMT"
export USE_REMOTE_OCR="$ENABLE_REMOTE_OCR"
export OCR_ENABLED="$ENABLE_OCR"
export TRANSLATION_ENABLED="$ENABLE_ASR_TRANSLATION"
export OCR_TRANSLATE="true"
export REMOTE_INFERENCE_BASE_URL="http://127.0.0.1:$LOCAL_INFERENCE_TUNNEL_PORT"
export REMOTE_ASR_BASE_URL="http://127.0.0.1:$LOCAL_ASR_TUNNEL_PORT"
export REMOTE_MMS_ASR_BASE_URL="http://127.0.0.1:$LOCAL_MMS_ASR_TUNNEL_PORT"
export REMOTE_TRANSLATION_BASE_URL="http://127.0.0.1:$LOCAL_TRANSLATION_TUNNEL_PORT"
export REMOTE_OCR_BASE_URL="http://127.0.0.1:$LOCAL_OCR_TUNNEL_PORT"
export ASR_ENGINE
export ASR_LANGUAGE
export DOLPHIN_MODEL
export DOLPHIN_MODEL_DIR
export DOLPHIN_LANG_SYM
export DOLPHIN_REGION_SYM
export DOLPHIN_AUDIO_LOADER
export DOLPHIN_WORD_TIMESTAMP
export DOLPHIN_PREDICT_TIME
export MMS_MODEL
export MMS_TARGET_LANG
export MMS_DEVICE
export MMS_DTYPE
export MMS_HF_ENDPOINT
export HYMT_MODEL
export OCR_ENGINE
export OCR_CONCURRENCY
export VIDEO_MOMENT_CONCURRENCY
export OCR_LANGUAGE_HINT
export OCR_SAMPLE_FPS
export ASR_TRANSLATE_ENGINE
export ASR_TRANSLATE_MODEL
export OCR_PADDLE_DEVICE
export OCR_PADDLE_PIPELINE_VERSION
export OCR_PADDLE_PROMPT_LABEL
export OCR_PADDLE_PROMPT_PRESET
export OCR_PADDLE_PROMPT_COMPOSE
export OCR_PADDLE_MAX_NEW_TOKENS
export OCR_PADDLE_TEMPERATURE
export OCR_PADDLE_REPETITION_PENALTY
export PADDLEOCR_API_JOB_URL
export PADDLEOCR_API_MODEL
export PADDLEOCR_API_POLL_SECONDS
export PADDLEOCR_API_TIMEOUT_SECONDS
export PADDLEOCR_API_REQUEST_RETRIES
export PADDLEOCR_API_RETRY_BACKOFF_SECONDS
if [[ -n "$PADDLEOCR_API_TOKEN" ]]; then
  export PADDLEOCR_API_TOKEN
fi

start_logged_process \
  "backend" \
  "$ROOT" \
  "$ROOT/.venv/bin/python" -m uvicorn backend.main:app --host 127.0.0.1 --port "$BACKEND_PORT"

start_logged_process \
  "frontend" \
  "$ROOT/frontend" \
  python3 -m http.server "$FRONTEND_PORT"

echo
echo "Started local stack. Open: http://127.0.0.1:$FRONTEND_PORT/"
echo "Local API: http://127.0.0.1:$BACKEND_PORT"
if [[ "$ENABLE_REMOTE_INFERENCE" == "true" ]]; then
  echo "Remote VLM/LLM tunnel: http://127.0.0.1:$LOCAL_INFERENCE_TUNNEL_PORT"
else
  echo "Qwen VLM/LLM: direct DashScope API"
fi
echo "Remote ASR tunnel: http://127.0.0.1:$LOCAL_ASR_TUNNEL_PORT"
if [[ "$ENABLE_MMS_ASR" == "true" ]]; then
  echo "Remote MMS ASR tunnel: http://127.0.0.1:$LOCAL_MMS_ASR_TUNNEL_PORT"
else
  echo "MMS ASR: disabled"
fi
if [[ "$ENABLE_HYMT" == "true" ]]; then
  echo "Remote HY-MT tunnel: http://127.0.0.1:$LOCAL_TRANSLATION_TUNNEL_PORT"
else
  echo "HY-MT translation: disabled"
fi
if [[ "$ENABLE_REMOTE_OCR" == "true" ]]; then
  echo "Remote OCR tunnel: http://127.0.0.1:$LOCAL_OCR_TUNNEL_PORT"
elif [[ "$ENABLE_OCR" != "true" ]]; then
  echo "OCR: disabled"
elif [[ "$OCR_ENGINE" == "vlm_ocr_translate" && "$ENABLE_REMOTE_OCR" != "true" ]]; then
  echo "OCR: VLM OCR+translation via Qwen ($OCR_CONCURRENCY concurrent)"
else
  echo "OCR: $OCR_ENGINE"
fi
echo "Moment coarse review concurrency: $VIDEO_MOMENT_CONCURRENCY"
echo "Logs: $LOG_DIR"
echo
echo "Remote services are controlled separately with: ./scripts/remote-stack.sh start|stop|status"
