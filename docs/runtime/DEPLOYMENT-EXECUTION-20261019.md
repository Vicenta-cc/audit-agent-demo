# 2026-10-19 候选部署执行记录

当前阶段：源码提交与生产只读盘点。**没有切换正式域名、重启生产/API/worker 或 3499，没有运行真实采集。**

## 固定源码

| 组件 | 提交 | 内容 |
| --- | --- | --- |
| Audit 业务候选 | `be5ca8ffab13975b41080dae5e33060d4690bd23` | 父提交 `f918dd6` 隔离发布入口 dotenv；本提交迁入历史分析记录链接补丁 |
| Media 文档候选 | `fe5e3e7881fe6b137504a573e690cc2678f6e990` | 本次仅交接文档变化，预存 `.xhs-audit-crawler.lock` 未改动、未提交 |
| Media 业务代码 | `7bd9d117cd1b0753990ba754c58b8c12de8f4ca5` | 至文档候选无业务差异 |

Audit 随后的部署文档提交 SHA 以 Git 及外部执行收据记录，不自引用当前文档提交。远端推送结果另记，不能将本地提交当作远端已发布。

## 只读生产盘点

2026-10-19 04:21 UTC 现场：

- 生产 Audit：`a4997d3969beab3d499a218e7a7aee025fea46d9`；Media：`4c70ac42ef3b8e71259fd9342032bed5fec75a34`。两个 tracked checkout 均干净。
- 独立前端仍指向 `analysis-record-link-20261014-e991e84c`，所以此次本地移植确有必要。
- 生产接口/前端：127.0.0.1:8298 / 3298；原 systemd 和 edge 服务 active。候选不占这些端口及正式入口。
- Linux x86_64、glibc 2.38、Python 3.11.6；已有 Xvfb、ffmpeg、Node、Docker 29.3.1。系统盘空余约 31 GiB，数据盘仅约 5 GiB（98% 已用）。候选预备考虑系统盘独立目录；不删除生产文件，不在空间不足的数据盘构建。
- SSH 经默认 TUN / 本机 7890 连接均被关闭；单次 `ProxyCommand=nc -b en0 -G 10 %h %p` 可用。未修改全局路由、TUN 或服务器 SSH。
- 数据盘全量 `du` 45 秒超时，未作清理；后续按确切目录预算，不反复全盘扫描。

只读 runtime/environment 文件摘要及脚本收据保存在本机私有目录 `Documents/Codex/acceptance/cloud-candidate-20261019/`，不提交密钥、账号、Cookie 或数据库正文。进程环境仅提取具名非敏感字段；所见 API/worker 的以下值一致。

## 生产实际参数与候选准备值

候选先保留生产具名参数，待隔离容量验证；不移植 Mac 的 2 词、10 评论测试覆盖。

| 字段 | 当前生产实际值 | 候选准备值 |
| --- | --- | --- |
| SEARCH_TERMS_MAX | 10 | 10 |
| TRIAGE_CANDIDATES_PER_KEYWORD | 10 | 10 |
| TRIAGE_CANDIDATE_COMMENTS | 60 | 60 |
| M3_COMMENTS_PER_POST | 300 | 300 |
| INVESTIGATION_MAX_POSTS | 10 | 10 |
| CRAWLER_MAX_CONCURRENCY | 1 | 1 |
| TASK_EXECUTION_CAPACITY | 3 | 3，须容量验收 |
| TASK_ANALYSIS_CAPACITY | 3 | 3，须容量验收 |
| TASK_WORKER_PROCESSES | 3 | 3，须容量验收 |
| INVESTIGATION_MULTI_ROUND_ENABLED | true | true |
| APP_AUTH_MODE / APP_AUTH_COOKIE_SECURE | required / true | required / true |
| 对话 / 资源生成 | deepseek-v4-pro-ga-260813 | 同生产 |
| 文本 / 视觉 / 报告 | qwen3.7-plus | 同生产 |
| 预筛 / 图片 / contact sheet | qwen3.6-flash | 同生产 |

生产 `audit_index.sqlite3` 存在 `task_settings` 表但无记录，不存在本机 revision 2 的 10 评论覆盖；默认值仍须结合准确运行源码解释。新版本每词精审 1 条的产品规则保持，账号/出口保护继续生效。尚未调用模型证明候选配置最终生效；这些是准备值，不是验收通过。

## 当前构建前置项

本地尚无匹配 Linux 的完整浏览器归档、两套完整哈希锁/wheelhouse，或真实资产匹配 G0 收据。已开始核对官方发行资产及安全依据。未找到现成云端 Camoufox 候选目录；现有 Docker 镜像清单没有识别到本次已审查的基础镜像。

构建器没有自动调用 `security_gate`，但 README 要求构建前先完成 G0；不能利用实现顺序缺口绕过。未完成 G0 和目标资产时，不启动新浏览器或普通账号登录/采集。仍按 [主计划](CLOUD-CANDIDATE-RELEASE-20261019.md) 完成依赖冻结、Linux 预检、回退演练及多轮业务验收。
