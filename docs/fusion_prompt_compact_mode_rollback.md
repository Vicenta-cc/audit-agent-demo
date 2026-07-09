# Fusion Prompt Compact Mode 变更记录与回退说明

日期：2026-07-03

## 背景

视频关键帧数量从 1 帧提升到 20 帧后，最终融合审核 prompt 会把每帧完整 VLM JSON、OCR 对齐信息、ASR、OCR 轨道、评论一起塞给 LLM。

远端 vLLM 当前配置为：

```bash
REMOTE_VLLM_GPU_MEMORY_UTILIZATION=0.35
REMOTE_VLLM_MAX_MODEL_LEN=12288
```

在该配置下，某条 20 帧视频融合审核触发了 vLLM 上下文长度错误：

```text
maximum context length is 12288 tokens
requested 2048 output tokens
prompt contains at least 10241 input tokens
total at least 12289 tokens
```

为了保证 OCR / ASR / HY-MT 继续常驻，暂时不扩大 vLLM 上下文，而是改成“VLM 分析多帧，融合阶段只喂精简证据”。

## 当前改动

### 1. 关键帧数量

文件：

- `.env`
- `backend/audit_agent/config.py`

当前配置：

```bash
MAX_VIDEO_FRAMES=10
```

代码默认值：

```python
max_video_frames = int(os.getenv("MAX_VIDEO_FRAMES", "10"))
```

### 2. 融合 prompt 新增压缩参数

文件：`backend/audit_agent/config.py`

新增配置项：

```python
fusion_max_comments = int(os.getenv("FUSION_MAX_COMMENTS", "20"))
fusion_comment_max_chars = int(os.getenv("FUSION_COMMENT_MAX_CHARS", "160"))
fusion_max_ocr_states = int(os.getenv("FUSION_MAX_OCR_STATES", "24"))
fusion_ocr_text_max_chars = int(os.getenv("FUSION_OCR_TEXT_MAX_CHARS", "180"))
fusion_frame_summary_max_chars = int(os.getenv("FUSION_FRAME_SUMMARY_MAX_CHARS", "180"))
fusion_frame_max_risk_items = int(os.getenv("FUSION_FRAME_MAX_RISK_ITEMS", "3"))
fusion_frame_max_ocr_items = int(os.getenv("FUSION_FRAME_MAX_OCR_ITEMS", "2"))
fusion_frame_evidence_max_chars = int(os.getenv("FUSION_FRAME_EVIDENCE_MAX_CHARS", "5000"))
```

### 3. 评论输入压缩

文件：`backend/audit_agent/pipeline.py`

原来融合 prompt 直接取前 30 条评论完整文本：

```python
comments_text = "\n".join(
    f"- comment:{c.get('comment_id')}: {c.get('content', '')}"
    for c in subject.comments[:30]
)
```

现在改为：

```python
comments=self._format_comments_for_prompt(subject.comments)
```

效果：

- 默认最多 20 条评论。
- 每条评论默认最多 160 字。
- 无评论时输出 `（无评论）`。

### 4. 视频 OCR 轨道压缩

文件：`backend/audit_agent/pipeline.py`

函数：

```python
_format_video_ocr_tracks_for_prompt()
```

当前行为：

- 按 OCR 中文译文或原文去重。
- 默认最多保留 24 条 OCR 状态。
- 单条 OCR 文本默认最多 180 字。
- 中文译文和原文合并到一行。
- 被省略的 OCR 状态会追加省略提示。

### 5. 关键帧完整 JSON 改为 compact evidence

文件：`backend/audit_agent/pipeline.py`

原来融合 prompt 直接塞完整帧 JSON：

```python
frame_analyses=json.dumps([v.get("frames") for v in video_results], ensure_ascii=False, indent=2)
```

现在改为：

```python
frame_analyses=self._format_frame_evidence_for_prompt(video_results)
```

当前保留字段：

