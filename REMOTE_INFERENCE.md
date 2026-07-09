# Remote Inference Setup

Goal: keep the web UI, crawler, video download, audio extraction, keyframe extraction, storage and result display on the local machine, while running GPU-heavy services on the server.

## Services

The default split is:

- `9000`: Qwen VLM/LLM inference server, backed by vLLM on `8001`
- `9001`: Dolphin ASR server, using `/mnt/workspace/paddleocr-vl-test/.venv-dolphin`
- `9002`: HY-MT translation server, using `/mnt/workspace/paddleocr-vl-test/.venv-hymt`

Default venvs:

- vLLM: `/root/vllm-019-env`
- VLM/LLM inference wrapper: `/root/xhs-audit-agent-demo/.venv`
- Dolphin ASR: `/mnt/workspace/paddleocr-vl-test/.venv-dolphin`
- HY-MT: `/mnt/workspace/paddleocr-vl-test/.venv-hymt`

Local tunnels default to:

- `19000 -> 9000`
- `19001 -> 9001`
- `19002 -> 9002`

## Daily Commands

Start or stop remote GPU services only when you need to change model config or release GPU memory:

```bash
./scripts/remote-stack.sh start
./scripts/remote-stack.sh status
./scripts/remote-stack.sh stop
```

Start or stop local frontend/backend/tunnels freely while developing:

```bash
./scripts/start-stack.sh
./scripts/stop-stack.sh
```

`stop-stack.sh` does not touch remote GPU services. `remote-stack.sh stop` terminates the known remote service processes, force-kills lingering ones, then checks GPU memory. If memory is still high and `nvidia-smi` reports no remaining compute process, the default `GPU_RESET_ON_STOP=auto` attempts a GPU reset.

If you want the stop command to also kill any remaining GPU compute process, run:

```bash
KILL_EXTRA_GPU_PROCESSES_ON_STOP=true ./scripts/remote-stack.sh stop
```

## Defaults

Current Dolphin default is explicit Uyghur decoding for testing, because Dolphin auto language detection is not reliable enough for this workflow:

```env
ASR_ENGINE=dolphin
ASR_LANGUAGE=ug
DOLPHIN_MODEL=small
DOLPHIN_MODEL_DIR=/mnt/workspace/models/dolphin
DOLPHIN_LANG_SYM=ug
DOLPHIN_REGION_SYM=CN
DOLPHIN_AUDIO_LOADER=soundfile
```

Force Uyghur for a test:

```bash
DOLPHIN_LANG_SYM=ug DOLPHIN_REGION_SYM=CN ./scripts/remote-stack.sh restart
```

Force Chinese for a test:

```bash
DOLPHIN_LANG_SYM=zh DOLPHIN_REGION_SYM=CN ./scripts/remote-stack.sh restart
```

HY-MT defaults:

```env
HYMT_MODEL=/mnt/workspace/models/HY-MT1.5-1.8B
HYMT_DTYPE=bfloat16
HYMT_MAX_NEW_TOKENS=256
HYMT_TEMPERATURE=0
HYMT_REPETITION_PENALTY=1.15
```

The local backend automatically calls HY-MT only when ASR text looks Uyghur or Arabic-script.

## Switches

Disable HY-MT:

```bash
ENABLE_HYMT=false ./scripts/remote-stack.sh restart
ENABLE_HYMT=false ./scripts/start-stack.sh
```

Disable Dolphin ASR:

```bash
ENABLE_DOLPHIN=false ./scripts/remote-stack.sh restart
ENABLE_DOLPHIN=false ./scripts/start-stack.sh
```

Use a different server or port:

```bash
SERVER=root@your.server SSH_PORT=6099 ./scripts/remote-stack.sh status
SERVER=root@your.server SSH_PORT=6099 ./scripts/start-stack.sh
```

## Notes

- Do not run Whisper and Dolphin resident at the same time on a memory-limited GPU. Keep both code paths, but deploy one `ASR_ENGINE`.
- The Dolphin and HY-MT venvs must also have service dependencies: `fastapi`, `uvicorn`, `pydantic`, `python-dotenv`, `python-multipart`, and `requests`.
- `REMOTE_ASR_BASE_URL` and `REMOTE_TRANSLATION_BASE_URL` are separate so Dolphin and HY-MT can live in different venvs.
- `USE_REMOTE_WHISPER` is still accepted for backward compatibility, but new deployments should use `USE_REMOTE_ASR`.
