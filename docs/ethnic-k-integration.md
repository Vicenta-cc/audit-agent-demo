# K 试运行配置接入

K 的模板与规则已纳入 `backend/rulesets/bundles/ethnic_k_v1`。专用 RuleSet ID 为
`ruleset.ethnic-content-review.k-trial.v1`。选择这个发布版本时，编译器校验固定规则内容与 bundle 文件哈希，使用保存的完整 Prompt profile，并把评论/汇总推理参数纳入执行配置哈希。修改专用规则内容会失败，不会悄悄与旧模板混用；其他 RuleSet 和临时生成规则继续使用普通编译。

评论：qwen3.7-plus、thinking=true、max_tokens=6000、timeout=120。
汇总：qwen3.7-plus、thinking=true、max_tokens=3000、timeout=120。
Qwen 文本调用 temperature=0。请求超时等 1 秒补试一次；再超时上层不叠加。Pipeline 从任务冻结快照读取配置，旧任务没有这些字段时沿用原默认值。

原始 K 的 Prompt 文本完整保留。历史名称含“未发布”也保留，以免为改显示名改变实验输入；运行登记状态与 `trial_profile.status=experimental_trial` 说明这是已登记的试运行候选。原实验 Prompt ID 保存于 `trial_profile.source_prompt_version`；接入版本生成新的 Prompt ID，涵盖推理设置和真实编译器版本，不冒充旧实验身份。

该接入不修复 K 已知判定波动。1022 条回放、42 条人工预期对照中 41 条相符；重复测试中“基因互补”仍出现 pass/medium 波动，婚恋建议还有误报。不得声明稳定版或据此承诺准确率。图像/视频重新推理未在本次接入验证。

本次仅接入、登记版本、加载正式 API。未启动新任务，旧采集与审核任务保持暂停，production-worker 不加载。后续启动须新建正式会话、确认上述专用 RuleSet 版本、登记正式路由并验证实际日志和首批报告。仍使用每词20帖、每帖最多1000一级评论、不抓次级评论。用户的 verify 即停止和整轮故障修复最多重启一次约束继续适用。

验证覆盖完整模板保留、配置哈希约束、独立版本发布/编译、普通规则不受影响、实际 Pipeline 到 HTTP 参数和超时不叠加，以及 M3 与网关回归。HTTP 测试使用模拟响应；没有为接入额外发起真实采集或付费模型调用。
