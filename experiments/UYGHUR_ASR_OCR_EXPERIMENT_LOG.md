# Uyghur ASR / OCR Experiment Log

Last updated: 2026-07-02

This document records the Uyghur ASR and OCR experiments tried so far, including local/server environments, install commands, run commands, observed errors, fixes, and current conclusions.

## 0. Repository And Assets

Local repository:

```text
/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo
```

Main experiment scripts:

```text
experiments/qwen3_asr_smoke_test.py
experiments/whisper_uyghur_smoke_test.py
experiments/dolphin_asr_smoke_test.py
experiments/paddleocr_smoke_test.py
experiments/paddleocr_vl_smoke_test.py
experiments/tesseract_uig_smoke_test.py
```

Per-experiment docs already created:

```text
experiments/QWEN3_ASR_SERVER_TEST.md
experiments/WHISPER_UYGHUR_TEST.md
experiments/DOLPHIN_ASR_TEST.md
experiments/PADDLEOCR_SERVER_TEST.md
experiments/PADDLEOCR_VL_TEST.md
experiments/TESSERACT_UIG_SERVER_TEST.md
```

Local test video and extracted audio:

```text
videos/video.mp4
videos/video_16k_mono.wav
```

Local ffmpeg:

```text
/Users/ext.wanghongtao6/Documents/software/ffmpeg
```

Local Tesseract Uyghur best model:

```text
local_models/tessdata-best/uig.traineddata
```

## 1. Current High-Level Status

| Direction | Model / Tool | Status | Current Judgment |
| --- | --- | --- | --- |
| ASR | Qwen3-ASR-0.6B | Environment/model download tested, not a good Uyghur candidate | Official language support does not include Uyghur; keep only as baseline/control. |
| ASR | ixxan/whisper-small-uyghur-common-voice | Ran successfully | Recognizes Uyghur-ish audio but outputs Latin transliteration, with repetition/hallucination on test video. Not ideal if downstream expects Arabic-script Uyghur. |
| ASR | DataoceanAI Dolphin | In progress | Promising because it explicitly supports `ug` / `ug-CN`; script patched to avoid torchaudio/torchcodec issue via `soundfile`. |
| ASR | lucio/xls-r-uyghur-cv8 | Discussed, not yet run | Worth testing next. It may output Arabic-script Uyghur, but likely needs short chunks/VAD. |
| OCR | PaddleOCR `lang=ug` | Ran locally | Detected text regions but recognition quality was poor on video subtitles. |
| OCR | PaddleOCR-VL | Environment tested on server, blocked by deps/model/GPU memory issues | Could be better than classical OCR, but cannot clearly force Uyghur language in the same way as `lang=ug`. |
| OCR | Tesseract `uig` + `tessdata_best` | Ran, almost all乱码 | Not suitable for low-quality colored video subtitles; useful as a negative baseline. |

## 2. Local Environment Notes

Local machine appears to have no usable NVIDIA GPU for these tests. PaddleOCR local environment is CPU-only.

Local PaddleOCR CPU venv:

```bash
cd /Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo
python3 -m venv .venv-paddleocr
. .venv-paddleocr/bin/activate
python -m pip install -U pip
python -m pip install paddlepaddle
python -m pip install paddleocr
```

Check Paddle/CUDA:

```bash
python - <<'PY'
import paddle
print("paddle", paddle.__version__)
print("cuda available", paddle.device.is_compiled_with_cuda())
PY
```

Extract local audio from video:

```bash
/Users/ext.wanghongtao6/Documents/software/ffmpeg \
  -hide_banner -nostdin -y \
  -i /Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/videos/video.mp4 \
  -vn -acodec pcm_s16le -ar 16000 -ac 1 \
  /Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/videos/video_16k_mono.wav
```

## 3. Server Environment Notes

Main server workspace:

```text
/mnt/workspace
```

Observed GPU:

```text
NVIDIA H20
Driver Version: 570.133.20
CUDA Version: 12.8
GPU memory: about 97GB
```

Useful checks:

