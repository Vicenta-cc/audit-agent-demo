#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"

SERVER="${SERVER:-root@47.117.134.103}"
SSH_PORT="${SSH_PORT:-6099}"

REMOTE_APP_DIR="${REMOTE_APP_DIR:-/root/xhs-audit-agent-demo}"
REMOTE_WORK_DIR="${REMOTE_WORK_DIR:-/mnt/workspace/paddleocr-vl-test}"
REMOTE_LOG_DIR="${REMOTE_LOG_DIR:-$REMOTE_APP_DIR/outputs/service-logs}"

REMOTE_VLLM_PORT="${REMOTE_VLLM_PORT:-8001}"
REMOTE_INFERENCE_PORT="${REMOTE_INFERENCE_PORT:-9000}"
REMOTE_ASR_PORT="${REMOTE_ASR_PORT:-9001}"
REMOTE_MMS_ASR_PORT="${REMOTE_MMS_ASR_PORT:-9004}"
REMOTE_TRANSLATION_PORT="${REMOTE_TRANSLATION_PORT:-9002}"
REMOTE_OCR_PORT="${REMOTE_OCR_PORT:-9003}"

REMOTE_VLLM_VENV="${REMOTE_VLLM_VENV:-/root/vllm-019-env}"
REMOTE_VLLM_MODEL="${REMOTE_VLLM_MODEL:-/root/models/Qwen3-VL-8B-Instruct}"
REMOTE_VLLM_SERVED_MODEL="${REMOTE_VLLM_SERVED_MODEL:-qwen3-vl-8b}"
REMOTE_VLLM_GPU_MEMORY_UTILIZATION="${REMOTE_VLLM_GPU_MEMORY_UTILIZATION:-0.65}"
REMOTE_VLLM_MAX_MODEL_LEN="${REMOTE_VLLM_MAX_MODEL_LEN:-32768}"
REMOTE_VLLM_EXTRA_ARGS="${REMOTE_VLLM_EXTRA_ARGS:-}"
REMOTE_INFERENCE_VENV="${REMOTE_INFERENCE_VENV:-$REMOTE_APP_DIR/.venv}"
DOLPHIN_VENV="${DOLPHIN_VENV:-$REMOTE_WORK_DIR/.venv-dolphin}"
HYMT_VENV="${HYMT_VENV:-$REMOTE_WORK_DIR/.venv-hymt}"
MMS_VENV="${MMS_VENV:-$HYMT_VENV}"
PADDLEOCR_VL_VENV="${PADDLEOCR_VL_VENV:-$REMOTE_WORK_DIR/.venv-paddleocr-vl}"

ENABLE_REMOTE_INFERENCE="${ENABLE_REMOTE_INFERENCE:-true}"
ENABLE_DOLPHIN="${ENABLE_DOLPHIN:-true}"
ENABLE_MMS_ASR="${ENABLE_MMS_ASR:-false}"
ENABLE_HYMT="${ENABLE_HYMT:-false}"
ENABLE_OCR="${ENABLE_OCR:-false}"

ASR_ENGINE="${ASR_ENGINE:-}"
if [[ -z "$ASR_ENGINE" ]]; then
  if [[ "$ENABLE_DOLPHIN" == "true" ]]; then
    ASR_ENGINE="dolphin"
  else
    ASR_ENGINE="whisper"
  fi
fi
ASR_LANGUAGE="${ASR_LANGUAGE:-ug}"
ASR_DEVICE="${ASR_DEVICE:-cuda}"
ASR_COMPUTE_TYPE="${ASR_COMPUTE_TYPE:-float16}"
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
MMS_CHUNK_LENGTH_S="${MMS_CHUNK_LENGTH_S:-20}"
MMS_STRIDE_LENGTH_S="${MMS_STRIDE_LENGTH_S:-2}"
MMS_BATCH_SIZE="${MMS_BATCH_SIZE:-4}"
MMS_RETURN_TIMESTAMPS="${MMS_RETURN_TIMESTAMPS:-false}"
MMS_HF_ENDPOINT="${MMS_HF_ENDPOINT:-https://hf-mirror.com}"
MMS_LOCAL_FILES_ONLY="${MMS_LOCAL_FILES_ONLY:-true}"

