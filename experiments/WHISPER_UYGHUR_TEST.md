# Uyghur Whisper Smoke Test

Model: `ixxan/whisper-small-uyghur-common-voice`

This is a fine-tuned `openai/whisper-small` model trained on Uyghur Common Voice. It should be tested separately from `qwen-asr`, because it uses the standard Hugging Face Transformers Whisper interface.

## Server Directory

```text
/mnt/workspace/whisper-uyghur-test/
├── .venv-whisper-uyghur/
├── whisper_uyghur_smoke_test.py
├── video.mp4
└── outputs/
```

## Upload

```bash
ssh user@SERVER 'mkdir -p /mnt/workspace/whisper-uyghur-test'
scp experiments/whisper_uyghur_smoke_test.py user@SERVER:/mnt/workspace/whisper-uyghur-test/whisper_uyghur_smoke_test.py
scp /path/to/video.mp4 user@SERVER:/mnt/workspace/whisper-uyghur-test/video.mp4
```

## Install

Use a separate venv or reuse the Qwen ASR venv after fixing Torch CUDA.

```bash
cd /mnt/workspace/whisper-uyghur-test
python3 -m venv .venv-whisper-uyghur
. .venv-whisper-uyghur/bin/activate
python -m pip install -U pip

# For CUDA 12.8 driver, use cu126 PyTorch:
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
python -m pip install -U transformers accelerate soundfile librosa
```

If Hugging Face is blocked:

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

## Run

```bash
cd /mnt/workspace/whisper-uyghur-test
. .venv-whisper-uyghur/bin/activate
export CUDA_VISIBLE_DEVICES=0

python whisper_uyghur_smoke_test.py video.mp4 \
  --model ixxan/whisper-small-uyghur-common-voice \
  --trim-seconds 90 \
  --device cuda \
  --dtype float16
```

Output:

```text
outputs/whisper_uyghur_tests/<video_name>/<timestamp>/
├── input_16k_mono.wav
├── text.txt
└── result.json
```