```bash
nvidia-smi
nvcc --version
python - <<'PY'
import torch
print("torch", torch.__version__)
print("torch cuda", torch.version.cuda)
print("cuda available", torch.cuda.is_available())
if torch.cuda.is_available():
    print(torch.cuda.get_device_name(0))
PY
```

The ASR venv was ultimately fixed to:

```text
torch 2.11.0+cu126
torchaudio 2.11.0+cu126
torch cuda 12.6
cuda available True
GPU: NVIDIA H20
```

Recommended PyTorch reinstall for this server:

```bash
python -m pip uninstall -y torch torchvision torchaudio torchcodec
python -m pip install torch torchvision torchaudio \
  --index-url https://download.pytorch.org/whl/cu126
```

If the newest package selected by pip is incompatible with the driver, pin to the working family:

```bash
python -m pip uninstall -y torch torchvision torchaudio torchcodec
python -m pip install torch==2.11.0 torchaudio==2.11.0 \
  --index-url https://download.pytorch.org/whl/cu126
```

## 4. Upload Pattern

Use the real server address in place of `root@SERVER`.

```bash
scp experiments/qwen3_asr_smoke_test.py root@SERVER:/mnt/workspace/qwen3-asr-test/qwen3_asr_smoke_test.py
scp experiments/whisper_uyghur_smoke_test.py root@SERVER:/mnt/workspace/whisper-uyghur-test/whisper_uyghur_smoke_test.py
scp experiments/dolphin_asr_smoke_test.py root@SERVER:/mnt/workspace/qwen3-asr-test/dolphin_asr_smoke_test.py
scp experiments/paddleocr_smoke_test.py root@SERVER:/mnt/workspace/paddleocr-test/paddleocr_smoke_test.py
scp experiments/paddleocr_vl_smoke_test.py root@SERVER:/mnt/workspace/paddleocr-vl-test/paddleocr_vl_smoke_test.py
scp experiments/tesseract_uig_smoke_test.py root@SERVER:/mnt/workspace/tesseract-uig-test/tesseract_uig_smoke_test.py
```

Upload the current video:

```bash
scp videos/video.mp4 root@SERVER:/mnt/workspace/qwen3-asr-test/video.mp4
```

Upload the Tesseract Uyghur model:

```bash
scp local_models/tessdata-best/uig.traineddata \
  root@SERVER:/mnt/workspace/tesseract-uig-test/tessdata-best/uig.traineddata
```

## 5. Qwen3-ASR-0.6B

Server directory:

```text
/mnt/workspace/qwen3-asr-test
```

Venv:

```bash
cd /mnt/workspace
python3 -m venv .venv-qwen3-asr
. .venv-qwen3-asr/bin/activate
python -m pip install -U pip
python -m pip install -U qwen-asr
```

Install/fix CUDA PyTorch:

```bash
python -m pip uninstall -y torch torchvision torchaudio torchcodec
python -m pip install torch torchvision torchaudio \
  --index-url https://download.pytorch.org/whl/cu126
```

Run:

```bash
cd /mnt/workspace/qwen3-asr-test
. /mnt/workspace/.venv-qwen3-asr/bin/activate
export CUDA_VISIBLE_DEVICES=0

python qwen3_asr_smoke_test.py video.mp4 \
  --model /mnt/workspace/models/Qwen3-ASR-0.6B \
  --trim-seconds 90 \
  --device cuda \
  --dtype bfloat16 \
  --also-force-nearby
```

Hugging Face network issue observed:

```text
Failed to establish a new connection: [Errno 101] Network is unreachable
```

Stop a hanging download/install:

```bash
Ctrl+C
```

ModelScope download attempt:

```bash
mkdir -p /mnt/workspace/models/Qwen3-ASR-0.6B
modelscope download \
  --model Qwen/Qwen3-ASR-0.6B \
  --local_dir /mnt/workspace/models/Qwen3-ASR-0.6B
```

Observed ModelScope/system Torch error:

