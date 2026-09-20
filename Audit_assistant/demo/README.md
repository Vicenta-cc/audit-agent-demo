# 前端改动假数据预览

基于候选提交 `4506314`，直接复用 `InvestigationSidebar`、`InvestigationCenterArea` 及其流水线组件。仅添加独立演示入口，不修改产品组件、业务数据或正式启动配置。

在 `Audit_assistant` 目录运行：

```sh
PATH=/Users/ext.wanghongtao6/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH node node_modules/vite/bin/vite.js --config demo/vite.config.ts
```

访问 http://127.0.0.1:4175/demo/ 。顶部蓝色工具栏仅用于切换假数据，不属于正式页面。

预览现已直接使用候选正式组件的紧凑布局，不再提供旧版 CSS 覆盖和布局切换。可切换正常完成、部分失败、全部失败、暂停、主动结束，观察绿色、黄色、红色、灰色状态。暂停／继续按可用动作切换，页面已移除停止分析。

可操作：暂停／继续采集和分析、结束任务确认及模拟停止、会话删除、额度展示切换。结束操作等待 2.5 秒模拟后台确认停止；删除不改变额度。其他业务入口只给出预览范围提示。

所有 fetch 均在页面内存中响应，未知请求拒绝；专用 Vite 服务没有 API 代理并拦截 `/api`。不连接真实后端、数据库、采集器或账号 Profile。此演示用于看界面，不代表真实任务验收。

已验证：包含 demo 的 TypeScript 检查通过；浏览器切换场景、暂停继续、结束、删除和额度展示通过；未发现页面脚本异常或真实 API 网络请求。

阶段五账号预览：`http://127.0.0.1:4175/demo/accounts.html`，复用当前登录、账号管理和调查组件。账号与额度位于会话顶部工具栏；点击剩余次数查看计次说明、等待原因和返回当前任务，点击用户名查看有效期和退出。仅使用页面内存假数据。
