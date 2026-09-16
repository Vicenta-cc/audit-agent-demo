# 抖音 M3 候选提交门禁记录（2026-09-16）

## 结论

候选工作树已完成范围审核、临时/实验项排除和提交前门禁，可以形成候选提交与候选标签。该结论只表示代码候选已经铆钉；不表示 3199/8199 主界面端到端验收完成，也不表示可以晋升 3198/8198 正式环境。

最终产品入口固定为 `/investigation`。`/tasks` 仅保留诊断用途，`/tasks/new` 不作为验收入口。本轮已撤回早期误加在 `/tasks/new` 的逐任务参数表单；统一设置只从 `/investigation` 左侧“业务入口 → 采集与分析设置”进入。两个已验证 profile/seed 未修改。

## 代码范围审核

- 分支：`codex/douyin-ops-controls-20260916`
- 审核起点：`a925969d78e570bbd99413dfa2e2b0d5283811da`
- 正式 3198/8198、正式数据库、正式控制脚本与正式工作树均未修改。
- 8027 只作为历史成功爬虫代码与 profile 行为参照，不是当前运行或验收对象。
- 当前候选保留公共账号轮换、结构化失败/恢复、Run/Job 检查点、统一设置冻结、前端独立控制和 profile 复用能力。
- 当前源码、旧 8027 成功验收副本和 8199 隔离副本的 `media_platform/douyin/client.py` SHA-256 均为 `8e857005dded68d3fffbce497a931e013d20f8099d2b9371eb7532355f040ccb`；源码仓库提交为 `6d2c85bcb02fd7dd1e30822e0cb2103bb9b42f39`，未检出页面 DOM 搜索实现。
- 未纳入日志、PID、SQLite、二维码、浏览器 profile 数据或临时实验产物。
- `git diff --check` 通过。

## 候选门禁

执行：

```bash
/bin/sh scripts/douyin-ops-gate.sh
```

结果：

- 后端定向门禁：`354 passed, 5 warnings, 23 subtests passed`
- TypeScript：通过
- Vite production build：通过
- 构建产物：CSS `366.33 kB`，JS `973.54 kB`
- 非阻断提示：FastAPI/Starlette 既有弃用提示；Vite 大包提示

本轮修正了确认卡的一个真实交互缺口：搜索词保存成功后，以规范化后的已保存词组解除“确认并开始调查”锁定；存在未保存改动时仍禁止启动。针对该交互的 Playwright 用例已通过。

## 全量后端回归归因

执行全量 `pytest -q`：

```text
1274 passed, 30 skipped, 8 failed, 11 errors
```

未通过项均已单独复核：

- 11 个 setup error：缺少两份外部历史冻结报告数据库；本轮没有伪造或修改正式归档绕过。
- 2 个 `test_pass_investigation.py` 失败：既有 Mock 缺少真实 `db_path`，以及评论覆盖率断言没有包含当前字段；该测试文件本轮未修改。
- 6 个调度/共享执行失败：默认 `external/MediaCrawler` 不存在。显式设置隔离 `MEDIACRAWLER_DIR` 后，本轮相关用例重跑 `7 passed`。

因此候选定向门禁通过，但不把全量仓库宣称为全绿。

## 前端回归归因

使用本机 Google Chrome 作为 Playwright Chromium 执行完整套件，排除浏览器二进制缺失后结果为：

```text
82 passed, 2 skipped, 5 failed
```

其中 1 个由本轮新增的保存保护触发，已修正并在定向重跑中通过。剩余 4 个失败对应的测试与相关生产实现均未在本候选中修改：

- `investigationPresentation.spec.ts` 仍断言旧按钮文案“去配置研判方案”，当前既有实现为“前往配置”。
- `mixedProposalPresentation.spec.ts` 两项仍查找旧的纯文本 CSS 容器；当前既有权威 proposal 展示使用 `GeneratedRulesMessage`。
- `structuredReport.spec.ts` 仍按源码字面量断言 `<small>风险帖子</small>`，当前既有实现由数组映射生成该标签。

`mixedProposalPresentation.spec.ts` 定向重跑结果为 `10 passed, 2 failed`；保存后确认按钮用例已经恢复通过，剩余两项就是上述既有展示断言。

## 运行边界

- 正式环境身份继续由原冻结控制器保护；其脚本按 `/bin/bash scripts/formal-runtime.sh status` 调用，不修改可执行位。
- 8199 已有 `/api/acceptance-runtime`，可识别隔离环境；候选控制器不以新增 `/api/m3-runtime` 为目标。
- 候选提交和标签之后，另建 3199/8199 独立控制器，固定候选提交、依赖路径、端口、数据库、profile 根目录、PID 与日志。
- 控制器属于运行固化，不拆服务、不修改 profile 机制，也不按端口误杀其他环境。
- 最终验收只从 3199 `/investigation` 进行，并使用既有已验证账号/profile 完成独立控制、重启恢复、完整审核和报告闭环。