```text
ImportError: libtorch_cuda.so: undefined symbol: ncclDevCommDestroy
```

Meaning: the `modelscope` command used a system Python/Torch with incompatible CUDA/NCCL. Prefer running downloads inside the venv, or upload model files from a machine that can download them.

Observed PyTorch CUDA mismatch:

```text
RuntimeError: The NVIDIA driver on your system is too old (found version 12080)
```

Meaning: Torch was compiled for a newer CUDA runtime than the server driver supports. Use `cu126` wheels on this server.

Current conclusion:

- Qwen3-ASR-0.6B is not the main Uyghur ASR candidate.
- Keep it as a baseline only.
- If it outputs Arabic/Turkish/Persian-looking text, it may still be semantically wrong for Uyghur.

## 6. Whisper Uyghur

Model:

```text
ixxan/whisper-small-uyghur-common-voice
```

Server directory:

```text
/mnt/workspace/whisper-uyghur-test
```

Install:

```bash
cd /mnt/workspace/whisper-uyghur-test
python3 -m venv .venv-whisper-uyghur
. .venv-whisper-uyghur/bin/activate
python -m pip install -U pip
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
python -m pip install -U transformers accelerate soundfile librosa
```

If Hugging Face is slow/blocked:

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

Alternative: reuse the fixed ASR venv:

```bash
. /mnt/workspace/.venv-qwen3-asr/bin/activate
python -m pip install -U transformers accelerate soundfile librosa
```

Run:

```bash
cd /mnt/workspace/whisper-uyghur-test
. /mnt/workspace/.venv-qwen3-asr/bin/activate
export CUDA_VISIBLE_DEVICES=0

python whisper_uyghur_smoke_test.py video.mp4 \
  --model ixxan/whisper-small-uyghur-common-voice \
  --trim-seconds 90 \
  --device cuda \
  --dtype float16
```

Observed output style:

```text
ikkimiz béshimizmu ...
... newri, newri, newri ...
```

Current conclusion:

- It appears to recognize Uyghur-like speech, but outputs Latin transliteration rather than Arabic-script Uyghur.
- The tested clip had severe repetition/hallucination at the end.
- It is useful as a comparison, but not ideal for direct downstream Uyghur text processing unless transliteration is acceptable or converted later.

## 7. Dolphin ASR

Project:

```text
https://github.com/DataoceanAI/Dolphin
```

Language list includes:

```text
ug
ug-CN
```

Recommended reuse of existing ASR venv:

```bash
cd /mnt/workspace/qwen3-asr-test
. /mnt/workspace/.venv-qwen3-asr/bin/activate
python -m pip install -U dataoceanai-dolphin soundfile
```

Run with Uyghur forced:

```bash
cd /mnt/workspace/qwen3-asr-test
. /mnt/workspace/.venv-qwen3-asr/bin/activate
export CUDA_VISIBLE_DEVICES=0

python dolphin_asr_smoke_test.py video.mp4 \
  --model small \
  --model-dir /mnt/workspace/models/dolphin \
  --lang-sym ug \
  --region-sym CN \
  --trim-seconds 90 \
  --device cuda
```

Try `NULL` region if `CN` behaves oddly:

```bash
python dolphin_asr_smoke_test.py video.mp4 \
  --model small \
  --model-dir /mnt/workspace/models/dolphin \
  --lang-sym ug \
  --region-sym NULL \
  --trim-seconds 90 \
  --device cuda
```

Try base model:

```bash
python dolphin_asr_smoke_test.py video.mp4 \
  --model base \
  --model-dir /mnt/workspace/models/dolphin \
  --lang-sym ug \
  --region-sym CN \
  --trim-seconds 90 \
  --device cuda
```

Error from first wrong script version:

```text
TypeError: ASRModel.forward() got an unexpected keyword argument 'lang_sym'
```

Fix: use Dolphin's `transcribe(model, audio_path, lang_sym=..., region_sym=...)` interface instead of calling the model forward directly.

Torchaudio/TorchCodec issue:

