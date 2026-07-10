# MMS ASR -> HY-MT Translation Smoke Test

目标：用 Meta MMS 做维吾尔语 ASR，再把识别出的维吾尔文文本交给 HY-MT 翻译成中文。

## 关键模型选择

`facebook/mms-1b` 是 MMS 的预训练底座，不是最适合直接跑 ASR 的 checkpoint。直接做 ASR 建议使用：

```text
facebook/mms-1b-all
```

维吾尔语 adapter 使用：

```text
uig-script_arabic
```

## 1. 上传脚本

可以继续使用服务器目录：

```bash
cd /mnt/workspace/paddleocr-vl-test
```

从本地上传：

```bash
scp experiments/mms_asr_smoke_test.py root@SERVER:/mnt/workspace/paddleocr-vl-test/mms_asr_smoke_test.py
scp experiments/hymt_translation_smoke_test.py root@SERVER:/mnt/workspace/paddleocr-vl-test/hymt_translation_smoke_test.py
```

## 2. MMS ASR 环境

建议给 MMS ASR 单独建一个 venv，不要混进 PaddleOCR-VL 环境：

```bash
cd /mnt/workspace/paddleocr-vl-test
python3 -m venv .venv-mms-asr
source .venv-mms-asr/bin/activate

python -m pip install -U pip
python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126
python -m pip install -U transformers accelerate soundfile librosa
```

如果 Hugging Face 下载慢：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

## 3. 跑 MMS ASR

先用短音频片段跑通：

```bash
cd /mnt/workspace/paddleocr-vl-test
source .venv-mms-asr/bin/activate
export CUDA_VISIBLE_DEVICES=0

python mms_asr_smoke_test.py videos/video.mp4 \
  --model facebook/mms-1b-all \
  --target-lang uig-script_arabic \
  --device cuda \
  --dtype float16 \
  --trim-seconds 90 \
  --chunk-length-s 20 \
  --stride-length-s 2
```

输出目录类似：

```text
outputs/mms_asr_tests/video/20260702_153000/
├── input_16k_mono.wav
├── text.txt
└── result.json
```

如果 90 秒能跑通，再跑完整文件：

```bash
python mms_asr_smoke_test.py videos/video.mp4 \
  --model facebook/mms-1b-all \
  --target-lang uig-script_arabic \
  --device cuda \
  --dtype float16 \
  --trim-seconds 0 \
  --chunk-length-s 20 \
  --stride-length-s 2
```

## 4. 用 HY-MT 翻译 ASR 输出

切到浑源翻译环境：

```bash
deactivate
source .venv-hymt/bin/activate
```

推荐用非量化原版 HY-MT，避免 GPTQ 后端问题：

```bash
python hymt_translation_smoke_test.py outputs/mms_asr_tests/video/20260702_153000 \
  --model tencent/HY-MT1.5-1.8B \
  --dtype bfloat16 \
  --num-samples 5 \
  --temperature 0.7 \
  --top-p 0.6 \
  --max-new-tokens 512
```

也可以直接传 ASR 的文本文件：

```bash
python hymt_translation_smoke_test.py outputs/mms_asr_tests/video/20260702_153000/text.txt \
  --model tencent/HY-MT1.5-1.8B \
  --dtype bfloat16 \
  --num-samples 5 \
  --temperature 0.7 \
  --top-p 0.6 \
  --max-new-tokens 512
```

## 5. 一条链路的最小命令

```bash
cd /mnt/workspace/paddleocr-vl-test

source .venv-mms-asr/bin/activate
python mms_asr_smoke_test.py videos/video.mp4 \
  --model facebook/mms-1b-all \
  --target-lang uig-script_arabic \
  --device cuda \
  --dtype float16 \
  --trim-seconds 90

deactivate
source .venv-hymt/bin/activate
python hymt_translation_smoke_test.py outputs/mms_asr_tests/video/<timestamp> \
  --model tencent/HY-MT1.5-1.8B \
  --dtype bfloat16 \
  --num-samples 5 \
  --temperature 0.7 \
  --top-p 0.6 \
  --max-new-tokens 512
```

把 `<timestamp>` 替换成上一步输出的实际目录名。