HYMT_MODEL="${HYMT_MODEL:-/mnt/workspace/models/HY-MT1.5-1.8B}"
HYMT_DTYPE="${HYMT_DTYPE:-bfloat16}"
HYMT_DEVICE_MAP="${HYMT_DEVICE_MAP:-auto}"
HYMT_TARGET_LANGUAGE="${HYMT_TARGET_LANGUAGE:-中文}"
HYMT_MAX_NEW_TOKENS="${HYMT_MAX_NEW_TOKENS:-256}"
HYMT_TEMPERATURE="${HYMT_TEMPERATURE:-0}"
HYMT_TOP_P="${HYMT_TOP_P:-0.6}"
HYMT_TOP_K="${HYMT_TOP_K:-20}"
HYMT_REPETITION_PENALTY="${HYMT_REPETITION_PENALTY:-1.15}"
OCR_ENGINE="${OCR_ENGINE:-vlm_ocr_translate}"
OCR_LANGUAGE_HINT="${OCR_LANGUAGE_HINT:-ug}"
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

GPU_ID="${GPU_ID:-0}"
GPU_MEMORY_WARN_MIB="${GPU_MEMORY_WARN_MIB:-1024}"
GPU_RESET_ON_STOP="${GPU_RESET_ON_STOP:-auto}"
KILL_EXTRA_GPU_PROCESSES_ON_STOP="${KILL_EXTRA_GPU_PROCESSES_ON_STOP:-false}"

SSH_OPTS=(-p "$SSH_PORT" -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=3)

case "$ACTION" in
  start|stop|restart|status|install-deps)
    ;;
  *)
    echo "Usage: $0 [start|stop|restart|status|install-deps]" >&2
    exit 2
    ;;
esac

ssh "${SSH_OPTS[@]}" "$SERVER" "bash -s" <<REMOTE
set -euo pipefail

ACTION="$ACTION"
REMOTE_APP_DIR="$REMOTE_APP_DIR"
REMOTE_LOG_DIR="$REMOTE_LOG_DIR"
REMOTE_VLLM_PORT="$REMOTE_VLLM_PORT"
REMOTE_INFERENCE_PORT="$REMOTE_INFERENCE_PORT"
REMOTE_ASR_PORT="$REMOTE_ASR_PORT"
REMOTE_MMS_ASR_PORT="$REMOTE_MMS_ASR_PORT"
REMOTE_TRANSLATION_PORT="$REMOTE_TRANSLATION_PORT"
REMOTE_OCR_PORT="$REMOTE_OCR_PORT"
REMOTE_VLLM_VENV="$REMOTE_VLLM_VENV"
REMOTE_VLLM_MODEL="$REMOTE_VLLM_MODEL"
REMOTE_VLLM_SERVED_MODEL="$REMOTE_VLLM_SERVED_MODEL"
REMOTE_VLLM_GPU_MEMORY_UTILIZATION="$REMOTE_VLLM_GPU_MEMORY_UTILIZATION"
REMOTE_VLLM_MAX_MODEL_LEN="$REMOTE_VLLM_MAX_MODEL_LEN"
REMOTE_VLLM_EXTRA_ARGS="$REMOTE_VLLM_EXTRA_ARGS"
REMOTE_INFERENCE_VENV="$REMOTE_INFERENCE_VENV"
DOLPHIN_VENV="$DOLPHIN_VENV"
HYMT_VENV="$HYMT_VENV"
MMS_VENV="$MMS_VENV"
PADDLEOCR_VL_VENV="$PADDLEOCR_VL_VENV"
ENABLE_REMOTE_INFERENCE="$ENABLE_REMOTE_INFERENCE"
ENABLE_DOLPHIN="$ENABLE_DOLPHIN"
ENABLE_MMS_ASR="$ENABLE_MMS_ASR"
ENABLE_HYMT="$ENABLE_HYMT"
ENABLE_OCR="$ENABLE_OCR"
ASR_ENGINE="$ASR_ENGINE"
ASR_LANGUAGE="$ASR_LANGUAGE"
ASR_DEVICE="$ASR_DEVICE"
ASR_COMPUTE_TYPE="$ASR_COMPUTE_TYPE"
DOLPHIN_MODEL="$DOLPHIN_MODEL"
DOLPHIN_MODEL_DIR="$DOLPHIN_MODEL_DIR"
DOLPHIN_LANG_SYM="$DOLPHIN_LANG_SYM"
DOLPHIN_REGION_SYM="$DOLPHIN_REGION_SYM"
DOLPHIN_AUDIO_LOADER="$DOLPHIN_AUDIO_LOADER"
DOLPHIN_WORD_TIMESTAMP="$DOLPHIN_WORD_TIMESTAMP"
DOLPHIN_PREDICT_TIME="$DOLPHIN_PREDICT_TIME"
MMS_MODEL="$MMS_MODEL"
MMS_TARGET_LANG="$MMS_TARGET_LANG"
MMS_DEVICE="$MMS_DEVICE"
MMS_DTYPE="$MMS_DTYPE"
MMS_CHUNK_LENGTH_S="$MMS_CHUNK_LENGTH_S"
MMS_STRIDE_LENGTH_S="$MMS_STRIDE_LENGTH_S"
MMS_BATCH_SIZE="$MMS_BATCH_SIZE"
MMS_RETURN_TIMESTAMPS="$MMS_RETURN_TIMESTAMPS"
MMS_HF_ENDPOINT="$MMS_HF_ENDPOINT"
MMS_LOCAL_FILES_ONLY="$MMS_LOCAL_FILES_ONLY"
HYMT_MODEL="$HYMT_MODEL"
HYMT_DTYPE="$HYMT_DTYPE"
HYMT_DEVICE_MAP="$HYMT_DEVICE_MAP"
HYMT_TARGET_LANGUAGE="$HYMT_TARGET_LANGUAGE"
HYMT_MAX_NEW_TOKENS="$HYMT_MAX_NEW_TOKENS"
HYMT_TEMPERATURE="$HYMT_TEMPERATURE"
HYMT_TOP_P="$HYMT_TOP_P"
HYMT_TOP_K="$HYMT_TOP_K"
HYMT_REPETITION_PENALTY="$HYMT_REPETITION_PENALTY"
OCR_ENGINE="$OCR_ENGINE"
OCR_LANGUAGE_HINT="$OCR_LANGUAGE_HINT"
OCR_PADDLE_DEVICE="$OCR_PADDLE_DEVICE"
OCR_PADDLE_PIPELINE_VERSION="$OCR_PADDLE_PIPELINE_VERSION"
OCR_PADDLE_PROMPT_LABEL="$OCR_PADDLE_PROMPT_LABEL"
OCR_PADDLE_PROMPT_PRESET="$OCR_PADDLE_PROMPT_PRESET"
OCR_PADDLE_PROMPT_COMPOSE="$OCR_PADDLE_PROMPT_COMPOSE"
OCR_PADDLE_MAX_NEW_TOKENS="$OCR_PADDLE_MAX_NEW_TOKENS"
OCR_PADDLE_TEMPERATURE="$OCR_PADDLE_TEMPERATURE"
OCR_PADDLE_REPETITION_PENALTY="$OCR_PADDLE_REPETITION_PENALTY"
PADDLEOCR_API_JOB_URL="$PADDLEOCR_API_JOB_URL"
PADDLEOCR_API_TOKEN="$PADDLEOCR_API_TOKEN"
PADDLEOCR_API_MODEL="$PADDLEOCR_API_MODEL"
PADDLEOCR_API_POLL_SECONDS="$PADDLEOCR_API_POLL_SECONDS"
PADDLEOCR_API_TIMEOUT_SECONDS="$PADDLEOCR_API_TIMEOUT_SECONDS"
GPU_ID="$GPU_ID"
GPU_MEMORY_WARN_MIB="$GPU_MEMORY_WARN_MIB"
GPU_RESET_ON_STOP="$GPU_RESET_ON_STOP"
KILL_EXTRA_GPU_PROCESSES_ON_STOP="$KILL_EXTRA_GPU_PROCESSES_ON_STOP"

