# 原六项失败的环境复核

日期：2026-10-15。原六项中 **2 项消失、4 项保留**；干净基线和 P1 的失败列表完全相同，未观察到新增回归。原先 3 个依赖条件跳过项已全部实际执行通过。

| 环境 | 通过 | 失败 | 跳过 | 子测试通过 |
| --- | ---: | ---: | ---: | ---: |
| a4997d3，无 Hermes 导入路径 | 243 | 6 | 3 | 23 |
| P1，无 Hermes 导入路径 | 243 | 6 | 3 | 23 |
| 干净 a4997d3，锁定 Hermes 源码 | 248 | 4 | 0 | 23 |
| 当前 P1，锁定 Hermes 源码 | 248 | 4 | 0 | 23 |

## 原六项逐项对照

| 完整 node ID | 原失败原因 | 补齐后基线 / P1 |
| --- | --- | --- |
| `tests/test_investigation_creation_conversation.py::test_real_hermes_0204_dispatch_supplies_stable_mutation_identity` | hermes_cli 不可导入 | PASS / PASS |
| `tests/test_investigation_creation_conversation.py::test_real_hermes_registry_switches_creation_report_creation_without_tool_loss` | hermes_cli 不可导入 | PASS / PASS |
| `tests/test_creation_prompt_isolation.py::test_guidance_is_available_through_native_deferred_describe` | tools.tool_search 不可导入 | FAIL / FAIL；导入成功后在第 74 行访问 describe 返回值的 name 字段触发 KeyError |
| `tests/test_creation_prompt_isolation.py::test_global_prompt_keeps_authority_but_not_authoring_payloads` | 缺预期的旧提示词片段 Only use_ruleset_proposal | FAIL / FAIL，原断言未改 |
| `tests/test_creation_prompt_isolation.py::test_formal_lexicon_prompt_uses_backend_bound_references_not_model_hashes` | 缺预期的旧提示词片段 reuse the resource_ref recall_plan | FAIL / FAIL，原断言未改 |
| `tests/test_creation_prompt_isolation.py::test_original_prompt_lines_are_preserved_or_explicitly_accounted_for` | 旧提示词逐行比较要求 create_investigation_draft 片段 | FAIL / FAIL，原断言未改 |

这四项属于在干净基线可复现的失败，不代表已修复或可以忽略；本轮未修改其断言、跳过条件或旧契约来制造通过。没有新增失败。

## 锁定依赖与隔离

Hermes 为项目规定的 0.20.4，Git HEAD 为 e624e9fde561e1add9388384012b295fde669ade；九个源码 SHA256 全部与 docs/product-baseline-r0/HERMES_RUNTIME_MANIFEST.md 一致，见 hermes-source-sha256.txt。

测试复用了已有解释器和锁定源码，未安装或升级依赖。PYTHONPATH 的第一项和 hermes_m0 对基线、P1 分别指向各自目录，Hermes 源码路径仅用于此次测试复现，未写进产品运行代码。候选独立运行环境仍须按仓库 requirements-hermes.txt 安装锁定依赖，不能将外部工作树路径当产品依赖。

dotenv 已禁用，没有加载 business/environment.private.json；每次测试单独隔离 data、outputs、APP_AUTH_DB、资源锁、调度数据库、采集密钥文件位置、浏览器 profile 和 HERMES_HOME。未运行正式服务、模型或采集。已有 multipart/on_event 弃用警告与测试失败分别记录。

原始输出：

- backend-baseline.log / backend-regression.log：无 Hermes 导入路径对照。
- backend-hermes-baseline.log / backend-hermes-p1.log：补齐锁定依赖后的完整输出。
- environment-retest-commands.json：精确启动命令、工作目录与隔离路径。

干净基线来自 git archive a4997d3，路径为 /tmp/geographic-p1-baseline.tCSxCT；未重置或改动任何现有工作树。

