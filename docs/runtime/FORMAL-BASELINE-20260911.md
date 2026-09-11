# 统一正式基线 2026-09-11

后续更新：3198 已接入真实会话及关联报告删除，见 [会话删除说明](SESSION-DELETION-20260911.md)；审核规则、黑话库真实增删改与 K2 补入见 [资源库说明](RESOURCE-LIBRARY-20260911.md)。当前开发分支为 `codex/resource-library-crud-20260911`，下述冻结标签和历史验收记录保留不动。

本文件替代旧候选交接文档中的当前状态描述。最终验收及冻结信息见本文件末尾；历史文档保留供追溯。

## 代码与入口

- 分支：`codex/formal-baseline-20260911`。
- 代码：`/Users/ext.wanghongtao6/Documents/Codex/projects/xhs-audit-agent-formal-baseline-20260911`。
- 前端：<http://127.0.0.1:3198/investigation>；API：8198。
- 外置运行目录：`/Users/ext.wanghongtao6/Documents/Codex/runtime/xhs-audit-agent-formal-20260911`。
- 环境 ID：`xhs-audit-formal-baseline-20260911`。
- 独立系统服务：`com.xhs-audit.formal-baseline-20260911`，管理 API、静态前端及 M3 worker，登录后自动启动。

原 formal-entry 的 3188/8188 和 M3 实验的 3178/8178 保留作对照；本环境使用自己的数据库、媒体副本和采集代码。当前默认单帖、最多 3 条评论，不代表完成大批量负载验收。

## 已整合能力

以 formal-entry 的 `1425652` 加全部当前已跟踪/未跟踪修改为底座，保存为 `ded2826`，依次迁入 M3 原提交 `a8904ef`、`8861c2a`、`57a0d1b`，对应新提交 `e9fc2b7`、`034b7d6`、`ed7be80`。没有覆盖原工作树的未提交修改。

- A/B/C 报告、已存正文、Finding/Evidence、账号投影和账号活动。
- C 发布账号正文前 5、独立发布账号抽屉、排序/搜索/分页；评论账号原有分组。
- 正文与附录的完整审核详情携带 `report_version`、`post_ref`，读取对应冻结审核结果；不依赖当前任务库恰好存在同号结果。
- 媒体按报告帖子授权读取，兼容原绝对路径的已归档副本及视频 Range 请求。
- M3 规则/词库会话读改存、规则重展示后的采用绑定、临时词库引用校验、评论 `lib/t/rule_id` 字段修复和统一 Markdown 表格渲染。

账号功能仅关联已有稳定账号标识与当前报告保存的帖子、评论、Finding/Evidence；不增加个人敏感属性或现实身份识别。

## 数据和模型

当前环境从 3188 的固定运行目录复制数据。SQLite 用在线备份 API，媒体在 macOS 使用 APFS 写时复制；两份文件独立可写，不是硬链接。5 个数据库通过 integrity_check，4 份报告数据库按 SHA-256 校验一致，C manifest 指向新目录的归档。复制时源任务与会话无正在执行的工作，不迁入待执行队列。

C 当前版本保持 `report-version:ac44c88f638c45fbbf651afd784ba193`，报告 219 帖、146 个发布账号；本次整合不重新审核 A/B/C。

模型配置取自 M3 已验证环境的显式 `--model-runtime`：Qwen 3.7 Plus，付费 token-plan endpoint。3188 原模型配置在真实验收中返回 403 免费额度耗尽，失败记录保留在新环境。没有充值或修改模型供应商账户设置。密钥仅保存在权限受限的运行配置，不提交到 Git。

## 日常启动

在代码目录运行：

```bash
scripts/formal-runtime.sh status
scripts/formal-runtime.sh check
scripts/formal-runtime.sh restart
```

`status` 核对前后端身份、构建指纹、A/B/C 及 worker 心跳。`restart` 仅重启本环境的系统服务。需要停止本环境时运行 `scripts/formal-runtime.sh stop`；停止后用 `scripts/formal-runtime.sh install-service` 重新加载服务。

配置中的 Python 路径是已安装 Hermes 0.20.4 的解释器；Node 路径是已验证 Node 24.19.0。前端依赖由 `Audit_assistant/pnpm-lock.yaml` 固定，使用 `pnpm install --frozen-lockfile` 安装。本 worktree 已独立安装 node_modules。采集器源码保存在 runtime/crawler，必须包含 cache Python 源码包；其 `.venv` 指向本机公共依赖环境，不包含另一环境的任务库。

## 在新的目录复现

先检出本基线并安装固定依赖。使用配置中的 Python 执行：