```json
{
  "video": 1,
  "frame": 1,
  "timestamp": 0.12,
  "visual_summary": "...",
  "risk_items": [
    {
      "severity": "low",
      "risk_type": "...",
      "evidence": "...",
      "reason": "..."
    }
  ],
  "ocr": [
    {
      "text_zh": "...",
      "text": "...",
      "time_range": "0.0-1.0s",
      "alignment_confidence": 0.91
    }
  ],
  "ocr_alignment_status": "matched",
  "benign_context": "..."
}
```

当前明确不送入融合 prompt 的字段：

- `path`
- `frame_path`
- `frame_asset_rel`
- `raw_response`
- 大段完整 OCR 对齐 JSON
- 本地文件绝对路径

注意：这些字段只是从融合 prompt 中移除，原始分析结果仍然保留。前端证据展示仍由原始 `video_results.frames[].path` 和后端生成的 `asset_rel` 支撑。

## 回退到完整 JSON 模式

如果后续想恢复“完整 JSON 送入融合审核”的旧模式，可以按下面步骤改回。

### 1. 恢复关键帧数量

如果要恢复 20 帧：

`.env`：

```bash
MAX_VIDEO_FRAMES=20
```

`backend/audit_agent/config.py`：

```python
max_video_frames = int(os.getenv("MAX_VIDEO_FRAMES", "20"))
```

### 2. 恢复评论旧逻辑

在 `backend/audit_agent/pipeline.py` 的 `_analyze_subject()` 中，把：

```python
comments=self._format_comments_for_prompt(subject.comments)
```

改回：

```python
comments="\n".join(
    f"- comment:{c.get('comment_id')}: {c.get('content', '')}"
    for c in subject.comments[:30]
)
```

### 3. 恢复关键帧完整 JSON

在 `_analyze_subject()` 中，把：

```python
frame_analyses=self._format_frame_evidence_for_prompt(video_results)
```

改回：

```python
frame_analyses=json.dumps([v.get("frames") for v in video_results], ensure_ascii=False, indent=2)
```

### 4. 可选：恢复 OCR 轨道完整模式

如果需要恢复 OCR 全量输入，可以把 `_format_video_ocr_tracks_for_prompt()` 中的去重、`fusion_max_ocr_states` 限制和 `fusion_ocr_text_max_chars` 截断去掉。

旧模式核心逻辑是：

```python
for state in track.get("states", []) or []:
    text = (state.get("text") or "").strip()
    text_zh = (state.get("text_zh") or "").strip()
    if not text and not text_zh:
        continue
    start = float(state.get("start_ts") or state.get("sample_ts") or 0.0)
    end = float(state.get("end_ts") or start)
    engine = state.get("engine") or "paddleocr_vl"
    if text_zh:
        lines.append(f"[OCR中文/{engine} {start:.1f}-{end:.1f}] {text_zh}")
    if text:
        lines.append(f"[OCR原文/{state.get('language_hint') or 'ug'} {start:.1f}-{end:.1f}] {text}")
```

### 5. 可选：保留函数但绕过 compact

如果只是临时回退，可以不删除新增 helper，只在 `_analyze_subject()` 里恢复完整 JSON 传参。这样后续再切回 compact 模式时更方便。

## 验证命令

```bash
python3 -m py_compile backend/audit_agent/config.py backend/audit_agent/pipeline.py
```

重启本地栈：

```bash
./scripts/stop-stack.sh
./scripts/start-stack.sh
```

## 推荐策略

当前远端仍建议保持：

```bash
REMOTE_VLLM_GPU_MEMORY_UTILIZATION=0.35
REMOTE_VLLM_MAX_MODEL_LEN=12288
```

在这个显存策略下，不建议恢复完整 JSON + 20 帧模式，否则容易再次触发 vLLM context length 错误。若要恢复完整 JSON，建议同步调大远端上下文，例如：

```bash
REMOTE_VLLM_MAX_MODEL_LEN=16384
```

但这可能挤压 OCR / ASR / HY-MT 常驻空间，需要重新做 GPU 稳定性验证。
