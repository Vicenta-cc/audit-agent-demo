# 本地离线联调检查点

2026-10-15，经用户同意保存本地Git提交和新标签。分支为 `codex/geographic-relevance-p234-offline`，父提交P1 `031e0cd26379ee44c2372c9a28536c377ab3d4b9`，标签为 `checkpoint/geographic-p234-offline-20261015`。这是离线成果冻结；P4原会话返修复验及本轮原会话验收仍待完成，地域启动阻断保留。

## 与GitHub主线的关系

只读 `git ls-remote` 现场核对：

- `git@github.com:Vicenta-cc/audit-agent-demo.git` 的 `main` 为 `a4997d3969beab3d499a218e7a7aee025fea46d9`，本地应用分支 `codex/investigation-rounds-phase2` / 工作树 `unified-report-query-phase1` 指向此提交。P0/P3实验分支也从此提交起步，但各有未提交实验内容。
- 本地名为 `main` 的分支仍是 `cd62a96e1e04609d346001be613ec1c2544c1e73`，不代表当前GitHub应用主线。
- 本仓库配置的 `origin` 实际为另一个仓库 `Vicenta-cc/saas-demo`，其远端 `main` 是 `5b6694d1b27b394e33a9141d424415a645bd2eaa`。没有修改远端配置，也没有推送。
- 本检查点沿 `a4997d3` → 已集成P0/P1及资源工具的 `031e0cd` → 本轮离线联调保存。未合并回应用GitHub主线，未核查或修改云服务器。

## 保存内容

Git保存全部209项固定导入文件（约14MB，保持INPUT_MANIFEST所需文件完整）、本轮新增源码/测试/报告、输入输出摘要清单、最终联合轮JUnit/摘要/命令/轻量日志，以及P2/P4新鲜原回归轻量证据。完整逐例风险/P3/P4轨迹和开发失败轮原件留在工作树，并复制到外部压缩交接包；没有删除或替换原失败记录。

完整包位置、SHA256与逐成员核验结果见 `CHECKPOINT_ARCHIVE.json`。交接包按仓库相对路径组织，包含此前INPUT/OUTPUT清单全部文件及OUTPUT清单自身。新CHECKPOINT说明与归档收据由本Git提交保存，避免归档自引用。需要在另一份检出中查看全部原始证据时，先验证压缩包摘要，再恢复到独立证据副本，勿覆盖已存在的修改。

测试源码保持最终87通过时的字节；本轮冻结不修改业务或测试实现，不重复执行9分钟联合全套。P2的4项精确P1既有失败继续保留。最终测试结果与源码摘要见REPORT及FINAL_TESTED_SOURCE。

`audit.py`是提交前的S0/S5工作树审计，显式要求HEAD为原P1；在新检查点HEAD直接运行会因这个前提拒绝，不能把该拒绝当成业务回归。该脚本未为提交而修改；其提交前结果已保存。联合测试runner使用固定INPUT_MANIFEST，仍可在本检查点复跑。

无commit以外的业务操作：不启动采集/模型/服务、不写正式库、不推送、不合并、不部署。REPORT中“未提交”记录的是实现轮结束时的历史状态；本文件单独记录本次随后授权的本地冻结。
