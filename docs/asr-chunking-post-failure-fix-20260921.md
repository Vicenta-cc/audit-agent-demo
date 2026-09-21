# 长音频分段与单帖失败收尾修复 · 2026-09-21

## 基线与范围

应用仓库为 `xhs-audit-agent-multi-user-v1-20260920`，分支 `codex/multi-user-v1-20260920`，开始时工作树干净，HEAD 为 `5873ea7570397f4b6f0c07dcec214e8541d7b2e0`，前一提交为 `095f22088f8b5fe37b0ef4683e62baf797a1526d`。本次修复保留为该 HEAD 之上的未提交改动。

MediaCrawler 保持 `efafe3186400b1020955c6acfdc201235b84a2d8`，无改动。没有修改旧 demo 仓库、云端数据、Profile、密钥或历史任务；没有启动或重启服务。冻结的 3198/8198、3199/8199 监听进程未操作。未使用 4175 演示证明真实接口可用。

## 代码确认的原因

- Dolphin 原实现对完整音频调用一次 transcribe。远程 ASR 对 500 响应重复提交相同文件，未区分显存不足。
- 历史提交 `0704932` 引入单帖失败计数与自动停止审核。`_record_subject_failure` 达到三次或判断服务不可用时设置 `analysis_stop_requested`，最终进入 `analysis_stopped`；worker 将该状态视为中断而跳过报告生成。
- 本次未读取线上日志。用户提供的三条笔记运行记录是故障线索，不视为本轮独立验证过的线上证据。

## 最终行为

- 真实 Coder 测试证明 300 秒段会 OOM，因此默认改为每段最多 **120 秒**，相邻段重叠 **5 秒**。对应配置为 `ASR_CHUNK_SECONDS=120`、`ASR_CHUNK_OVERLAP_SECONDS=5`；上限仍可显式调到 300 秒，但未经对应 GPU 实测不应这样配置。
- 处理应用抽取出的 PCM WAV，按采样位置切片并串行请求。请求端和本地 Dolphin 执行端共用分段实现；请求端可对旧推理服务发送分段音频。
- 按重叠区中点确定词的归属，恢复全局时间戳。Dolphin 保留词级时间戳；旧服务只在 raw 中返回词时间戳时也可恢复。无词时间戳时使用段时间与边界精确文本匹配，并记录能力限制提示，不做模糊改写。
- 明确 OOM 时只将失败段减半，最小至 15 秒；同一音频窗口不原样重试。最小分段仍失败则整帖转写失败，不提交部分转写作为完整结果。新服务通过结构化 422 告知客户端已耗尽分段，避免两端重复拆分。
- 单帖失败不再触发自动停审或累计停止阈值，继续处理已选择的帖子。用户暂停/结束、真正的审核线程不可恢复异常仍使用原有控制流程。
- 新增公开错误分类 `asr_gpu_out_of_memory`、`audit_content_blocked`，在任务日志与报告失败原因中区分显存不足和供应商内容拦截；内容拦截不表述为已经判定违规。
- 已有报告流程现在能在这些单帖失败后正常收尾：有效审核结果可生成报告，失败帖子排除在风险比例分母之外；全部失败进入明确的审核完成终态。沿用原有执行停止后释放位置与额度规则。
- 未修改前端布局、权限校验、任务所有者传递或额度账本逻辑。

## 本次验证

应用 Python：`/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-investigation-report-integration/.venv/bin/python`。

最终单次定向回归：**346 passed，23 subtests passed**，19.55 秒；5 个已有弃用提示。测试通过现有 conftest 使用临时存储，未使用真实业务数据库或外部采集/模型请求。

```sh
python -m pytest -q \
  tests/test_asr_chunks.py \
  tests/test_remote_inference.py \
  tests/test_comment_alias_and_failure_isolation.py \
  tests/test_authoritative_m3_provider_closure.py \
  tests/test_task_admission.py \
  tests/test_investigation_creation_m3.py \
  tests/test_review_pipeline.py \
  tests/test_unified_report.py \
  tests/test_job_phase_state.py \
  tests/test_qwen_timeout_retry.py \
  tests/test_crawl_resume_control.py \
  tests/test_analyze_limit_ingestion.py \
  tests/test_current_report_commenter_index.py
```

