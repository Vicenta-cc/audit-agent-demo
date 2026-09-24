# 新增统一报告：选定报告共同账号查询

范围：仅新增 1–30 帖报告的账号比较与目录；不改审核、生成报告、创建 Agent 或账号活动授权语义。

## 工具分工

- list_authorized_reports：分页列出已授权的新报告标题、序号、是否当前报告、会话 report_ref。只用于选报告，不返回账号，不切换当前报告绑定。
- compare_authorized_report_accounts：必须传目录中取得的 2–10 个不同 report_refs。只计算选定报告的交集，不再默认比较所有报告，不返回各报告完整目录或所有两两比较。
- list_report_accounts：明确要求某份报告账号目录时单独调用；接受一个 report_ref。

均默认每页 10 个、最多 20 个。cursor 绑定会话、授权/快照及账号数据修订、查询类型、目标报告、角色和结果顺序；不能跨查询或跨会话复用。报告引用和游标沿用现有持久化注册表，重启后可恢复。

## 交集语义和结果

共同账号指在每一份所选报告中均出现；2 份时就是两者交集。三份以上不再含混表示任意两份共有。
role=any 接受不同报告中的不同角色；post_author/comment_author 要求每份报告均为发布者/评论者。
common_account_count / common_publisher_count / common_commenter_count 是服务端全量交集统计；
matched_account_count 是所选 role 下的全量总数，accounts 仅是当前页。
同一账号可以同时计入发布者和评论者，两角色计数不互斥。has_common_accounts 指任意角色交集是否非空；角色筛选是否有匹配以 matched_account_count 为准。

每个账号只返回昵称、结果序号、会话 account_ref，以及其在所选报告中的角色和活动数量。
稳定平台标识不外露，内部引用不进入用户回答。用户问“第二个都评论了什么”，直接复用 account_ref，按原有授权范围查询活动，不重新按昵称猜测，也不将查询选择误当权限变化。
同昵称保留为不同账号；同名报告要求用户确认目录序号。不能稳定识别的活动返回排除数量，“没有共同账号”仅代表可识别的冻结资料，不证明现实中不存在联系。

## 兼容及边界

旧 compare 工具的空参数调用现在明确报参数错误，而不是隐式扩大查询。
旧报告模板的工具目录保持不变，新增工具只注册到 unified-report。
只替换主 Prompt 中原有账号比较段落；参数与分页细节放工具说明，不追加另一套冲突协议。
不修改旧发布快照/数据库、不改统计为模型推算、不更改账号穿透授权。

## 验证（2026-09-24）

7 文件：164 passed，12 subtests passed；git diff --check 通过。
覆盖真实报告存储/只读工具链、原生工具注册、默认/上限分页、过滤后分页、完整计数、交集无匹配、多报告交集、同昵称不合并、越权/跨会话/过期引用、游标错配、账号概览追问、引用与分页序列化、实际报告服务重建后的引用恢复、公开活动与引用脱敏。
运行使用固定应用 Python，PYTHONPATH 包含候选源码、hermes_m0 和已安装 Hermes 源码。
文件：
tests/test_report_account_queries.py
tests/test_unified_report_investigation.py
tests/test_public_activity_projection.py
tests/test_pass_investigation.py
tests/test_r021_session_scoped_runtime.py
tests/test_hermes_m1_m22_offline.py
tests/test_unified_report.py

尚未做新真实模型/前端交互验收。未重启、提交或推送；运行中的 3398/8398 不受本修改影响。
创建 Agent 的独立验收任务应继续使用其启动时的固定快照，不把本次后续变化混入原验收结果。
