# 原桥接最小候选：本地实现计划

目标：保留原B31文件、完整工具集/schema及4响应/12工具限制，分别检验重复确认、实际scope状态判断、记录与草案承接三处提示指导。仅追加实验候选，不恢复旧强制tool_choice、分阶段工具集或自动地域注入。

顺序：先增加回归测试并记录失败；新增可独立选择confirmation/state/recording和combined指导，用原run_arm及FixtureDispatcher执行手写响应；增加只读进度诊断，保留原termination/原自动判分，不把配置成功等同最终回复完成。最后复跑针对性测试、25项既有回归，冻结新版本和18dev各候选初始请求。

本轮不读取凭据、不调用真实模型、不改变业务文件，不提交或进入P1。脚本测试与历史离线诊断只验证接线，不能证明追问减少或语义提高。真实请求累计维持308/305响应；本候选真实调用0。未来真实对照须单独明确样本/组数/预算，不能复用已消耗的历史授权或将同一dev当作独立留出。

变更所有权：仅新增bridge_minimal.py、bridge_minimal_guidance.json、本方案、tests/test_geographic_scope_bridge_minimal.py及evidence；README/REPORT和主目录P0交接/主方案只追加当前交付。保留其他修改和历史。

## 用户追加真实评测授权

用户要求“如果成功改完 都实际测试一下看看有没有提升效果”。完成本地测试与冻结后，confirmation/state/recording/combined四组各固定18dev、每组最多72次真实模型请求，总预算288；每例4响应/12工具，45秒，无重试。保持DeepSeek Pro/provider及仅CONVERSATION三项配置，不新增样本、不重跑holdout。四组独立输出及预算器，可并行执行；若限流/超时影响对比必须单列，不把运行失败说成理解错误。既有原B18dev为历史对照，非同时随机控制；不得据一次开发集比较宣称稳定提升。原自动判分与只读进度诊断分别报告，全部案例人工复核。

独立代码审查发现两处新诊断误判，先补回归再修：已有同样scope但只改标题不能算本轮set承接；clear成功后继续改标题不能误报失败。原终止标签仍不改，进度诊断不替代原判分。新真实入口须enable_real及authorization_label，默认阻断；没有强制tool_choice、工具分阶段或自动注入。