mkdir -p "\$REMOTE_LOG_DIR"

install_service_deps() {
  local name="\$1"
  local venv="\$2"
  shift 2

  if [[ ! -x "\$venv/bin/python" ]]; then
    echo "\$name venv is not usable: \$venv/bin/python not found or not executable" >&2
    return 1
  fi

  echo "Installing service deps into \$name venv: \$venv"
  "\$venv/bin/python" -m pip install "\$@"
}

check_venv_modules() {
  local name="\$1"
  local venv="\$2"
  shift 2

  if [[ ! -x "\$venv/bin/python" ]]; then
    echo "\$name venv is not usable: \$venv/bin/python not found or not executable" >&2
    return 1
  fi

  "\$venv/bin/python" - "\$@" <<'PY'
import importlib
import sys

missing = []
for module_name in sys.argv[1:]:
    try:
        importlib.import_module(module_name)
    except Exception as exc:
        missing.append(f"{module_name}: {type(exc).__name__}: {exc}")

if missing:
    print("Missing or unusable modules:", file=sys.stderr)
    for item in missing:
        print(f"  - {item}", file=sys.stderr)
    sys.exit(1)
PY
}

start_vllm() {
  if [[ "\$ENABLE_REMOTE_INFERENCE" != "true" ]]; then
    echo "Skipping vLLM: ENABLE_REMOTE_INFERENCE=\$ENABLE_REMOTE_INFERENCE"
    return
  fi
  if pgrep -f "vllm.entrypoints.openai.api_server.*--port \$REMOTE_VLLM_PORT" >/dev/null; then
    echo "vLLM already running on port \$REMOTE_VLLM_PORT"
    return
  fi
  check_venv_modules "vLLM" "\$REMOTE_VLLM_VENV" vllm
  echo "Starting vLLM on port \$REMOTE_VLLM_PORT ..."
  local vllm_cmd
  vllm_cmd="source '\$REMOTE_VLLM_VENV/bin/activate' && exec python -m vllm.entrypoints.openai.api_server --host 127.0.0.1 --port '\$REMOTE_VLLM_PORT' --model '\$REMOTE_VLLM_MODEL' --served-model-name '\$REMOTE_VLLM_SERVED_MODEL' --trust-remote-code"
  if [[ -n "\$REMOTE_VLLM_GPU_MEMORY_UTILIZATION" ]]; then
    vllm_cmd="\$vllm_cmd --gpu-memory-utilization '\$REMOTE_VLLM_GPU_MEMORY_UTILIZATION'"
  fi
  if [[ -n "\$REMOTE_VLLM_MAX_MODEL_LEN" ]]; then
    vllm_cmd="\$vllm_cmd --max-model-len '\$REMOTE_VLLM_MAX_MODEL_LEN'"
  fi
  if [[ -n "\$REMOTE_VLLM_EXTRA_ARGS" ]]; then
    vllm_cmd="\$vllm_cmd \$REMOTE_VLLM_EXTRA_ARGS"
  fi
  nohup bash -lc "\$vllm_cmd" \
    > "\$REMOTE_LOG_DIR/vllm.out.log" \
    2> "\$REMOTE_LOG_DIR/vllm.err.log" &
}

