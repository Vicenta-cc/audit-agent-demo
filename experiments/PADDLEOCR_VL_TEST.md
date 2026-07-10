# PaddleOCR-VL Smoke Test

这个实验用于单独测试 PaddleOCR-VL。它不是 `PaddleOCR(lang="ug")` 那条轻量 OCR pipeline，而是 PaddleOCR 的视觉语言模型文档解析流程。

官方文档入口：

- PaddleOCR-VL 算法介绍：https://www.paddleocr.ai/main/version3.x/algorithm/PaddleOCR-VL/PaddleOCR-VL.html
- PaddleOCR-VL 使用教程：https://www.paddleocr.ai/main/version3.x/pipeline_usage/PaddleOCR-VL.html

## 本地 CPU 测试

你本地的 `.venv-paddleocr` 已经能导入 `PaddleOCRVL`，并已补装 `paddlex[ocr]==3.7.2`。但 PaddlePaddle 在 macOS 上是 CPU 版。首次运行会自动下载 `PaddleOCR-VL-1.6-0.9B`，CPU 会比较慢，建议先只跑一张图：

```bash
cd /Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-demo
. .venv-paddleocr/bin/activate

python experiments/paddleocr_vl_smoke_test.py data/ocr_images/image16.jpg \
  --device cpu \
  --max-new-tokens 1024
```

输出：

```text
outputs/paddleocr_vl_tests/<timestamp>/image16/
├── raw.json
└── text.txt
```

## 服务器 GPU 测试

建议目录：

```text
/mnt/workspace/paddleocr-vl-test/
├── .venv-paddleocr-vl/
├── paddleocr_vl_smoke_test.py
├── images/
│   └── image16.jpg
└── outputs/
```

上传：

```bash
ssh user@SERVER 'mkdir -p /mnt/workspace/paddleocr-vl-test/images'
scp experiments/paddleocr_vl_smoke_test.py user@SERVER:/mnt/workspace/paddleocr-vl-test/paddleocr_vl_smoke_test.py
scp data/ocr_images/image16.jpg user@SERVER:/mnt/workspace/paddleocr-vl-test/images/image16.jpg
```

每次本地脚本有更新后，都要重新上传到服务器。服务器上可以用下面的命令确认是不是新版脚本：

```bash
python paddleocr_vl_smoke_test.py --help | grep -E 'pipeline-version|chart-recognition'
```

安装：

```bash
cd /mnt/workspace/paddleocr-vl-test
python3 -m venv .venv-paddleocr-vl
. .venv-paddleocr-vl/bin/activate
python -m pip install -U pip

# CPU smoke test:
python -m pip install paddlepaddle
python -m pip install paddleocr
python -m pip install 'paddlex[ocr]==3.7.2'
```

如果服务器要用 GPU，需要按服务器 CUDA 版本安装对应的 `paddlepaddle-gpu`，例如 CUDA 11.8：

```bash
python -m pip install paddlepaddle-gpu -i https://www.paddlepaddle.org.cn/packages/stable/cu118/
python -m pip install paddleocr
python -m pip install 'paddlex[ocr]==3.7.2'
```

运行：

```bash
cd /mnt/workspace/paddleocr-vl-test
. .venv-paddleocr-vl/bin/activate

python paddleocr_vl_smoke_test.py images/image16.jpg \
  --device gpu:0 \
  --max-new-tokens 1024
```

## 复刻在线 Demo 的 OCR 模式

HuggingFace 在线 demo 里选择文字识别时，本质上是让 PaddleOCR-VL 走 element-level OCR，而不是整页 layout parsing。官方接口里对应的关键点是：

- 关闭 layout detection。
- 使用 `prompt_label=ocr`，它只在 `use_layout_detection=False` 时生效。
- 关闭 document unwarping。
- 关闭 orientation classification。

源码核对：

- `handle_targeted_recognition()` 把 `"Text Recognition"` 映射为 `"ocr"`。
- 随后调用 `_call_api(..., use_layout_detection=False, prompt_label=label, use_doc_unwarping=False, use_doc_orientation_classify=False)`。
- `_call_api()` 在 layout detection 关闭时把 `promptLabel` 写入请求 payload。
- Source: https://huggingface.co/spaces/PaddlePaddle/PaddleOCR-VL_Online_Demo/resolve/main/app.py

