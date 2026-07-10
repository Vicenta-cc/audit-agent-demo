# HY-MT Uyghur Translation Smoke Test

这个实验用于把 OCR/ASR 得到的维吾尔文文本接到 `tencent/HY-MT1.5-1.8B-GPTQ-Int4`，快速判断能否形成通顺中文。

它不重新跑 OCR，只读取已有的 `text.txt`、`result.json` 或整个输出目录。

## 1. 上传脚本

```bash
scp experiments/hymt_translation_smoke_test.py root@SERVER:/mnt/workspace/paddleocr-vl-test/hymt_translation_smoke_test.py
```

## 2. 安装依赖

建议继续用独立环境，避免影响 PaddleOCR-VL：

```bash
cd /mnt/workspace/paddleocr-vl-test
python3 -m venv .venv-hymt
. .venv-hymt/bin/activate
python -m pip install -U pip
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
python - <<'PY'
import torch
print(torch.__version__)
print(torch.cuda.is_available())
PY
python -m pip install transformers==4.56.0 accelerate sentencepiece
```

如果 Hugging Face 下载慢：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

先不要急着安装 `auto-gptq`。HY-MT 模型卡推荐的是 `transformers==4.56.0`。如果运行时明确报缺少 GPTQ backend，再执行：

```bash
python -m pip install optimum
python -m pip install --no-build-isolation auto-gptq==0.7.1
```

`--no-build-isolation` 很关键，否则 `auto-gptq` 构建环境可能看不到当前 venv 里已经安装的 `torch`，从而报 `No module named 'torch'`。

如果 `import torch` 报 `libtorch_global_deps.so: cannot open shared object file`，说明 PyTorch wheel 解压/安装不完整。直接重装当前 venv 里的 torch：

```bash
python -m pip uninstall -y torch torchvision torchaudio
python -m pip cache purge
python -m pip install --no-cache-dir --force-reinstall torch torchvision torchaudio \
  --index-url https://download.pytorch.org/whl/cu126

python - <<'PY'
import torch
print(torch.__version__)
print(torch.cuda.is_available())
print(torch.version.cuda)
PY
```

如果仍然缺动态库，优先新建 Python 3.11 venv 再装；不要在坏掉的 venv 上继续叠依赖。

## 3. 翻译单个 OCR 输出

假设 PaddleOCR-VL 输出目录是：

```text
outputs/paddleocr_vl_tests/20260702_114930/
```

直接传整个目录：

```bash
python hymt_translation_smoke_test.py outputs/paddleocr_vl_tests/20260702_114930 \
  --model tencent/HY-MT1.5-1.8B-GPTQ-Int4 \
  --num-samples 5 \
  --temperature 0.7 \
  --top-p 0.6 \
  --max-new-tokens 512
```

也可以传某个文本文件：

```bash
python hymt_translation_smoke_test.py outputs/paddleocr_vl_tests/20260702_114930/image16/text.txt \
  --num-samples 5
```

## 4. 输出

默认保存到：

```text
outputs/hymt_translation_tests/<timestamp>/
├── result.json
└── <sample_id>/
    ├── source.txt
    ├── prompt.txt
    ├── translation_01.txt
    ├── translation_02.txt
    └── ...
```

`result.json` 里会记录：

- 原始 OCR 文本
- 每次翻译结果
- 简单中文通顺度分数
- `pass_rate`
- `average_pass_rate_percent`

这个分数是启发式指标，不是模型置信度。它主要检查译文里中文字符比例、是否残留大量维吾尔/阿拉伯字母、是否输出了提示词残留等。最终仍建议人工抽样判断。

## 5. 推荐对比方式

先把同一张图分别跑两种 OCR：

```bash
python paddleocr_vl_smoke_test.py images/image16.jpg \
  --device gpu:0 \
  --pipeline-version v1.6 \
  --prompt-label ocr \
  --max-new-tokens 1024 \
  --temperature 0 \
  --repetition-penalty 1.05
```

再跑增强/其他 OCR 方案，然后分别接 HY-MT：

```bash
python hymt_translation_smoke_test.py outputs/paddleocr_vl_tests/<timestamp> \
  --num-samples 5
```

比较不同 OCR 输出的 `average_pass_rate_percent` 和人工可读性，判断“识别 + 翻译”整条链路是否可用。