覆盖 740 秒/1162 秒模拟音频、5 秒重叠、词去重与全局时间轴、旧服务 raw 词时间戳、OOM 自动减半/终止/临时文件清理、请求端与推理接口错误契约、2 成功后 3 失败、3 失败后继续成功、全部失败、报告失败原因与分母、额度/位置释放、暂停继续以及前 5 评论账号回归。`git diff --check` 通过。

## 验证边界

已对现有 Coder workspace 的真实 Dolphin 做只读探针：服务 `127.0.0.1:19001`，health 返回 `dolphin/small/ug/cuda`；将本地现有 563.85 秒真实音频的首个 300 秒段发送到旧服务时，复现 HTTP 500 与 `torch.cuda.OutOfMemoryError`，日志为尝试额外申请 75.50 GiB、当时仅 45.84 GiB 可用。该旧服务仍是 2026-08-31 的部署副本，没有结构化 OOM 响应或服务端分段。由于本机没有配置远端 API key，候选客户端完整鉴权探针被服务以 HTTP 401 拒绝；没有绕过鉴权，也没有读取或输出密钥。上述真实探针未修改或重启 Coder workspace。

随后通过用户提供的 SSH host 进入同一 workspace，从运行进程取得鉴权值但不回显，用同一份音频的前 180 秒做直接真实请求：HTTP 200，约 5.13 秒，805 字符、29 个分段，无 OOM。该样本证明 3 分钟在当时空闲 GPU 状态可用；它不证明所有 3 分钟音频、并发或显存状态都安全。

因此仍不能宣称 5 分钟分段在更新后的远端服务上已成功完成，也未证明实际语音边界识别质量；需先按本文边界更新远端 Coder 服务，再用同一音频和腾讯服务器两条原始样本复测。无词时间戳时的文本边界去重属于保守降级，不保证语义层面完全消除重复。前端未修改，本次未重跑浏览器验收。未推送、未部署，历史已中断任务未自动恢复或重生成报告。

## 后续核对：Coder 更新边界与 Qwen JSON

仓库的 `docs/backend_startup_and_remote_asr.md` 记载 Coder workspace 承载 Dolphin，通过端口转发调用；这不是对远端当前进程、目录或版本的独立核实。

完整发布应包含应用后端与远端 Dolphin 推理服务。只更新应用，请求端也能先发送分段音频；远端更新补齐服务端保护、词时间戳和结构化 OOM 错误。更新前仍需只读核对实际 Coder 入口和工作树，不能直接覆盖远端整个仓库。此修复不要求更新 Qwen 模型或修改其 JSON schema。

本轮额外增加两项跨模块测试，将实际分段合并结果通过现有转写合同校验、Qwen JSON 解析、翻译索引回填、融合转写输入格式化和原始 JSON 落盘。分别覆盖完整 JSON 和首次截断后按原索引拆批恢复；全局时间戳保持，分段元数据与词数组不进入融合转写提示。

执行 `test_asr_chunks.py`、`test_review_pipeline.py`、`test_fusion_comment_boundary.py`、`test_qwen_timeout_retry.py`、`test_authoritative_m3_provider_closure.py`、`test_qwen_report_client.py`、`test_report_risk_inputs.py`：**146 passed**，2.98 秒，1 个已有弃用提示。该批包含此前已执行用例，不与 346 简单相加。未改 Qwen 客户端、提示词或融合合同代码；未调用真实 Qwen 或远端 GPU。

发布前仍需隔离环境的真实长音频链路验收：Dolphin 转写 → Qwen 翻译/融合 JSON → 报告，并核对边界上下文、显存、输出截断、证据引用、部分成功和全部失败收尾。