start_inference() {
  if [[ "\$ENABLE_REMOTE_INFERENCE" != "true" ]]; then
    echo "Skipping inference server: ENABLE_REMOTE_INFERENCE=\$ENABLE_REMOTE_INFERENCE"
    return
  fi
  if pgrep -f "backend.inference_server:app.*--port \$REMOTE_INFERENCE_PORT" >/dev/null; then
    echo "Inference server already running on port \$REMOTE_INFERENCE_PORT"
    return
  fi
  check_venv_modules "VLM/LLM inference" "\$REMOTE_INFERENCE_VENV" fastapi uvicorn pydantic dotenv requests multipart
  echo "Starting VLM/LLM inference server on port \$REMOTE_INFERENCE_PORT ..."
  cd "\$REMOTE_APP_DIR"
  nohup bash -lc "source '\$REMOTE_INFERENCE_VENV/bin/activate' && export DASHSCOPE_API_KEY='EMPTY' DASHSCOPE_BASE_URL='http://127.0.0.1:\$REMOTE_VLLM_PORT/v1' QWEN_TEXT_MODEL='\$REMOTE_VLLM_SERVED_MODEL' QWEN_VL_MODEL='\$REMOTE_VLLM_SERVED_MODEL' TRANSLATION_ENABLED=false OCR_ENABLED=false && exec python -m uvicorn backend.inference_server:app --host 0.0.0.0 --port '\$REMOTE_INFERENCE_PORT'" \
    > "\$REMOTE_LOG_DIR/inference.out.log" \
    2> "\$REMOTE_LOG_DIR/inference.err.log" &
}

start_asr() {
  if [[ "\$ENABLE_DOLPHIN" != "true" ]]; then
    echo "Skipping Dolphin ASR: ENABLE_DOLPHIN=\$ENABLE_DOLPHIN"
    return
  fi
  if pgrep -f "backend.inference_server:app.*--port \$REMOTE_ASR_PORT" >/dev/null; then
    echo "Dolphin ASR server already running on port \$REMOTE_ASR_PORT"
    return
  fi
  check_venv_modules "Dolphin ASR" "\$DOLPHIN_VENV" fastapi uvicorn pydantic dotenv requests multipart dolphin soundfile torch torchaudio
  echo "Starting Dolphin ASR server on port \$REMOTE_ASR_PORT ..."
  cd "\$REMOTE_APP_DIR"
  nohup bash -lc "source '\$DOLPHIN_VENV/bin/activate' && export CUDA_VISIBLE_DEVICES='\$GPU_ID' PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True' ASR_ENGINE='\$ASR_ENGINE' ASR_LANGUAGE='\$ASR_LANGUAGE' ASR_DEVICE='\$ASR_DEVICE' ASR_COMPUTE_TYPE='\$ASR_COMPUTE_TYPE' DOLPHIN_MODEL='\$DOLPHIN_MODEL' DOLPHIN_MODEL_DIR='\$DOLPHIN_MODEL_DIR' DOLPHIN_LANG_SYM='\$DOLPHIN_LANG_SYM' DOLPHIN_REGION_SYM='\$DOLPHIN_REGION_SYM' DOLPHIN_AUDIO_LOADER='\$DOLPHIN_AUDIO_LOADER' DOLPHIN_WORD_TIMESTAMP='\$DOLPHIN_WORD_TIMESTAMP' DOLPHIN_PREDICT_TIME='\$DOLPHIN_PREDICT_TIME' TRANSLATION_ENABLED=false OCR_ENABLED=false && exec python -m uvicorn backend.inference_server:app --host 0.0.0.0 --port '\$REMOTE_ASR_PORT'" \
    > "\$REMOTE_LOG_DIR/dolphin-asr.out.log" \
    2> "\$REMOTE_LOG_DIR/dolphin-asr.err.log" &
}

