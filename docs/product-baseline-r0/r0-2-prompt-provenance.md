# R0.2 Prompt and Configuration Provenance

| Resource | Canonical SHA-256 | Candidate SHA-256 | Role | Class | Consumer |
|---|---|---|---|---|---|
| `hermes_m0/account_activity_prompt.txt` | `62768caf1e59d4783c8c8ca362e30dd5bbee05f232898631bd7d31512a360c9e` | same | Final M1/M2.2 product System Prompt | runtime | `HermesRuntimeBinding.create_agent()` as `ephemeral_system_prompt` |
| `hermes_m0/diagnostic_prompt.txt` | `503171df4f263e39b16def13e6ecdb0dbc9c089b14ebe62a8e7ba4edff3aaab7` | same | M0/M1 diagnostic oracle | validation-only | archived diagnostic runners; no formal adapter import |
| `hermes_m0/real_report_diagnostic_prompt.txt` | `982ce6093c80e0b7b6996a66b5f0959b9ba37271442bb06bd3c231692e1a1e36` | same | M1 real-report acceptance prompt | validation-only | archived diagnostic runners; no formal adapter import |
| `hermes_m0/profile/config.yaml` | `15c830c2fe0e9651db4d98a7e1d3fdd3148fca16d0f50575dec14b0bc0cceb18` | same | Canonical diagnostic profile | validation-only | not loaded by the formal adapter |
| `hermes_m0/provider_config.py` | `55d88941dc18ff55ef3c31ad758611febec9ab1407ef34d55cb7acc1b7104dd2` | same | Diagnostic/provider preflight helper | validation-only | not imported by the formal adapter |

The product prompt is loaded through package resources and guarded by its
SHA-256 before `AIAgent` construction. No legacy Planner prompt, concatenation,
or rewritten prompt is used.

`HERMES_INVESTIGATION_TASK_MODE`, report-task mode, and real-report diagnostic
mode are rejected by the formal adapter. ACCOUNT_ACTIVITY is the sole product
catalog. The old cross-dataset fixture is therefore not restored.