```text
ImportError: TorchCodec is required for load_with_torchcodec.
```

Then after installing torchcodec, another incompatibility appeared:

```text
OSError: libnvrtc.so.13: cannot open shared object file
```

Fix in current script:

- `experiments/dolphin_asr_smoke_test.py` defaults to `--audio-loader soundfile`.
- It monkeypatches `torchaudio.load` to use `soundfile`, avoiding TorchCodec.
- Make sure `soundfile` is installed.

Current conclusion:

- Dolphin is the strongest current ASR path because it explicitly supports Uyghur and can force `ug`.
- Next meaningful comparison should be Dolphin `small` vs `base`, `region=CN` vs `NULL`, on the same 90-second clip.

## 8. XLS-R Uyghur

Model discussed:

```text
lucio/xls-r-uyghur-cv8
```

Status:

- Not yet run in this repo.
- Worth testing because it is a Uyghur-specific XLS-R/CTC-style model.
- It may be better for Arabic-script Uyghur output than the Whisper model, but CTC models often need cleaner segmentation.

Suggested next test design:

- Extract 16k mono WAV.
- Split with VAD or fixed 10-20 second windows.
- Compare output script, CER-like quality, repetitions, and sensitivity to background music.

## 9. PaddleOCR `lang=ug`

Local environment:

```bash
cd /Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo
python3 -m venv .venv-paddleocr
. .venv-paddleocr/bin/activate
python -m pip install -U pip
python -m pip install paddlepaddle
python -m pip install paddleocr
```

Server directory:

```text
/mnt/workspace/paddleocr-test
```

Server install:

```bash
cd /mnt/workspace/paddleocr-test
python3 -m venv .venv-paddleocr
. .venv-paddleocr/bin/activate
python -m pip install -U pip
python -m pip install paddlepaddle
python -m pip install paddleocr
```

Run:

```bash
python paddleocr_smoke_test.py 'images/*' \
  --lang ug \
  --device cpu
```

Or GPU:

```bash
python paddleocr_smoke_test.py 'images/*' \
  --lang ug \
  --device gpu:0
```

Important note about `arabic_PP-OCRv5_mobile_rec`:

- It is a lightweight recognition model for Arabic-script language families.
- It supports Arabic, Persian, Uyghur, Urdu, etc.
- It is not the same thing as PaddleOCR-VL.
- `PaddleOCR(lang="ug")` may route to an Arabic-script recognition model, but real subtitle quality still depends heavily on detection, font, compression, and preprocessing.

Observed quality on `image16`:

- Text boxes were detected.
- Recognition was mostly unusable or not semantically valid.
- It caught a few fragments like `توي`, `بار`, and approximations of `قىزلار` / `قىلغوسی`, but many characters/lines were wrong.

Reference text provided by user:

```text
‫خه نزو پیگیت بیله‬
 ‫توي قىلغوسی بار‬
‫بويتاق قىزلار مبنی‬
     ‫نزدهك‪.‬‬
```

Current conclusion:

- PaddleOCR `lang=ug` is useful as a baseline.
- It is not reliable enough for these compressed colored video subtitles as-is.

## 10. PaddleOCR-VL

Server directory:

```text
/mnt/workspace/paddleocr-vl-test
```

Install attempt:

```bash
cd /mnt/workspace/paddleocr-vl-test
python3 -m venv .venv-paddleocr-vl
. .venv-paddleocr-vl/bin/activate
python -m pip install -U pip
python -m pip install paddleocr==3.7.0
python -m pip install 'paddlex[ocr]==3.7.2'
```

Run:

```bash
export CUDA_VISIBLE_DEVICES=0

python paddleocr_vl_smoke_test.py images \
  --device gpu:0 \
  --max-new-tokens 1024
```

Useful variants:

```bash
python paddleocr_vl_smoke_test.py images/image16.jpg \
  --device gpu:0 \
  --prompt-label ocr \
  --max-new-tokens 1024
```

Dependency error observed:

```text
RuntimeError: A dependency error occurred during pipeline creation.
```