start_mms_asr() {
  if [[ "\$ENABLE_MMS_ASR" != "true" ]]; then
    echo "Skipping MMS ASR: ENABLE_MMS_ASR=\$ENABLE_MMS_ASR"
    return
  fi
  if pgrep -f "backend.inference_server:app.*--port \$REMOTE_MMS_ASR_PORT" >/dev/null; then
    echo "MMS ASR server already running on port \$REMOTE_MMS_ASR_PORT"
    return
  fi
  check_venv_modules "MMS ASR" "\$MMS_VENV" fastapi uvicorn pydantic dotenv requests multipart transformers soundfile torch
  echo "Starting MMS ASR server on port \$REMOTE_MMS_ASR_PORT ..."
  cd "\$REMOTE_APP_DIR"
  nohup bash -lc "source '\$MMS_VENV/bin/activate' && export CUDA_VISIBLE_DEVICES='\$GPU_ID' PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True' MMS_MODEL='\$MMS_MODEL' MMS_TARGET_LANG='\$MMS_TARGET_LANG' MMS_DEVICE='\$MMS_DEVICE' MMS_DTYPE='\$MMS_DTYPE' MMS_CHUNK_LENGTH_S='\$MMS_CHUNK_LENGTH_S' MMS_STRIDE_LENGTH_S='\$MMS_STRIDE_LENGTH_S' MMS_BATCH_SIZE='\$MMS_BATCH_SIZE' MMS_RETURN_TIMESTAMPS='\$MMS_RETURN_TIMESTAMPS' MMS_HF_ENDPOINT='\$MMS_HF_ENDPOINT' MMS_LOCAL_FILES_ONLY='\$MMS_LOCAL_FILES_ONLY' HF_ENDPOINT='\$MMS_HF_ENDPOINT' TRANSLATION_ENABLED=false OCR_ENABLED=false && exec python -m uvicorn backend.inference_server:app --host 0.0.0.0 --port '\$REMOTE_MMS_ASR_PORT'" \
    > "\$REMOTE_LOG_DIR/mms-asr.out.log" \
    2> "\$REMOTE_LOG_DIR/mms-asr.err.log" &
}

start_translation() {
  if [[ "\$ENABLE_HYMT" != "true" ]]; then
    echo "Skipping HY-MT translation: ENABLE_HYMT=\$ENABLE_HYMT"
    return
  fi
  if pgrep -f "backend.inference_server:app.*--port \$REMOTE_TRANSLATION_PORT" >/dev/null; then
    echo "HY-MT translation server already running on port \$REMOTE_TRANSLATION_PORT"
    return
  fi
  check_venv_modules "HY-MT translation" "\$HYMT_VENV" fastapi uvicorn pydantic dotenv requests multipart transformers accelerate sentencepiece torch
  echo "Starting HY-MT translation server on port \$REMOTE_TRANSLATION_PORT ..."
  cd "\$REMOTE_APP_DIR"
  nohup bash -lc "source '\$HYMT_VENV/bin/activate' && export CUDA_VISIBLE_DEVICES='\$GPU_ID' PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True' HYMT_MODEL='\$HYMT_MODEL' HYMT_DTYPE='\$HYMT_DTYPE' HYMT_DEVICE_MAP='\$HYMT_DEVICE_MAP' HYMT_TARGET_LANGUAGE='\$HYMT_TARGET_LANGUAGE' HYMT_MAX_NEW_TOKENS='\$HYMT_MAX_NEW_TOKENS' HYMT_TEMPERATURE='\$HYMT_TEMPERATURE' HYMT_TOP_P='\$HYMT_TOP_P' HYMT_TOP_K='\$HYMT_TOP_K' HYMT_REPETITION_PENALTY='\$HYMT_REPETITION_PENALTY' && exec python -m uvicorn backend.inference_server:app --host 0.0.0.0 --port '\$REMOTE_TRANSLATION_PORT'" \
    > "\$REMOTE_LOG_DIR/hymt-translation.out.log" \
    2> "\$REMOTE_LOG_DIR/hymt-translation.err.log" &
}

