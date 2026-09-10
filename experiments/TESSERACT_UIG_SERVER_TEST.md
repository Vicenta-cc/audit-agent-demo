# Tesseract Uyghur OCR Smoke Test

这个实验用来和 PaddleOCR 的 `lang=ug` 结果做对照。Tesseract 对预处理很敏感，所以脚本会对原图、字幕区域、放大、灰度增强、二值化版本分别跑 `psm`。

## 1. 服务器目录

```text
/mnt/workspace/tesseract-uig-test/
├── tesseract_uig_smoke_test.py
├── images/
│   └── image16.jpg
└── outputs/
```

上传：

```bash
ssh user@SERVER 'mkdir -p /mnt/workspace/tesseract-uig-test/images'
scp experiments/tesseract_uig_smoke_test.py user@SERVER:/mnt/workspace/tesseract-uig-test/tesseract_uig_smoke_test.py
scp data/ocr_images/image16.jpg user@SERVER:/mnt/workspace/tesseract-uig-test/images/image16.jpg
```

## 2. 安装 Tesseract 和维语包

Debian/Ubuntu：

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-uig python3-opencv python3-numpy
```

检查语言包：

```bash
tesseract --list-langs | grep uig
```

## 3. 跑测试

```bash
cd /mnt/workspace/tesseract-uig-test

python3 tesseract_uig_smoke_test.py 'images/*' \
  --lang uig \
  --auto-bottom-crop \
  --bottom-ratio 0.42
```

如果你知道字幕区域，可以显式裁剪，格式是 `x,y,w,h`：

```bash
python3 tesseract_uig_smoke_test.py images/image16.jpg \
  --lang uig \
  --crop 80,1050,720,520 \
  --psm 6 \
  --psm 7 \
  --psm 11
```

输出目录：

```text
outputs/tesseract_uig_tests/<timestamp>/
```

里面会有每个预处理版本的图片，以及对应 OCR 结果。

## 4. 判断标准

用人工原文对照：

```text
خه نزو پیگیت بیله
توي قىلغوسی بار
بويتاق قىزلار مبنی
نزدهك.
```

重点看：

- 是否能稳定识别 `توي`、`قىلغوسی`、`بار`、`بويتاق`、`قىزلار`。
- 是否还会出现大量无关字符、数字、重复词。
- 行序是否符合右到左文本阅读。
- 翻译软件能否基于 OCR 文本得到基本通顺的中文。
