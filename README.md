# XHS Audit Agent Demo

这是一个小红书“宗教”主题内容审核 demo 骨架，借鉴了：

- `D:\good-agent\MediaCrawler`：负责小红书关键词爬取。
- `D:\good-agent\video-to-txt`：负责视频抽音频、Whisper 转写、关键帧抽取的处理思路。

当前 MVP 只做：

- 平台：小红书。
- 关键词：默认 `宗教`，前端可改。
- 内容：标题、正文、正文图片、视频语音、视频关键帧、评论文本。
- 不处理评论图片。
- 大模型：阿里百炼 Qwen/Qwen-VL，使用 OpenAI 兼容接口。

## 安全提示

不要把 API Key 写进代码、前端或 Git。你之前贴出的 key 建议立刻在百炼控制台轮换。

复制环境变量模板：

```cmd
copy .env.example .env
```

然后编辑 `.env`，填入新的 `DASHSCOPE_API_KEY`。

## 安装

```cmd
cd D:\good-agent\xhs-audit-agent-demo
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

还需要确保：

- `D:\good-agent\MediaCrawler` 能运行。
- FFmpeg 已安装并加入 PATH。
- 如果分析视频语音，需要首次下载 faster-whisper 模型。

## 启动

```cmd
.venv\Scripts\activate
uvicorn backend.main:app --host 127.0.0.1 --port 8090 --reload
```

如果你只是想先快速试 UI，也可以临时复用 MediaCrawler 已有虚拟环境：

```cmd
cd D:\good-agent\xhs-audit-agent-demo
D:\good-agent\MediaCrawler\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8090 --reload
```

打开：

```text
http://127.0.0.1:8090
```

## Demo 流程

1. 前端选择小红书，输入关键词，如 `宗教`。
2. 后端启动 MediaCrawler：
   - `--platform xhs`
   - `--type search`
   - `--save_data_option jsonl`
3. 后端读取 MediaCrawler 生成的 JSONL 内容和评论。
4. 每条笔记进入审核流水线：
   - 文本：标题、正文、评论文本。
   - 图片：Qwen-VL 做 OCR、画面描述、风险证据。
   - 视频：Whisper 转写语音，OpenCV 抽关键帧，Qwen-VL 分析关键帧。
   - 融合：Qwen 文本模型输出结构化审核 JSON。

## 当前边界

这是 demo 骨架，不是生产审核系统。宗教内容本身不是违规，违规取决于具体规则和证据。模型输出必须以证据为准，不能因为出现宗教关键词就直接拒绝。
