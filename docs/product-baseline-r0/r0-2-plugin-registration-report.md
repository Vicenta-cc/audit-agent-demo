# R0.2 Plugin Registration Report

- Runtime: Hermes Agent 0.20.4 in the candidate-local Python 3.12.10 virtual
  environment; no `PYTHONPATH`.
- Discovery: `xhs-investigation` 0.3.0, source `project`, enabled, no load error.
- Registration: 11 tools and one `tool_execution` middleware.
- AIAgent: constructor-only smoke returned `run_agent.AIAgent`; canonical
  prompt hash matched; Provider call count was zero.

Registered product catalog:

```text
read_report
list_finding_posts
search_posts
read_posts
list_post_risk_comments
list_evidence
read_evidence
get_account_overview
list_account_occurrences
read_account_occurrence
read_account_post
```

The manifest rename/adaptation changes plugin metadata only. The registered
names come directly from final `M2_ACCOUNT_ACTIVITY_TOOLS`; the test asserts
manifest, registration callback, and live Hermes registry equality.
