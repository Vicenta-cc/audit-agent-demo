# 云端分析记录链接补丁的本地移植

日期：2026-10-19。工作区分支 `codex/new-media-app-integration-20261016`，已提交起点 `709c3929c5c584d3b596b83b813f7555e14c913d`。本项为独立前端改动；没有重启 3499、连接或修改线上、调用模型或真实采集。已有发布入口 dotenv 修复保留，未混入前端实现。

## 补丁来源与范围

10 月 14 日独立前端发布 `analysis-record-link-20261014-e991e84c` 基于 Audit `a4997d3969beab3d499a218e7a7aee025fea46d9`，补丁摘要 `e991e84ce42fe6171fc981cc62639cb7e18ff30d2cf7504f2cbf1fd60b31ebb0`。源码快照及测试保存于本机 `Documents/Codex/acceptance/frontend-audit-provider-hotfix-20261014`；三处源码和原测试均已与历史 manifest 的 SHA256 核对。该发布未改变后端 Git SHA，不能仅靠后端祖先关系判断是否纳入。

- `InvestigationCenterArea.tsx`：给任务卡传会话 ID；具体调查轮次仍由单独的 Run 参数指定。
- `m3AnalysisRecords.ts`：按指定 Run 查对应历史轮次，兼容旧 Run 路径；校验 Run 和 ReportVersion 一致性。
- `AnalysisRecordsPage.tsx`：使用上述加载方式；旧 Run 路径返回调查工作区，不再跳入不存在的会话。保留页面卸载后的异步更新保护。
- `analysisRecordTarget.spec.ts`：迁回历史 7 项测试，再补 11 项边界；加入 `playwright.logic.config.ts`，避免默认离线测试漏跑。

实现不将权限失败转换为其他端点的查询，不自动退到最新调查轮次，也不修改后端授权。现有其他页面修改按补丁上下文保留，没有整文件覆盖旧快照。

## 离线验证

先迁入历史测试，确认旧实现缺少 `loadAnalysisRecordRun` 导出、测试加载失败；移植后历史 7 项通过。最终目标解析测试 18 项覆盖：历史轮次、旧链接、当前轮次、缺失轮次、Run 不匹配、报告不匹配，以及两种路径的 401/403/404/503 原样拒绝且不改走其他端点。

从 `Audit_assistant` 执行：

```sh
node node_modules/@playwright/test/cli.js test --config=playwright.logic.config.ts
VITE_API_PROXY_TARGET=http://127.0.0.1:8499 npm run build -- --outDir /tmp/audit-analysis-link-hotfix-20261019-dist
```

逻辑套件 **50 项通过**（含上述 18 项），仅模拟 HTTP 返回，不发起网络请求、不启动浏览器或 Vite 服务。构建输出放在独立临时目录，不覆盖在用前端产物；代理目标仅供构建配置解析，不代表调用 8499。

`tsc --noEmit` 与 Vite 生产构建通过，保留既有大包体积提示。既有 dotenv 修复的 3 个文件已按移植前 SHA256 逐字节复核，保持不变；部署文档相对链接、代码围栏和 `git diff --check` 通过。独立代码 review 为 **APPROVE**（8 个范围内文件，未发现确认缺陷），架构 review 为 **CLEAR**。两位审查者各自重跑 18 项目标解析测试并通过；代码审查者另独立运行 TypeScript 检查通过。多轮投影依赖已补入候选配置及验收门槛。历史云端 30 项测试及浏览器验证仅作补丁来源证据，不能充当此次本地或未来候选的验收。

## 发布约束

本地补丁已单独提交为 `be5ca8ffab13975b41080dae5e33060d4690bd23`，父提交 `f918dd68b6072292ff74c8e2d1ec6215555d94b0` 为已审查的 dotenv 修复；相对已推送起点 `709c392` 没有其他业务代码变化。推送及最终文档提交摘要另记执行收据；在独立 Linux 候选验证旧链接、同会话多轮调查、权限拒绝、返回导航和帖子详情。未将本地离线验证扩大为线上已升级或完整真实链路已验收。

历史轮次读取依赖 workspace state 的 `rounds` 投影。候选必须逐项核对 `INVESTIGATION_MULTI_ROUND_ENABLED` 的最终生效值及旧会话的轮次成员数据；开关关闭或旧轮次缺失时，前端保持拒绝，不能把该情况当作历史多轮链接已通过，也不能添加绕过 workspace 的自动 Run 查询。真实权限仍须由多个登录用户的候选验收证明，模拟 HTTP 拒绝测试只证明前端不改走其他端点。

配套文档：[部署计划](CLOUD-CANDIDATE-RELEASE-20261019.md)、[部署准备核对](DEPLOYMENT-READINESS-20261019.md)。
