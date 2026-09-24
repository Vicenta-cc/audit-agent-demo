# 新词库生成：语义结构与具体校验诊断

## 问题

旧专用模型直接生成完整 `LexiconContent.entries`，同时承担语义编写、ID、parent_id、kind、enabled。
即使 JSON 合法、供应商正常结束，也可能因启用主题没有变体、停用主题下存在启用变体等关系而无法采用。
原 `RESOURCE_GENERATION_INVALID` 没有保留具体失败阶段或约束，难以区分数量问题和字段关系问题。

## 变更边界

新生成的词库使用 `GeneratedLexicon`，结构为 `themes[].variants[]`。
模型负责主题、搜索候选、备注及平台等语义信息；宿主构造稳定 ID、父子关联、kind 和默认启用状态。
每个主题至少一个变体，默认启用主题及实际搜索词。仅用户明确要求保留的停用备选使用
`alternatives`，宿主将其标为停用；`tags` 仍不参与搜索。

转换后仍使用原 `LexiconContent` 合同及搜索投影进行验证，不静默改词、去重、截断或丢弃非法候选。
默认整组不超过十词、用户明确数量、原文及顺序等校验均保留。
空主题、空变体、重复词、非法字段仍应失败；这是减轻模型的存储结构负担，不是保证任意输出成功。

- 只调整专用词库生成的 Schema 和相应局部输出说明，不追加主 system prompt。
- 规则生成 Schema、已有词库 content 导入、编辑启停、保存、采用、执行格式不变。
- 主题模板选择保留，无匹配时继续使用通用生成。
- 不改模型供应商，不新增自动纠正调用或自动重试。

## 诊断

`RESOURCE_GENERATION_INVALID.details` 保留：

- `validation_stage`：authoring_schema / storage_contract / search_constraints / rule_constraints。
- `validation_errors`：有界字段路径、错误类型、固定约束代码；最多二十条，另存总数。
- diagnostic_id、请求与响应哈希、供应商 request ID、HTTP 状态、finish_reason、编写器版本。

例如 `default_count_exceeded`、`exact_terms_mismatch`、`duplicate_term_platform_match_type`。
普通日志及工具回执不回显原始响应、字段值、未知额外字段名、API key 或供应商异常原文。
隔离验收可显式注入 `diagnostic_sink`，保存完整请求消息、原始响应和诊断；生产默认不保存正文。
验收原始记录仅存放在私有目录，文件权限 0600，不提交 Git。
取证写入失败不会替换原校验异常或触发生成重试。

## 验收记录

2026-09-24 对上轮色情场景原请求重放，消息哈希完全一致：
`4fd81845fa0f8337df6a979299e8f9b5f8c742dbbdfd2efc6216d836fe9a5492`。
这次旧格式输出 HTTP 200 / stop，但“地陪导游”为启用主题、没有任何变体，
明确触发 `theme_without_active_variants`。这是重放产生的新响应，不冒充上轮未保存的原始响应。

新格式以同一调查请求独立生成两次，均通过，实际词数分别为九和八。
网络赌博场景复测同样通过，实际词数为八。相关自动化回归为 273 passed。
结构通过不代表这些词的检索质量或固定含义已得到证实，仍须对噪声、机械组合及无依据断言进行评估。
本轮验收仅调用专用生成步骤，不保存正式词库、不启动采集、不重启现有服务。