start_ocr() {
  if [[ "\$ENABLE_OCR" != "true" ]]; then
    echo "Skipping remote OCR: ENABLE_OCR=\$ENABLE_OCR"
    return
  fi
  if pgrep -f "backend.inference_server:app.*--port \$REMOTE_OCR_PORT" >/dev/null; then
    echo "Remote OCR server already running on port \$REMOTE_OCR_PORT"
    return
  fi
  check_venv_modules "remote OCR" "\$PADDLEOCR_VL_VENV" fastapi uvicorn pydantic dotenv requests multipart
  echo "Starting remote OCR server on port \$REMOTE_OCR_PORT ..."
  cd "\$REMOTE_APP_DIR"
  local paddleocr_token_env=""
  if [[ -n "\$PADDLEOCR_API_TOKEN" ]]; then
    paddleocr_token_env="PADDLEOCR_API_TOKEN='\$PADDLEOCR_API_TOKEN'"
  fi
  nohup bash -lc "source '\$PADDLEOCR_VL_VENV/bin/activate' && export CUDA_VISIBLE_DEVICES='\$GPU_ID' OCR_ENABLED=true USE_REMOTE_OCR=false OCR_ENGINE='\$OCR_ENGINE' OCR_LANGUAGE_HINT='\$OCR_LANGUAGE_HINT' OCR_PADDLE_DEVICE='\$OCR_PADDLE_DEVICE' OCR_PADDLE_PIPELINE_VERSION='\$OCR_PADDLE_PIPELINE_VERSION' OCR_PADDLE_PROMPT_LABEL='\$OCR_PADDLE_PROMPT_LABEL' OCR_PADDLE_PROMPT_PRESET='\$OCR_PADDLE_PROMPT_PRESET' OCR_PADDLE_PROMPT_COMPOSE='\$OCR_PADDLE_PROMPT_COMPOSE' OCR_PADDLE_MAX_NEW_TOKENS='\$OCR_PADDLE_MAX_NEW_TOKENS' OCR_PADDLE_TEMPERATURE='\$OCR_PADDLE_TEMPERATURE' OCR_PADDLE_REPETITION_PENALTY='\$OCR_PADDLE_REPETITION_PENALTY' PADDLEOCR_API_JOB_URL='\$PADDLEOCR_API_JOB_URL' \$paddleocr_token_env PADDLEOCR_API_MODEL='\$PADDLEOCR_API_MODEL' PADDLEOCR_API_POLL_SECONDS='\$PADDLEOCR_API_POLL_SECONDS' PADDLEOCR_API_TIMEOUT_SECONDS='\$PADDLEOCR_API_TIMEOUT_SECONDS' && exec python -m uvicorn backend.inference_server:app --host 0.0.0.0 --port '\$REMOTE_OCR_PORT'" \
    > "\$REMOTE_LOG_DIR/paddleocr-vl.out.log" \
    2> "\$REMOTE_LOG_DIR/paddleocr-vl.err.log" &
}

stop_pattern() {
  local name="\$1"
  local pattern="\$2"
  local pids
  pids="\$(pgrep -f "\$pattern" || true)"
  if [[ -z "\$pids" ]]; then
    echo "\$name is not running"
    return
  fi
  echo "Stopping \$name: \$pids"
  kill \$pids 2>/dev/null || true
}

kill_lingering_pattern() {
  local name="\$1"
  local pattern="\$2"
  local pids
  pids="\$(pgrep -f "\$pattern" || true)"
  if [[ -n "\$pids" ]]; then
    echo "Force killing lingering \$name: \$pids"
    kill -9 \$pids 2>/dev/null || true
  fi
}

kill_extra_gpu_processes() {
  if [[ "\$KILL_EXTRA_GPU_PROCESSES_ON_STOP" != "true" ]]; then
    return
  fi
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    return
  fi

  local gpu_pids
  gpu_pids="\$(nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits -i "\$GPU_ID" 2>/dev/null | awk 'NF {print \$1}' | xargs 2>/dev/null || true)"
  if [[ -z "\$gpu_pids" ]]; then
    return
  fi

  echo "Stopping remaining GPU processes because KILL_EXTRA_GPU_PROCESSES_ON_STOP=true: \$gpu_pids"
  ps -fp \$gpu_pids || true
  kill \$gpu_pids 2>/dev/null || true
  sleep 3
  gpu_pids="\$(nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits -i "\$GPU_ID" 2>/dev/null | awk 'NF {print \$1}' | xargs 2>/dev/null || true)"
  if [[ -n "\$gpu_pids" ]]; then
    kill -9 \$gpu_pids 2>/dev/null || true
  fi
}

