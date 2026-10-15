# P0 冻结实验的运行基线

P0 的八份原始测试保存在 `baseline_tests/`，内容与原 P0 工作区一致。冻结实验源码、提示、样本、schema、评分和历史失败证据保持原样。

这些测试属于 `a4997d3969beab3d499a218e7a7aee025fea46d9` 基线。`contract.py` 会从所在仓库的生产源码提取 `ConfigureDraftInput`，因此不能把它们当作 P1 当前契约的生产回归：P1 新增的 `scope_decision` 等要求会改变实验输入。不能通过修改旧样本或刷新快照消除这种差异。

离线复现方式：在一个新的临时目录解出 `git archive a4997d3969beab3d499a218e7a7aee025fea46d9`，把本目录复制到临时仓库的 `experiments/geographic_scope_intent/`，再把 `baseline_tests/test_*.py` 复制到临时仓库的 `tests/`。在该临时仓库使用具有项目依赖的 Python 执行：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=hermes_m0 python -m pytest -q \
  tests/test_geographic_scope_bridge_minimal.py \
  tests/test_geographic_scope_contract.py \
  tests/test_geographic_scope_data.py \
  tests/test_geographic_scope_scoring.py \
  tests/test_geographic_scope_runner.py \
  tests/test_geographic_scope_contrast_intent.py \
  tests/test_geographic_scope_contrast_runner.py \
  tests/test_geographic_scope_contrast_scoring.py
```

这组纯离线测试不需要外部 Hermes。把外部 Hermes 加入 `PYTHONPATH` 会引入它的 `tests` 包，与旧测试的交叉导入冲突。P1 生产回归仍使用本仓库根目录下的 `tests/`，并按需要加入外部 Hermes。

2026-10-15 合并验证：原 P0 工作区八份测试 435 项通过；另在从 Git 基线重新解出的临时仓库复现，结果见最终合并报告。没有追加真实模型调用。