Fix:

```bash
python -m pip install 'paddlex[ocr]==3.7.2'
```

Model file error observed:

```text
ValueError: No valid model files were found for engine 'paddle_dynamic'.
```

Meaning:

- Model cache/model directory was incomplete or for the wrong engine.
- Need a clean redownload or a complete local model directory.

GPU memory error observed:

```text
CUBLAS_STATUS_ALLOC_FAILED
```

Cause found:

- Old vLLM/Qwen3-VL service was occupying about 89GB GPU memory.
- `nvidia-smi` did not clearly list active processes in the process table, but `/proc/*/fd/*` revealed hidden NVIDIA device holders.

Commands used to find GPU holders:

```bash
nvidia-smi
ps -ef | egrep 'vllm|qwen|python|paddle|uvicorn' | grep -v grep
lsof /dev/nvidia* 2>/dev/null | head -50
ls -l /proc/*/fd/* 2>/dev/null | grep nvidia | head -50
cat /proc/<PID>/cmdline | tr '\0' ' '
echo
```

Kill only confirmed stale processes:

```bash
kill -9 <PID>
```

Observed stale process label:

```text
VLLM::EngineCore
```

Current conclusion:

- PaddleOCR-VL may be worth trying again after GPU memory and model cache are clean.
- PaddleOCR-VL cannot force `lang=ug` like `PaddleOCR(lang="ug")`, but local failures should be debugged against the HuggingFace demo settings first.
- Important API detail: `prompt_label=ocr` takes effect only when `use_layout_detection=False`. If local inference accidentally runs full layout parsing, it is not the same task as element-level text recognition in the online demo.
- The smoke-test script now supports `--prompt-preset uyghur-strong-en` and `--prompt-preset uyghur-strong-zh`. This uses an experimental hook to override PaddleOCR-VL's internal VLM query, so treat it as an A/B diagnostic rather than a guaranteed official API.

HuggingFace demo source check:

- Source: https://huggingface.co/spaces/PaddlePaddle/PaddleOCR-VL_Online_Demo/resolve/main/app.py
- `"Text Recognition"` maps to `promptLabel="ocr"`.
- The targeted recognition path calls the API with `use_layout_detection=False`, `use_doc_unwarping=False`, and `use_doc_orientation_classify=False`.
- Therefore the closest local reproduction is not a custom natural-language prompt first; it is element-level OCR mode with `--prompt-label ocr` and no layout/doc preprocessing.
- Keep `--pipeline-version v1.6` for current tests. The smoke-test script has a version switch, but the recommended OCR comparison below intentionally does not use old `v1`.

Recommended PaddleOCR-VL retest order:

```bash
cd /mnt/workspace/paddleocr-vl-test
. .venv-paddleocr-vl/bin/activate
export CUDA_VISIBLE_DEVICES=0

# 1. HF-demo-like element OCR mode.
python paddleocr_vl_smoke_test.py images/image16.jpg \
  --device gpu:0 \
  --pipeline-version v1.6 \
  --prompt-label ocr \
  --lite-output \
  --skip-save-assets \
  --max-new-tokens 1024 \
  --temperature 0 \
  --repetition-penalty 1.05

# 2. Same image with Uyghur language constraint in the VLM query.
python paddleocr_vl_smoke_test.py images/image16.jpg \
  --device gpu:0 \
  --pipeline-version v1.6 \
  --prompt-label ocr \
  --prompt-preset uyghur-strong-en \
  --lite-output \
  --skip-save-assets \
  --max-new-tokens 1024 \
  --temperature 0 \
  --repetition-penalty 1.05
```

If command 1 works, the original local failure was probably parameter/task mismatch. If command 1 fails but command 2 improves, prompt language bias is a real factor. If both fail while the online demo works on the exact same uploaded image, compare PaddleOCR/PaddleX/model versions and cached model files.

If the active backend rejects or ignores generation parameters, rerun after removing `--temperature` and `--repetition-penalty`; keep the image, `--prompt-label ocr`, and `--prompt-preset` fixed so the A/B comparison stays meaningful.