gpu_check() {
  local allow_reset="${1:-false}"

  if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "nvidia-smi not found; cannot verify GPU memory."
    return
  fi

  echo
  nvidia-smi || true

  local used
  used="\$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i "\$GPU_ID" 2>/dev/null | head -n1 | tr -dc '0-9' || true)"
  if [[ -z "\$used" ]]; then
    return
  fi
  if (( used <= GPU_MEMORY_WARN_MIB )); then
    echo "GPU \$GPU_ID memory looks released: \${used}MiB used."
    return
  fi

  local gpu_pids
  gpu_pids="\$(nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits -i "\$GPU_ID" 2>/dev/null | awk 'NF {print \$1}' | xargs 2>/dev/null || true)"
  echo "GPU \$GPU_ID still reports \${used}MiB used."
  if [[ -n "\$gpu_pids" ]]; then
    echo "Remaining GPU process ids: \$gpu_pids"
    ps -fp \$gpu_pids || true
  fi

  if [[ "\$allow_reset" != "true" ]]; then
    echo "GPU reset is only attempted during stop/restart."
  elif [[ "\$GPU_RESET_ON_STOP" == "true" || ( "\$GPU_RESET_ON_STOP" == "auto" && -z "\$gpu_pids" ) ]]; then
    echo "Attempting GPU reset for GPU \$GPU_ID ..."
    nvidia-smi --gpu-reset -i "\$GPU_ID" || true
    nvidia-smi || true
  else
    echo "GPU reset skipped. Set GPU_RESET_ON_STOP=true to force a reset attempt after stopping services."
  fi
}

show_process_env() {
  local label="\$1"
  local pattern="\$2"
  shift 2

  local pid
  pid="\$(pgrep -f "\$pattern" | head -n1 || true)"
  if [[ -z "\$pid" || ! -r "/proc/\$pid/environ" ]]; then
    return
  fi

  echo "\$label env:"
  local key_pattern
  key_pattern="\$(printf '%s\n' "\$@" | paste -sd '|' -)"
  tr '\0' '\n' < "/proc/\$pid/environ" | grep -E "^(\$key_pattern)=" || true
}

status_services() {
  echo "Remote service status on \$(hostname):"
  echo "Remote app dir: \$REMOTE_APP_DIR"
  echo "vLLM venv: \$REMOTE_VLLM_VENV"
  echo "vLLM gpu memory utilization: \$REMOTE_VLLM_GPU_MEMORY_UTILIZATION"
  echo "vLLM max model len: \$REMOTE_VLLM_MAX_MODEL_LEN"
  echo "VLM/LLM inference venv: \$REMOTE_INFERENCE_VENV"
  echo "Dolphin venv: \$DOLPHIN_VENV"
  echo "MMS venv: \$MMS_VENV"
  echo "HY-MT venv: \$HYMT_VENV"
  echo "PaddleOCR-VL venv: \$PADDLEOCR_VL_VENV"
  pgrep -af "vllm.entrypoints.openai.api_server" || echo "vLLM: stopped"
  pgrep -af "backend.inference_server:app.*--port \$REMOTE_INFERENCE_PORT" || echo "VLM/LLM inference: stopped"
  pgrep -af "backend.inference_server:app.*--port \$REMOTE_ASR_PORT" || echo "Dolphin ASR: stopped"
  pgrep -af "backend.inference_server:app.*--port \$REMOTE_MMS_ASR_PORT" || echo "MMS ASR: stopped"
  pgrep -af "backend.inference_server:app.*--port \$REMOTE_TRANSLATION_PORT" || echo "HY-MT translation: stopped"
  pgrep -af "backend.inference_server:app.*--port \$REMOTE_OCR_PORT" || echo "PaddleOCR-VL OCR: stopped"
  show_process_env "Dolphin ASR" "backend.inference_server:app.*--port \$REMOTE_ASR_PORT" \
    ASR_ENGINE ASR_LANGUAGE DOLPHIN_MODEL DOLPHIN_LANG_SYM DOLPHIN_REGION_SYM DOLPHIN_WORD_TIMESTAMP DOLPHIN_PREDICT_TIME
  show_process_env "MMS ASR" "backend.inference_server:app.*--port \$REMOTE_MMS_ASR_PORT" \
    MMS_MODEL MMS_TARGET_LANG MMS_DEVICE MMS_DTYPE MMS_CHUNK_LENGTH_S MMS_STRIDE_LENGTH_S MMS_BATCH_SIZE MMS_RETURN_TIMESTAMPS MMS_HF_ENDPOINT MMS_LOCAL_FILES_ONLY HF_ENDPOINT
  show_process_env "HY-MT translation" "backend.inference_server:app.*--port \$REMOTE_TRANSLATION_PORT" \
    HYMT_MODEL HYMT_DTYPE HYMT_DEVICE_MAP
  show_process_env "PaddleOCR-VL OCR" "backend.inference_server:app.*--port \$REMOTE_OCR_PORT" \
    OCR_ENGINE OCR_LANGUAGE_HINT OCR_PADDLE_DEVICE OCR_PADDLE_PIPELINE_VERSION OCR_PADDLE_PROMPT_LABEL OCR_PADDLE_PROMPT_PRESET OCR_PADDLE_PROMPT_COMPOSE PADDLEOCR_API_JOB_URL PADDLEOCR_API_MODEL PADDLEOCR_API_POLL_SECONDS PADDLEOCR_API_TIMEOUT_SECONDS
  gpu_check false
}

