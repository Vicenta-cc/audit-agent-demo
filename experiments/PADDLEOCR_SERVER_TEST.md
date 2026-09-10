# PaddleOCR Uyghur OCR Server Smoke Test

这个实验用于快速判断 PaddleOCR 对你准备好的维语图片是否可用，不接入现有审核流水线。

PaddleOCR 官方文档说明 PP-OCRv5 支持命令行和 Python API，`lang` 可以指定识别语言；PP-OCRv5 多语言文档里列出 `ug` 是 Uyghur，且 `arabic_PP-OCRv5_mobile_rec` 支持 Arabic、Persian、Uyghur、Urdu 等阿拉伯文字系语言。

## 1. 服务器目录

建议放在：

```text
/mnt/workspace/paddleocr-test/
├── .venv-paddleocr/
├── paddleocr_smoke_test.py
├── images/
│   ├── sample_01.jpg
│   └── sample_02.png
└── outputs/
```

本机上传：

```bash
ssh user@SERVER 'mkdir -p /mnt/workspace/paddleocr-test/images'
scp experiments/paddleocr_smoke_test.py user@SERVER:/mnt/workspace/paddleocr-test/paddleocr_smoke_test.py
scp /path/to/your/images/* user@SERVER:/mnt/workspace/paddleocr-test/images/
```

## 2. 建 venv

```bash
cd /mnt/workspace/paddleocr-test
python3 -m venv .venv-paddleocr
. .venv-paddleocr/bin/activate
python -m pip install -U pip
```

如果服务器有 `python3.12`，可以用：

```bash
python3.12 -m venv .venv-paddleocr
. .venv-paddleocr/bin/activate
python -m pip install -U pip
```

## 3. 安装 PaddlePaddle + PaddleOCR

CPU 版：

```bash
python -m pip install paddlepaddle -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
python -m pip install paddleocr
```

GPU 版要匹配服务器 CUDA。比如 CUDA 11.8：

```bash
python -m pip install paddlepaddle-gpu -i https://www.paddlepaddle.org.cn/packages/stable/cu118/
python -m pip install paddleocr
```

如果服务器是 CUDA 12.x，先看官方 PaddlePaddle 安装页选对应 index，不要硬套 cu118。

## 4. 跑维语图片 OCR

```bash
cd /mnt/workspace/paddleocr-test
. .venv-paddleocr/bin/activate

python paddleocr_smoke_test.py 'images/*' \
  --lang ug \
  --device gpu:0
```

如果 GPU 环境没配好，先用 CPU 验证流程：

```bash
python paddleocr_smoke_test.py 'images/*' \
  --lang ug \
  --device cpu
```

输出目录：

```text
outputs/paddleocr_tests/<timestamp>/
├── result.json
├── texts.txt
└── <image_name>/
    ├── raw.json
    └── text.txt
```

## 5. 怎么判断结果

优先人工看这几项：

- `texts.txt` 里的维语是否是真实可读，而不是阿语/波斯语形似字符乱串。
- 右到左文本顺序是否反了。
- 人名、地名、宗教词、敏感词是否漏识别。
- 低清、压缩、字幕描边、弯曲文字是否明显掉点。

如果整图 OCR 很差，下一步不要急着换 VLM，而是先做图片预处理实验：裁剪字幕区域、放大 2x、去噪、增强对比度，再跑同一脚本比较。
