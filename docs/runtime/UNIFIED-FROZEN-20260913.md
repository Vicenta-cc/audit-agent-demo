# 统一代码冻结基线 · 2026-09-13

本基线合并正式管理功能与 3298 QA 修复。它是新的代码冻结点；本次没有切换 3198/8198 的运行配置、数据库或服务，也没有恢复或启动 380 条审核任务。

## 固定位置

- 分支：`codex/unified-frozen-20260913`
- 冻结标签：`unified-baseline-20260913`
- 工作树：`/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-unified-frozen-20260913`
- 合并父提交：`876d58bfe592de7d898183494344b76fdb55ef28` 与 `7215bf94cff68af57225814e892b2f0764b0f16d`
- 共同祖先：`f3ea3b08aa36e4d7c284ad0e50b2164f8fe61dd4`
- 原标签 `formal-baseline-20260911` 保持原指向。

读取冻结提交与当前状态：

```bash
git -C /Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-unified-frozen-20260913 rev-parse 'unified-baseline-20260913^{commit}'
git -C /Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-unified-frozen-20260913 status --short
```

## 合并范围

`876d58b` 一支提供会话及关联报告删除、规则/词库编辑持久化、K2 恢复和管理按钮布局；`7215bf9` 一支提供昵称定位、旧引用与失败对话恢复、报告评论统计与任务配置问答、词库说明、M3 对话约束、评论/视频审核规则映射、审核详情链接和答案展示改进。

两支在四个词库说明文件中产生冲突。本次保留说明字段的完整编辑、保存和查询，并采用 QA 版本的空说明兼容序列化、字段说明与关键字参数。清除自动合并产生的重复 `description` 字典键。QA 提示词与 `7215bf9` 字节一致，仅将一项测试中的两个过期提示词指纹同步到该已提交版本。

明确排除正式工作树的全部 12 个未提交文件，包括后来 380 条导入、流水线/入口、启动脚本和代理调整。未复制这些工作树文件、未 stash、未 reset。排除清单及差异指纹见 `docs/evidence/unified-frozen-20260913/excluded-changes.json`。

## 验证结果与边界

- 18 个相关测试模块：333 passed，31 subtests passed；覆盖词库说明与前后台编辑、会话删除、历史报告恢复、昵称定位、引用恢复、评论统计、任务配置、M3 会话、评论/规则映射和运行快照边界。
- TypeScript `tsc --noEmit` 通过；Vite 正式构建通过。
- `git diff --check`、启动入口 Shell 语法检查、前端服务 Node 语法检查通过。
- pytest 有 5 项依赖弃用警告；Vite 有大文件体积提示，均不阻塞构建。
- 历史报告测试通过环境变量指定正式 runtime 中的 A/B 冻结归档作为输入，测试写入临时数据库；没有删除或修改正式 A/B/C 报告及任务数据。
- 本次未执行真实 Qwen 多轮问答、浏览器全流程验收或 380 条压力验收。此前 QA 中保留的问题不因代码合并而自动算通过。

完整日志在 `docs/evidence/unified-frozen-20260913/pytest.txt` 和 `frontend-build.txt`。

本机验证使用 integration 项目的现有 Python 虚拟环境，以及 3298 的前端依赖（锁文件与两支一致，`node_modules` 为本机忽略链接）。这些依赖没有被打包进 Git 冻结标签。

## 与正式运行环境的关系

正式运行数据目录仍为 `/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-formal-20260911`。本次没有修改其中配置、凭据、端口和数据库。新代码工作树不等于该 runtime 已经切换到新版本。

实际部署时须将 runtime 的 `worktree`、服务启动路径与本目录一致，再通过既有 `m3_environment.py` 构建及环境检查后启动；不能只更换一个前端路径，也不能直接复用旧构建指纹。此说明记录代码冻结，不把启动脚本语法检查视作完整运行恢复验收。