```bash
python scripts/provision_formal_runtime.py \
  --source /path/to/verified-runtime-or-backup \
  --runtime /path/to/new-runtime \
  --model-runtime /path/to/verified-model-runtime \
  --environment-id xhs-audit-formal-recovery \
  --frontend-port 3298 --api-port 8298
export XHS_MANAGED_RUNTIME=/path/to/new-runtime
scripts/formal-runtime.sh build
scripts/formal-runtime.sh check
scripts/formal-runtime.sh install-service
```

源数据必须没有正在执行的任务/会话。程序拒绝覆盖已有运行目录；不得对运行中的 SQLite 文件直接使用文件复制替代在线备份。恢复到新目录和空闲端口，验收后再选择入口；不要直接覆盖 3188 或 3178。

数据结构通过现有 Store 初始化逻辑维护，本次不合并另一实验库的任务或资源记录。`investigation_run_execution_holds` 保留在代码中；完整结构及数据备份指纹写入运行目录的 receipts。

## 备份与恢复

空闲时可使用同一 `provision_formal_runtime.py` 将当前运行目录复制到一个新的备份目录；为备份配置一个不同的环境 ID 和空闲端口，完成复制即可，不启动服务。`--model-runtime` 可省略以保留该副本已有的模型配置。恢复时以上述备份为 `--source` 创建新的运行目录。原始快照、模型配置来源、数据库及归档 SHA-256 位于 `receipts/`，与 Git 提交共同构成恢复依据。

媒体与数据库可以独立复制，但恢复时必须使用同一备份批次的数据库、归档、媒体、采集认证加密密钥；只检出代码不能恢复历史数据。运行目录包含私有配置，不应提交或公开。

## 验收记录

机器记录存放于运行目录 `receipts/`：后端测试、前端测试、报告接口一致性、真实模型会话及最终冻结清单。旧 3188 和 3178 的测试结果不替代本环境验收。

冻结标记：`formal-baseline-20260911`。精确提交及构建指纹见运行目录 `receipts/freeze.json`；标签固定本次已验收的代码，后续开发应使用新分支。

已完成的验收：

- 后端整合回归：371 passed、42 subtests；前端逻辑测试：13 passed；TypeScript 与 Vite 构建通过。
- 并发快照、备份和 Hermes 会话边界回归：31 passed、12 subtests；流式校验后追加快照回归 3 passed。
- 报告工具回归：使用本环境 A/B 归档执行，22 passed，无跳过。包括完整账号索引人数与前 5 张预览卡片的区分，以及新任务评论审核覆盖统计。
- A/B/C 的报告、展示投影、发布账号分页、风险评论账号分页及冻结帖子审核详情，与原 3188 返回的业务 JSON 一致。
- 浏览器核验 C 的 146 个发布账号、正文前 5、独立完整列表抽屉及完整审核详情。原 `outputs/843?report_version=...&post_ref=post-002` 在 3198 保留 6 条评论证据、578 条已存评论，视频正常加载。
- 真实 Qwen 生成规则/词库，修改并正式保存，再读取版本；使用保存的资源创建并确认任务，未重复启动。
- 真实任务 `m3-109a05cdfb2056c1231f`：实际采集 1 帖、3 条评论，全部审核完成；约 89 秒完成并发布 `report-version:4de12d46035143dbbc984a0c2054b14b`。报告问答正确返回 1 帖、3 条已审核评论及原通过结论。
- C 问答中断恢复成功；再次读取完整账号索引正确回答 219 帖、146 个发布账号、42,259 个评论账号。没有重新审核 A/B/C。

验收发现并修复的两处问题：

1. 报告问答此前对正在被 worker 写入的审核数据库计算文件 SHA-256，随后分次加载 A/B/C，可能出现校验中途变化。现在通过 SQLite 在线备份生成包含已提交 WAL 的一致只读快照；按会话及已授权报告集合复用，持久化并复核文件摘要，仍验证报告内容和冻结快照摘要。私有副本保存在 `data/` 下对应 Hermes ledger 同级的 `report-snapshots/`；不要在会话运行中手工修改这些文件。
2. 问答工具的账号卡片是 Top 5 预览，原接口遗漏完整账号人数。现在从已校验的完整账号投影返回发布者、评论者、去重人数；双重角色分别计入角色人数，不新增画像字段。

备份目录：`/Users/ext.wanghongtao6/Documents/Codex/runtime/backups/xhs-audit-agent-formal-20260911-frozen`。包含本次业务数据、4 份报告归档、媒体、采集器、私有配置以及验收/冻结收据；备份服务未启动。SQLite 完整性和归档摘要由 `receipts/runtime-snapshot.json` 记录。备份使用独立文件，不改写原运行目录。

本次冻结是可复现的本机正式基线：已通过真实单帖流程及历史报告回归。尚未做大批量或多用户压力验收；Python/采集器虚拟环境仍复用本机已安装依赖，跨机器恢复需按依赖记录重新安装。测试中的现有 Python 弃用警告和 Vite 大 chunk 提示保留，不影响当前验收结果。
