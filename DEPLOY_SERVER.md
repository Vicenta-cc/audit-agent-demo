# Server Backend + Qwen3-VL Deployment

This project can run with the frontend on your local machine and the backend on a GPU server.

## 1. Start Qwen3-VL with vLLM on the server

Your model folder is a HuggingFace/safetensors directory, so vLLM can serve it directly.

```bash
python -m vllm.entrypoints.openai.api_server \
  --host 127.0.0.1 \
  --port 8001 \
  --model /root/models/Qwen3-VL-8B-Instruct \
  --served-model-name qwen3-vl-8b \
  --trust-remote-code
```

If you want other machines to call vLLM directly, change `--host` to `0.0.0.0`. For this project, keeping vLLM on `127.0.0.1` is safer because only the FastAPI backend needs to call it.

Quick check:

```bash
curl http://127.0.0.1:8001/v1/models
```

Text request check:

```bash
curl http://127.0.0.1:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer local" \
  -d '{
    "model": "qwen3-vl-8b",
    "messages": [{"role": "user", "content": "只输出 JSON：{\"ok\": true}"}],
    "temperature": 0
  }'
```

Image request check:

```bash
python - <<'PY'
import base64
import requests

image_path = "/root/xhs-audit-agent-demo/outputs/4bccf865eb2e/crawler/xhs/images/6a38c6bf00000000110121bd/0.jpg"
encoded = base64.b64encode(open(image_path, "rb").read()).decode("utf-8")
payload = {
    "model": "qwen3-vl-8b",
    "messages": [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
            {"type": "text", "text": "描述这张图片，只输出一句话。"},
        ],
    }],
    "temperature": 0,
}
response = requests.post(
    "http://127.0.0.1:8001/v1/chat/completions",
    headers={"Authorization": "Bearer local", "Content-Type": "application/json"},
    json=payload,
    timeout=120,
)
print(response.status_code)
print(response.text)
PY
```

## 2. Configure the backend on the server

Copy this project to the server, then create or edit `.env`:

```env
DASHSCOPE_BASE_URL=http://127.0.0.1:8001/v1
DASHSCOPE_API_KEY=local
QWEN_TEXT_MODEL=qwen3-vl-8b
QWEN_VL_MODEL=qwen3-vl-8b
QWEN_USE_RESPONSE_FORMAT=false
VL_IMAGE_MAX_SIDE=1024
VL_IMAGE_QUALITY=75
CORS_ALLOW_ORIGINS=*
MEDIACRAWLER_DIR=/root/MediaCrawler
MAX_IMAGES_PER_NOTE=4
MAX_VIDEO_FRAMES=6
WHISPER_MODEL=base
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
WHISPER_CPU_FALLBACK=true
```

Install and start the backend:

```bash
cd /root/xhs-audit-agent-demo
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8090
```

For GPU Whisper transcription, make sure the server has NVIDIA driver, CUDA runtime, and cuDNN libraries that match `faster-whisper` / `ctranslate2`. If CUDA loading fails and `WHISPER_CPU_FALLBACK=true`, the job will continue on CPU and the transcript result will include `fallback_reason`.

Quick check from your local machine:

```bash
curl http://SERVER_IP:8090/api/config
curl http://SERVER_IP:8090/api/outputs
```

`/api/config` should show:

```json
{
  "model_base_url": "http://127.0.0.1:8001/v1",
  "qwen_text_model": "qwen3-vl-8b",
  "qwen_vl_model": "qwen3-vl-8b",
  "qwen_use_response_format": false,
  "vl_image_max_side": 1024,
  "vl_image_quality": 75,
  "whisper_device": "cuda",
  "whisper_compute_type": "float16"
}
```

## 3. Point the local frontend to the server backend

On your local project, edit:

```js
// frontend/config.js
window.XHS_AUDIT_API_BASE = "http://SERVER_IP:8090";
```

Then open:

```text
frontend/index.html
```

or serve the frontend with any static server:

```bash
cd frontend
python -m http.server 5173
```

Then open:

```text
http://127.0.0.1:5173
```

The frontend will call the server backend through `window.XHS_AUDIT_API_BASE`.

## Recommended first test

1. Put existing crawler outputs under the server project `outputs/`.
2. Open the UI locally.
3. Uncheck `重新爬取`.
4. Pick an existing output.
5. Set `分析条数` to `1`.
6. Start the job.

After this works, increase `分析条数` gradually.
