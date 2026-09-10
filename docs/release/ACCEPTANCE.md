# 独立演示交付验收

验证日期：2026-09-10。应用来源基线：`f6d19f8e82a9cf0944c7b11ffbcbfe8c15cc5bd8`。本仓库在此基础上补充演示数据、初始化、依赖固定、启动与前端部署入口，未重新调整民族 K 审核标准。

## 已完成

| 检查 | 结果 |
| --- | --- |
| 新建主项目 Python 3.12.10 环境 | 安装成功，未复用作者原 `.venv` |
| 新建 MediaCrawler 独立环境 | 安装成功，CLI 与 Chromium 可用 |
| Hermes 官方固定源码 | 0.20.4；核心文件与原可用环境比对一致；editable 安装成功 |
| 前端干净安装与构建 | pnpm frozen lockfile 安装、TypeScript 与 Vite build 通过 |
| 新目录初始化 A/B | 通过；两报告所有快照、证据和账号投影校验通过 |
| A/B 真实模型问答 | 8/8 完成，grounding 校验全部通过；覆盖代表帖子、证据、账号活动与跨任务查询 |
| 浏览器 A/B 页面 | 可见真实报告卡片；A 真实问答与 SSE 展示正常 |
| 自然语言生成新任务 | Hermes/Qwen 生成临时规则提案，用户采用后形成草稿；前端生成预览并确认 |
| 实际抖音单条采集 | 1 条入库、1 条审核成功、0 条审核失败；未使用假采集器 |
| 新报告 | 判定 pass，报告发布成功；真实模型问答能解释依据及不能外推的范围 |
| 整套停止与重启 | A/B、新报告版本、任务对话和报告问答均保留 |
| 重复执行安装器 | 配置、测试账号、既有任务和报告保留；不清空数据 |
| 回归 | 140 项测试、23 项子测试通过 |
| 发布数据检查 | A/B 档案仅 15 张必要报告表；不含采集账号、队列或登录态；索引文件未命中已知本机密钥 |

单条实测搜索词为“猫咪打哈欠”，使用与原正式采集不同的账号，独立 API/前端/worker/数据。任务约 106 秒完成，含首次本地 Whisper 下载和审核。该帖实际返回 **0 条评论**，新报告问答如实说明评论覆盖为空，不将其描述为“评论均安全”。这次没有用另一个假任务代替完整运行。

本轮发现并解决的交付缺口：Hermes 普通 wheel 安装被上游禁止；A/B 原依赖外部本机目录；账号证据语料缺失；Vite build 原强制要求开发代理；临时规则的旧展示文案错误地声称不能执行；启动加载期间短暂展示了 mock 会话。现在使用固定源码 editable 安装、裁剪 seed、可迁移数据目录、构建产物与本地代理，并修正两处展示问题。

## 验证的实际边界

- 实测主机为 macOS；提供的 Linux 路径与命令尚未在 Linux 主机实跑。不支持直接照搬到原生 Windows；可另行验证 WSL2。
- 平台登录验收复用了独立的已有授权账号，没有为发布包保存该账号，也没有在新机器扫码登录。接收者仍须自行完成官方登录/验证码流程。
- 当前实测单帖无评论；已有评论审核、融合边界与历史完整证据查询另由回归测试和 A/B 真实问答覆盖。不能把这一次运行当作评论数量、吞吐量或审核准确率的全面评估。
- 这次没有验收 24 小时批量稳定性或双后端共前台，默认不创建相应定时任务。
- A/B 不分发原始视频文件；问答使用冻结文本与证据，外部媒体链接可能过期。
- 历史研究测试中仍有依赖未分发私有 fixtures 的用例；下面列出的交付回归无需这些文件。

## 复核命令

先停止本仓库演示运行再跑隔离性测试，以免正在写日志的真实 worker 触发“默认 data 目录被改变”的检查。不要为了跑这些测试停止其他项目的任务。

```bash
uv pip install --python .venv/bin/python pytest httpx
.venv/bin/python -m pytest \
  tests/test_demo_delivery.py \
  tests/test_historical_report_demo.py \
  tests/test_investigation_creation_m3.py \
  tests/test_investigation_creation_conversation.py \
  tests/test_ethnic_k_profile.py \
  tests/test_qwen_timeout_retry.py \
  tests/test_fusion_comment_boundary.py -q
```

结构化摘要见 [acceptance-results.json](acceptance-results.json)。真实问答涉及模型费用；运行结果会因模型与外部平台变化，不能把本次成功视为永久服务保证。
