# 审核健壮性代码审查与交付

审查日期：2026-09-10。来源提交 `0704932fe9614fb74969491d1a88a369ec5e4983`，交付起点 `ec82cfb`。只引入代码健壮性和状态展示改动；不引入 K2 规则包、不修改既有 K 规则、固定 Prompt 文件或其思考参数，也不复制开发机数据库和守护脚本。

## 审查发现与处理

1. **P1：评论短编号指令泄漏到其他阶段。** 来源提交在非 V2 融合、视频分段审核和 ASR 翻译提示词里追加了 C01/comment_id 指令；实际这些阶段分别使用 evidence_id、frame/chunk ID、index。相互矛盾的输出要求可能再次导致无效引用和翻译对齐失败。已仅在评论请求保留短编号指令，新增测试确认其他阶段保留自己的 ID 协议。
2. **P1：继续分析缺少模型健康校验。** 此缺口原已存在，但本次新增清除上一帖错误标志后，更需要与首次审核路径一致。恢复路径在写入前未检查 provider_failure，可能保存带失败标志的兜底结果。已在持久化之前补齐检查，测试确认它只记录失败，不写入 pass。
3. **P2：部分失败后报告状态误导。** worker 将部分失败的处理标记为终态 AUDIT_COMPLETED，不再自动生成报告；投影仍返回 pending，页面标题显示审核完成。已显示“部分帖子审核失败”和“未生成完整报告”，避免用户等待不会自动发生的报告生成。

上述问题已在交付仓库修复。原开发目录没有修改或重启，审查不代表来源提交可原样发布。

## 保留并验证的行为

- 单帖审核异常：记录失败状态及 `outputs/<job>/post_failures/` 诊断，继续其他帖子；不设 crawl_stop_requested/stop_all_requested。
- 明确审核接口鉴权/配置故障、连续三帖接口失败：停止审核，采集继续按任务范围执行。媒体下载 HTTP 401 不按审核服务整体宕机处理。
- 评论请求使用局部 C01 等编号，返回顺序可变；未知、重复、互相冲突的编号不接受；仅对缺失项补偿一次，程序映射回平台真实 ID。诊断保存映射和模型返回。
- 成功帖与失败帖分别保留，不将审核失败当作 pass，也不把部分审核包装成全量成功报告。未实现部分失败报告自动发布、自动重试整轮或无限采集。
- 模型参数、账号校验、确认范围和报告完成校验仍保留。首次启动配置检查与运行中服务失效不同；服务失效不连带停止已在正常运行的采集。

## 验证

以下回归：230 项通过，42 项子测试通过；无真实抓取或付费模型调用。

```bash
.venv/bin/python -m pytest \
  tests/test_comment_alias_and_failure_isolation.py \
  tests/test_authoritative_m3_provider_closure.py \
  tests/test_investigation_creation_m3.py \
  tests/test_review_pipeline.py \
  tests/test_ruleset_foundation_gambling.py \
  tests/test_ethnic_k_profile.py tests/test_ethnic_delivery.py \
  tests/test_fusion_comment_boundary.py tests/test_qwen_timeout_retry.py -q
pnpm --dir Audit_assistant build
```

失败隔离测试通过事件同步，明确让审核失败/停止发生在剩余采集之前，再验证后续采集完成；不是先采完再检查错误标志。覆盖中间帖失败、末帖失败、服务连续超时、审核鉴权和单帖媒体鉴权。评论映射测试使用仍交付的 K1 编译配置，不依赖 K2。

前端 TypeScript 与生产构建通过。本轮没有验证真实服务故障下持续一天的采集，不承诺进程崩溃、断网或磁盘故障后的无损恢复。
