# V2 第一阶段基线与范围

## 固定基线

- 应用仓库：`2134c7b53c572b9981a0cbd9a2a281ed7ca6eb3a`
- 应用 worktree：`xhs-audit-agent-v2-integration-20260914`
- MediaCrawler 正式运行基线：`d9aa0c10acdd6de8701303afe34a1c0f9cf2d42e`
- MediaCrawler 第一阶段 worktree：`MediaCrawler-douyin-v2-pagination-dedupe-20260914`
- MediaCrawler 阶段起始提交包含 CDN 修复：`60ccef1f181621ccc825dffdc1f11ce7df9a98d8`

正式应用、正式 runtime、正式数据库、登录态密钥、端口和 endpoint 未修改。

## 本阶段范围

本阶段只处理采集层基础问题：

1. 固定搜索页请求数量与 offset 步长，避免第一页负 offset 和页间重叠；
2. 帖子在同一搜索任务内按 `aweme_id` 去重；
3. 评论及二级评论在采集回调前按原始 `cid` 去重；
4. 评论 cursor 不前进时有界退出；
5. 合入已验证的 CDN 请求头、备用播放地址和单次地址刷新处理；
6. 为上述行为补确定性测试和真实小样本证据。

本阶段不实现共享采集结果、任务断点、跨任务复用、账号换号状态机、统一跨任务调度或前端改动。

## 后续集成顺序

下一阶段在本 worktree 的提交通过评审后，才把 MediaCrawler 执行入口接入应用侧。任何正式部署都需要单独的真实回归和发布步骤。
