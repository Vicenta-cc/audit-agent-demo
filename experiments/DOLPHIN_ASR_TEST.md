# Dolphin Uyghur ASR Smoke Test

Dolphin is a multilingual multitask ASR model from DataoceanAI and Tsinghua University. It supports ASR, VAD, segmentation, and language identification, and its language list includes `ug` / `ug-CN` for Uyghur.

Docs:

- GitHub: https://github.com/DataoceanAI/Dolphin
- Languages: https://github.com/DataoceanAI/Dolphin/blob/main/languages.md

## Reuse Existing ASR Server Env

You can reuse `/mnt/workspace/.venv-qwen3-asr` if Torch CUDA has already been fixed to `cu126`.

```bash
cd /mnt/workspace/qwen3-asr-test
. /mnt/workspace/.venv-qwen3-asr/bin/activate

python -m pip install -U dataoceanai-dolphin
```

If you want a clean env:

```bash
mkdir -p /mnt/workspace/dolphin-asr-test
cd /mnt/workspace/dolphin-asr-test
python3 -m venv .venv-dolphin
. .venv-dolphin/bin/activate
python -m pip install -U pip
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
python -m pip install -U dataoceanai-dolphin
```

## Upload Script

```bash
scp experiments/dolphin_asr_smoke_test.py user@SERVER:/mnt/workspace/qwen3-asr-test/dolphin_asr_smoke_test.py
```

## Run

Use your existing video:

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

If `region-sym CN` behaves oddly, try:

```bash
python dolphin_asr_smoke_test.py video.mp4 \
  --model small \
  --model-dir /mnt/workspace/models/dolphin \
  --lang-sym ug \
  --region-sym NULL \
  --trim-seconds 90 \
  --device cuda
```

You can also test the smaller/faster model:

```bash
python dolphin_asr_smoke_test.py video.mp4 \
  --model base \
  --model-dir /mnt/workspace/models/dolphin \
  --lang-sym ug \
  --region-sym CN \
  --trim-seconds 90 \
  --device cuda
```

Output:

```text
outputs/dolphin_asr_tests/<video_name>/<timestamp>/
├── input_16k_mono.wav
├── text.txt
└── result.json
```

## Why Dolphin Is Worth Testing

- It explicitly supports `ug` Uyghur, unlike Qwen3-ASR.
- It supports language and region tokens, so you can force Uyghur decoding.
- It should output normal text for the chosen language instead of the Latin transliteration seen in the fine-tuned Whisper model, but this must be verified on your actual videos.
