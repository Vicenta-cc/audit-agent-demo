# 图片与视频音频抽取诊断及重测（2026-09-23）

## 修复范围

图片审核原来只在模型调用返回后的合同校验中保存失败响应，调用或解析时抛出的异常可能绕过保存。

现在每次图片审核及纠正重试均记录独立 attempt：

- `assets/<note_id>/image_attempts/` 保存请求、返回的原始响应、解析结果、短 ID 映射、冻结规则版本和最终状态。校验前先写响应，成功后保存解码结果。
- `assets/<note_id>/image_failures/` 额外保存失败阶段、具体错误、完整异常链及是否会纠正重试。
- 帖子级 `post_failures` 保存诊断路径；即使外层包装了合同异常，仍分类为 `fusion_contract_invalid`。
- 使用权限 0600 的临时文件及原子替换；保存失败会明确记录，且不会掩盖原来的审核异常。
- HTTP 非成功响应及非 JSON 响应也保留响应体，不记录鉴权请求头。
- 保持合同校验与短 ID 精确解码。合同失败最多纠正重试一次，普通接口错误不触发合同纠正重试。

原始图片、Prompt 和模型响应只保存在运行数据目录，不提交到 Git。

## 回归验证

图片改动初次验证 202 项测试通过；加上视频取证后的合并验证共 224 项测试通过（另有 19 个 subtests），覆盖：

- 校验前落盘及成功结果留存。
- 模型调用内部抛出的包装合同异常。
- 字段错误、纠正重试耗尽及帖子失败记录中的诊断路径。
- 普通接口故障、非 JSON/HTTP 错误响应体保存。
- 磁盘写入失败时保留原始审核异常。
- 既有图片短 ID、评论、融合、Provider、报告客户端回归。

## 真实模型重测

来源任务：`m3-0b94dde03051b900c3b2`。
冻结审核配置：`audit-config-revision:f313ab200aa68e9377270c34bd8120ff`。
使用已采集图片及冻结规则/Prompt 快照，审核模型为 DashScope `qwen3.6-flash`。

| 原失败帖子 | 本次图片数 | 图片阶段结果 |
| --- | ---: | --- |
| 7386218721704021285 | 1 | 全部首次通过 |
| 7683949960911401457 | 2 | 全部首次通过 |
| 7305296973492423946 | 4 | 全部首次通过 |
| 7686350563685387110 | 1 | 全部首次通过 |
| 7680564051084100837 | 2 | 全部首次通过 |

按原环境 `MAX_IMAGES_PER_NOTE=4` 取样，共 10 张；该轮没有合同错误或纠正重试。
10 份原始响应、10 份解析结果均完整保存，供应商返回模型均为 `qwen3.6-flash`，结束原因为 `stop`。

本地收据目录：运行环境下的 `image-replay-vv6J86/`。
其中 `manifest.json` 记录图片审核源码哈希、Python、模型与规则快照；逐帖结果记录图片 SHA-256；`summary.json` 记录汇总。

这次只重测图片审核，没有重跑 OCR、评论、视频、融合或报告，也没有更改原任务的失败状态。
未复现旧错误不能证明旧错误一定来自哪个字段，更不能视为整轮 30 帖验收通过；旧响应缺失的事实仍然存在。

## 后续重放

使用应用虚拟环境中的 Python，并加载审核阶段的环境变量后执行：

```bash
python scripts/replay_image_audit.py \
  --database /absolute/path/to/audit_index.sqlite3 \
  --source-output /absolute/path/to/outputs/SOURCE_JOB \
  --source-job SOURCE_JOB \
  --note-ids NOTE_ID_1 NOTE_ID_2 \
  --output /absolute/path/to/empty-replay-directory
```

脚本只读来源数据库，使用独立输出目录，拒绝未配置的真实视觉 Provider。
应使用审核阶段的 `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL` / `QWEN_IMAGE_AUDIT_MODEL`（或已配置的远程视觉服务），不能将规则和关键词生成阶段的 DMX Key 混入审核环境。
图片阶段不调用 Dolphin；视频转写仍需单独配置 Dolphin 和 ffmpeg，本次重测不验证视频环境。

## 视频音频抽取取证与对照

音频抽取现在保存 `audio_<index>/audio_extraction.json`，包括：

- ffmpeg 配置路径、解析后的路径、可执行权限、完整命令。
- 输入路径、文件大小与前 32 字节文件头；输出路径和成功时的文件大小。
- 退出码、未经裁剪的 stdout/stderr、异常类型及 traceback。
- 失败阶段、状态、`last_extract_error`、耗时。

诊断使用 0600 文件及原子替换。即使输出目录创建失败，诊断也仍保留在处理器内存中，随后写入帖子的失败收据。
帖子级错误现在分类为 `audio_extraction_failed`，并保存上述诊断及其路径，避免只剩 `post_audit_failed`。
ASR 调用失败继续按原路径处理，不归到 ffmpeg 抽取失败。

从原任务失败收据定位到全部 13 条视频，使用已经采集的 `video.mp4` 重测：

| 条件 | 成功抽取 | 失败 | 诊断 |
| --- | ---: | ---: | --- |
| 当前完整运行环境，指定 ffmpeg | 13 | 0 | 全部退出码 0 |
| 干净环境，PATH 为 `/usr/bin:/bin`，FFMPEG_PATH 为空 | 0 | 13 | `ffmpeg not found; install ffmpeg or set FFMPEG_PATH` |
| 同一干净环境，仅设置 FFMPEG_PATH 为本机实际二进制 | 13 | 0 | 全部退出码 0 |

三个本地收据目录分别为运行环境下：

- `audio-replay-i892kV/`
- `audio-missing-ffmpeg-n9Ku1P/`
- `audio-clean-ffmpeg-g3g6NP/`

这台机器的 ffmpeg 位于自定义目录，`/opt/homebrew/bin/ffmpeg`、`/usr/local/bin/ffmpeg`、`/usr/bin/ffmpeg` 均不存在。
上述对照证明漏配 ffmpeg 路径可以稳定造成 13 条视频全部在 ASR 之前失败。
旧进程的真实环境及 stderr 没有留存，因此仍不能将这个受控复现等同于原故障的确定根因。
这次未调用 Dolphin、未重跑视频审核或报告；不会更改原任务状态。

重放脚本（同样要求独立、空的输出目录）：

```bash
python scripts/replay_audio_extraction.py \
  --source-output /absolute/path/to/outputs/SOURCE_JOB \
  --output /absolute/path/to/empty-replay-directory
```

所有修改与收据留在本地，本次未上传 GitHub。