Optional v1.6 enhanced-preprocessing comparison:

```bash
python paddleocr_vl_smoke_test.py images/image16.jpg \
  --device gpu:0 \
  --pipeline-version v1.6 \
  --use-doc-unwarping \
  --use-doc-orientation-classify \
  --use-chart-recognition \
  --use-layout-detection \
  --use-ocr-for-image-block \
  --max-new-tokens 1024
```

This keeps PaddleOCR-VL at `v1.6` and tests document preprocessing/layout parsing. It is a different experiment from reproducing the online demo's text recognition mode, because `prompt_label=ocr` only applies when layout detection is disabled.

## 11. Tesseract Uyghur OCR

Server directory:

```text
/mnt/workspace/tesseract-uig-test
```

Create venv:

```bash
mkdir -p /mnt/workspace/tesseract-uig-test/images
mkdir -p /mnt/workspace/tesseract-uig-test/tessdata-best
cd /mnt/workspace/tesseract-uig-test
python3 -m venv .venv-tesseract-uig
. .venv-tesseract-uig/bin/activate
python -m pip install -U pip
python -m pip install opencv-python numpy
```

Install Tesseract system package:

```bash
apt-get update
apt-get install -y tesseract-ocr
```

If apt cache is full:

```bash
apt-get clean
rm -rf /var/cache/apt/archives/*.deb
apt-get update
apt-get install -y tesseract-ocr
```

Download `tessdata_best` Uyghur model locally:

```bash
mkdir -p /Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/local_models/tessdata-best

curl -L -o /Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/local_models/tessdata-best/uig.traineddata \
  https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/uig.traineddata
```

If GitHub raw is slow, the mirror that worked locally was:

```bash
curl -L -o /Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/local_models/tessdata-best/uig.traineddata \
  https://gh.llkk.cc/https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/uig.traineddata
```

Upload model to server:

```bash
scp /Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo/local_models/tessdata-best/uig.traineddata \
  root@SERVER:/mnt/workspace/tesseract-uig-test/tessdata-best/uig.traineddata
```

Verify:

```bash
tesseract --tessdata-dir /mnt/workspace/tesseract-uig-test/tessdata-best --list-langs
```

Expected:

```text
uig
```

Run without crop:

```bash
cd /mnt/workspace/tesseract-uig-test
. .venv-tesseract-uig/bin/activate

python tesseract_uig_smoke_test.py images/image16.jpg \
  --lang uig \
  --tessdata-dir /mnt/workspace/tesseract-uig-test/tessdata-best \
  --psm 6 \
  --psm 7 \
  --psm 11 \
  --psm 13
```

Run with auto bottom crop:

```bash
python tesseract_uig_smoke_test.py images/image16.jpg \
  --lang uig \
  --tessdata-dir /mnt/workspace/tesseract-uig-test/tessdata-best \
  --auto-bottom-crop \
  --bottom-ratio 0.42 \
  --psm 6 \
  --psm 7 \
  --psm 11 \
  --psm 13
```

Run with manual crop:

```bash
python tesseract_uig_smoke_test.py images/image16.jpg \
  --lang uig \
  --tessdata-dir /mnt/workspace/tesseract-uig-test/tessdata-best \
  --crop 80,1050,720,520 \
  --psm 6 \
  --psm 7 \
  --psm 11 \
  --psm 13
```

Final "single-line death test":

```bash
python tesseract_uig_smoke_test.py images/image16.jpg \
  --lang uig \
  --tessdata-dir /mnt/workspace/tesseract-uig-test/tessdata-best \
  --crop 80,1050,720,120 \
  --scale 3 \
  --psm 7
```

Script bug fixed:

```text
Error, cannot read input file .../variants/bottom_0.42_x3_adaptive.png
```

Cause:

- The script did not create the `variants` directory before `cv2.imwrite`.
- `cv2.imwrite` failed silently.

Fix:

- Current `experiments/tesseract_uig_smoke_test.py` now creates the directory and checks `cv2.imwrite` return value.

