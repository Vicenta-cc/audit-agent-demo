# P2/P3/P4 离线联调实验

真实P1临时资源/地域来源 → P2 capture/build/validate/save/load → 绑定适配 → 原风险/P3/P4选择与恢复。仅人工响应、本地候选和临时数据库；不产生正式Run/Job，不启用地域调查。

基线P1 `031e0cd26379ee44c2372c9a28536c377ab3d4b9`；P3 `2120c53379d1b79c45e3cb8232b9c9b96657b183`。P2/P4按 `artifacts/joint-20261015-01/INPUT_MANIFEST.json` 导入，禁止修改这些上游文件。P4原会话复验待完成；用户后续明确要求直接离线联调，记录保留在清单和REPORT，不伪造验收通过。

从此独立工作树根目录，使用已有解释器，输出目录必须全新：

```sh
PYTHONPATH=backend:. /Users/ext.wanghongtao6/Documents/Codex/projects/audit-agent-demo/.venv/bin/python -B -m experiments.geographic_joint_integration.run_offline --output-dir experiments/geographic_joint_integration/artifacts/new-run
```

默认覆盖新增joint、导入P2及P4全部测试，JUnit和逐例证据在新输出目录。runner在pytest导入前装防护，数据库限定本轮绝对临时根与输出根，Python恢复子进程继承相同防护；禁止其他进程/网络/模型/爬虫/调度。故意拒绝探针单列，意外尝试使整轮失败。临时库生命周期覆盖父子进程，结束即删除。

独立进程replay的实际命令与输入/外部expected示例由执行测试记录；使用新临时根的runner或等价相同防护环境，不能把旧临时路径当长期数据库。`audit.py`只读核对P1原树、P3 Git对象、导入来源/目标字节及新增白名单。

见 `INTERFACE_MAP.md`、`INTERFACE_GAPS.md`、`REPORT.md`。正式流水线、前端、采集、上线仍不在此实验范围。
