# Qwen3-ASR Server Smoke Test

这个实验只用于在 GPU 服务器上快速判断 `Qwen/Qwen3-ASR-0.6B` 对维吾尔语视频是否可用，不接入现有审核流水线。

官方模型卡写明 Qwen3-ASR 支持 30 种语言和 22 种中文方言，但不包含 Uyghur/维吾尔语。因此测试重点不是“能不能输出文字”，而是看它是否把维语误识别成 Arabic/Turkish/Persian，并产生看似流畅但语义错误的文本。

## 1. 上传文件

建议统一放到服务器的 `/mnt/workspace/qwen3-asr-test`：

```bash
ssh user@SERVER 'mkdir -p /mnt/workspace/qwen3-asr-test'
scp experiments/qwen3_asr_smoke_test.py user@SERVER:/mnt/workspace/qwen3-asr-test/qwen3_asr_smoke_test.py
scp data/test.mp4 user@SERVER:/mnt/workspace/qwen3-asr-test/test.mp4
```

如果要测试另一个视频，把 `data/test.mp4` 换成你的本地维语视频路径。

## 2. 服务器环境

优先用 `venv` 独立环境，避免污染现有推理服务：

```bash
python3 -m venv .venv-qwen3-asr
. .venv-qwen3-asr/bin/activate
python -m pip install -U pip
python -m pip install -U qwen-asr
```

如果服务器有多个 Python 版本，优先用 Python 3.12：

```bash
python3.12 -m venv .venv-qwen3-asr
. .venv-qwen3-asr/bin/activate
python -m pip install -U pip
python -m pip install -U qwen-asr
```

服务器还需要 `ffmpeg`：

```bash
ffmpeg -version
```

如果没有：

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg
```

## 3. 首次跑 0.6B

先只跑前 90 秒，确认依赖、显存、模型下载都正常：

```bash
cd /mnt/workspace/qwen3-asr-test
python qwen3_asr_smoke_test.py test.mp4 \
  --model Qwen/Qwen3-ASR-0.6B \
  --trim-seconds 90 \
  --device cuda \
  --dtype bfloat16 \
  --also-force-nearby
```

脚本会输出：

- `auto.txt`：自动语言识别结果
- `arabic.txt`、`turkish.txt`、`persian.txt`：强制相邻语言识别结果
- `result.json`：完整结果、耗时、模型配置

默认结果目录：

```text
outputs/qwen3_asr_tests/<video_name>/<timestamp>/
```

## 4. 国内服务器下载权重

如果 Hugging Face 下载慢，可以先用 ModelScope 下载：

```bash
python -m pip install -U modelscope
modelscope download --model Qwen/Qwen3-ASR-0.6B --local_dir /data/models/Qwen3-ASR-0.6B
```

然后用本地目录跑：

```bash
cd /mnt/workspace/qwen3-asr-test
python qwen3_asr_smoke_test.py test.mp4 \
  --model /data/models/Qwen3-ASR-0.6B \
  --trim-seconds 90 \
  --device cuda \
  --dtype bfloat16 \
  --also-force-nearby
```

## 5. 怎么判断结果

优先看这几件事：

- `auto` 检测出来的语言是否长期落在 Arabic/Turkish/Persian。
- 维语人名、地名、宗教词、敏感词是否能稳定保留。
- 输出是否只是“看起来像阿拉伯字母文本”，但人工读起来不成句。
- 同一段音频在 `auto`、`Arabic`、`Turkish`、`Persian` 下差异是否很大。

如果维语人工读者判断 `auto.txt` 明显不可用，就不要把 Qwen3-ASR 当主 ASR。可以把它保留为对照组，再测专门支持维语的 MMS 或商业维语 ASR。