当前脚本默认就是关闭 layout detection、document unwarping、orientation classification。为了让服务器命令更显式，建议先这样跑一张图：

```bash
python paddleocr_vl_smoke_test.py images/image16.jpg \
  --device gpu:0 \
  --pipeline-version v1.6 \
  --prompt-label ocr \
  --lite-output \
  --skip-save-assets \
  --max-new-tokens 1024 \
  --temperature 0 \
  --repetition-penalty 1.05
```

如果这个结果接近在线 demo，说明本地失败主要是推理入口/参数差异，不是模型本身完全不认维语。

## v1.6 增强预处理对比实验

如果你想保留 `v1.6`，同时开启文档去畸变、方向分类和图表识别，可以这样跑第二组对照：

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

注意：这组不是在线 demo 的 Text Recognition 路径。因为一旦开启 `--use-layout-detection`，`--prompt-label ocr` 就不生效了；模型会先做版面检测，再按 text/table/chart/image 等 block 类型识别。对短视频字幕帧来说，这组参数不一定更好：`use_doc_unwarping` / `use_doc_orientation_classify` 更偏扫描文档，`use_chart_recognition` 更偏图表。字幕 OCR 的首选对照仍然是上面的 `v1.6 + --prompt-label ocr` element OCR 模式。

## 维语强约束 Prompt 实验

PaddleOCR-VL 的公开 Python pipeline 不是通用聊天 VLM 接口，文档没有提供任意自然语言 prompt 参数。脚本里现在加了一个实验性 hook，可以覆盖内部传给 VLM 的 query，用来验证“强语言约束”是否能改善维语字幕识别。

英文强约束版本：

```bash
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

中文强约束版本：

```bash
python paddleocr_vl_smoke_test.py images/image16.jpg \
  --device gpu:0 \
  --prompt-label ocr \
  --prompt-preset uyghur-strong-zh \
  --lite-output \
  --skip-save-assets \
  --max-new-tokens 1024 \
  --temperature 0 \
  --repetition-penalty 1.05
```

也可以把自己的 prompt 写到文件里：

```bash
python paddleocr_vl_smoke_test.py images/image16.jpg \
  --device gpu:0 \
  --prompt-label ocr \
  --custom-prompt-file uyghur_ocr_prompt.txt \
  --max-new-tokens 1024 \
  --temperature 0
```

每次运行会在 `result.json` 里记录 `prompt_override`、`prompt_override_installed`、`prompt_label` 和 `vlm_extra_args`，确认实际跑的是哪一种 prompt。

如果 DSW 可写磁盘很小，建议加 `--lite-output --skip-save-assets`。这样只保存 `text.txt` 和紧凑 `result.json`，避免完整 VLM 原始结果和可视化图片把磁盘写满。

如果当前后端对生成参数支持不一致，`--temperature`、`--top-p`、`--repetition-penalty` 可能被忽略或报错。遇到这种情况先删掉这些生成参数，只保留 `--prompt-label ocr` 和 `--prompt-preset ...` 对比。

## 说明

- 默认没有开启 layout detection，因为你当前是视频帧字幕，不是复杂文档版面。
- 如果要测文档/海报，可以加 `--use-layout-detection`。
- 如果 CPU 很慢，这是正常的；PaddleOCR-VL 比 `arabic_PP-OCRv5_mobile_rec` 重得多。
- 如果遇到 `PaddleOCR-VL-1.6 requires additional dependencies`，说明缺 `paddlex[ocr]`，执行 `python -m pip install 'paddlex[ocr]==3.7.2'`。
- 默认模型会下载到 `~/.paddlex/official_models/PaddleOCR-VL-1.6`。
- 这次测试结果可以和 PaddleOCR `lang=ug` 的 `text.txt` 直接对比。
- 如果在线 demo 能识别、本地不能识别，先对比 `use_layout_detection`、`prompt_label`、`max_new_tokens`、`temperature`、模型版本和图片预处理，而不是直接下结论说模型不支持维语。