start_services() {
  echo "Remote app dir: \$REMOTE_APP_DIR"
  echo "vLLM venv: \$REMOTE_VLLM_VENV"
  echo "vLLM gpu memory utilization: \$REMOTE_VLLM_GPU_MEMORY_UTILIZATION"
  echo "vLLM max model len: \$REMOTE_VLLM_MAX_MODEL_LEN"
  echo "VLM/LLM inference venv: \$REMOTE_INFERENCE_VENV"
  echo "Dolphin venv: \$DOLPHIN_VENV"
  echo "MMS venv: \$MMS_VENV"
  echo "HY-MT venv: \$HYMT_VENV"
  echo "PaddleOCR-VL venv: \$PADDLEOCR_VL_VENV"
  echo
  start_vllm
  start_inference
  start_asr
  start_mms_asr
  start_translation
  start_ocr
  sleep 2
  status_services
}

install_deps() {
  install_service_deps \
    "VLM/LLM inference" \
    "\$REMOTE_INFERENCE_VENV" \
    fastapi uvicorn pydantic python-dotenv python-multipart requests

  install_service_deps \
    "Dolphin ASR" \
    "\$DOLPHIN_VENV" \
    fastapi uvicorn pydantic python-dotenv python-multipart requests

  install_service_deps \
    "MMS ASR" \
    "\$MMS_VENV" \
    fastapi uvicorn pydantic python-dotenv python-multipart requests

  install_service_deps \
    "HY-MT translation" \
    "\$HYMT_VENV" \
    fastapi uvicorn pydantic python-dotenv python-multipart requests

  install_service_deps \
    "PaddleOCR-VL OCR" \
    "\$PADDLEOCR_VL_VENV" \
    fastapi uvicorn pydantic python-dotenv python-multipart requests
}

stop_services() {
  stop_pattern "PaddleOCR-VL OCR" "backend.inference_server:app.*--port \$REMOTE_OCR_PORT"
  stop_pattern "HY-MT translation" "backend.inference_server:app.*--port \$REMOTE_TRANSLATION_PORT"
  stop_pattern "MMS ASR" "backend.inference_server:app.*--port \$REMOTE_MMS_ASR_PORT"
  stop_pattern "Dolphin ASR" "backend.inference_server:app.*--port \$REMOTE_ASR_PORT"
  stop_pattern "VLM/LLM inference" "backend.inference_server:app.*--port \$REMOTE_INFERENCE_PORT"
  stop_pattern "vLLM" "vllm.entrypoints.openai.api_server"
  sleep 5
  kill_lingering_pattern "PaddleOCR-VL OCR" "backend.inference_server:app.*--port \$REMOTE_OCR_PORT"
  kill_lingering_pattern "HY-MT translation" "backend.inference_server:app.*--port \$REMOTE_TRANSLATION_PORT"
  kill_lingering_pattern "MMS ASR" "backend.inference_server:app.*--port \$REMOTE_MMS_ASR_PORT"
  kill_lingering_pattern "Dolphin ASR" "backend.inference_server:app.*--port \$REMOTE_ASR_PORT"
  kill_lingering_pattern "VLM/LLM inference" "backend.inference_server:app.*--port \$REMOTE_INFERENCE_PORT"
  kill_lingering_pattern "vLLM" "vllm.entrypoints.openai.api_server"
  sleep 2
  kill_extra_gpu_processes
  sleep 1
  gpu_check true
}

case "\$ACTION" in
  start)
    start_services
    ;;
  stop)
    stop_services
    ;;
  restart)
    stop_services
    start_services
    ;;
  status)
    status_services
    ;;
  install-deps)
    install_deps
    ;;
esac
REMOTE