Current conclusion:

- Tesseract `uig.traineddata` + `tessdata_best` is still nearly all乱码 on the test subtitle image.
- It is probably trained for clearer scanned/printed text, not colorful compressed video subtitles.
- Do not spend much more time on Tesseract unless using custom training data.

## 12. GPU Memory Troubleshooting

When `nvidia-smi` shows high memory usage but no obvious process:

```bash
nvidia-smi
ps -ef | egrep 'vllm|qwen|python|paddle|uvicorn' | grep -v grep
lsof /dev/nvidia* 2>/dev/null | head -50
ls -l /proc/*/fd/* 2>/dev/null | grep nvidia | head -50
```

Inspect a suspicious PID:

```bash
cat /proc/<PID>/cmdline | tr '\0' ' '
echo
ps -fp <PID>
```

Kill only if it is confirmed stale and safe:

```bash
kill -9 <PID>
```

Example stale service found:

```text
python -m vllm.entrypoints.openai.api_server ...
VLLM::EngineCore
uvicorn backend.inference_server:app ...
```

## 13. Current Recommended Next Steps

1. ASR first: rerun Dolphin with the latest script and `soundfile` installed.

```bash
cd /mnt/workspace/qwen3-asr-test
. /mnt/workspace/.venv-qwen3-asr/bin/activate
python -m pip install soundfile

python dolphin_asr_smoke_test.py video.mp4 \
  --model small \
  --model-dir /mnt/workspace/models/dolphin \
  --lang-sym ug \
  --region-sym CN \
  --trim-seconds 90 \
  --device cuda
```

2. Compare Dolphin `CN` vs `NULL`.

```bash
python dolphin_asr_smoke_test.py video.mp4 \
  --model small \
  --model-dir /mnt/workspace/models/dolphin \
  --lang-sym ug \
  --region-sym NULL \
  --trim-seconds 90 \
  --device cuda
```

3. If Dolphin is poor, implement and test `lucio/xls-r-uyghur-cv8`.

4. For OCR, stop treating Tesseract/PaddleOCR classical OCR as final solution. Use them as baselines only.

5. For subtitle OCR, try a VLM workflow:

```text
1. Detect/crop subtitle region.
2. First run PaddleOCR-VL in HF-demo-like OCR mode: layout detection off, prompt_label=ocr.
3. Then rerun the same frame with a Uyghur-specific query/prompt override.
4. Prompt idea: "The image contains Uyghur Arabic-script subtitles. Transcribe the visible text exactly. Do not translate. Preserve line breaks."
5. Translate the transcribed text with a dedicated translation model/tool.
```

Translation smoke test added:

```bash
python hymt_translation_smoke_test.py outputs/paddleocr_vl_tests/<timestamp> \
  --model tencent/HY-MT1.5-1.8B-GPTQ-Int4 \
  --num-samples 5 \
  --temperature 0.7 \
  --top-p 0.6
```

See `experiments/HYMT_TRANSLATION_TEST.md`. This estimates whether OCR text can become fluent Chinese after translation; it is a downstream usability check, not proof that OCR is exact.

6. Long-term production path:

```text
Collect real short-video Uyghur subtitle frames.
Annotate exact Arabic-script Uyghur text.
Fine-tune OCR/VLM or train a subtitle-specific recognizer.
Use ASR as primary signal and OCR as auxiliary signal.
```

## 14. Practical Conclusion

For the current pure Uyghur video problem:

- ASR is currently more promising than OCR.
- Qwen3-ASR is not a reliable Uyghur ASR candidate.
- Whisper Uyghur works but outputs Latin transliteration and may hallucinate.
- Dolphin is the most promising ASR candidate to continue.
- PaddleOCR `lang=ug` and Tesseract `uig` are weak on the current video subtitle frames.
- PaddleOCR-VL or a general VLM may help, but language forcing and prompt stability must be tested.
- If this becomes a production requirement, custom Uyghur subtitle data is likely necessary.
