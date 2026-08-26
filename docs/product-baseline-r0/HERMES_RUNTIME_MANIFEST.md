# Hermes Runtime Manifest

## External runtime

- Required distribution: `hermes-agent==0.20.4`
- Declared install: `requirements-hermes.txt`
- Recorded source version: `0.20.4` from the local Hermes `pyproject.toml`
- Required source commit: `e624e9fde561e1add9388384012b295fde669ade`.
  R0.1 independently fetches and checks out this exact commit from the
  official repository. The earlier R0 `git ls-remote` observation is retained
  only as an incomplete audit attempt, not as evidence that the commit is
  missing. The R0.1 fetch attempt is documented in
  `hermes-clean-room-report.md` and ended before any object was obtained.
- Required Python: `>=3.11,<3.14`
- Product imports only public `hermes_cli.plugins`, `model_tools` and
  `run_agent.AIAgent` through `backend/hermes_runtime/adapter.py`.

The candidate must never depend on the audited absolute Hermes source path or
on a foreign worktree virtual environment at runtime.

## Selectively integrated Hermes M1/M2.2 modules

`hermes_m0/` contains only the public domain, refs, ledger, repositories,
services, schemas, runtime, plugin, Account corpus/activity and report-task
modules listed in `included-files.md`. Excluded diagnostics, provider
preflight, held-out runners, `real_report_contract.py`, raw traces and old
experiment packages are archive-only.

## Key source hashes from the audited external source

```text
run_agent.py a26e5264738f1c62347e63c1265e562d3cfae439dadc313db48572f3e9cc751b
model_tools.py 32a106d66835dc9f88f15624076086a53cbd4bb7ed80889228d0e53f62d4cfac
registration_lifecycle.py 42c7a95159a02cd5dd252a5f5d43e81d083656b3498b4b95bc09b078e3fbdee0
hermes_state.py 70c69963f39902bad1b3ed1b943aaa1dc195b986ebd267fe1b0ce49a0c6d6723
hermes_state_schema.py 04a60bc99ca439a4e7c7187857a17dd910ff51c805e00999e3af6a5ec8e25c3e
hermes_state_common.py 52c6e960369099f2497015bd3a8033ba47c5be0edc50b8dae163144acf0c9080
hermes_constants.py 58b8d5f17bc6d23dc192990bb71cb92725ed37b6c99a4a5b4cc97ac807846d56
pyproject.toml 1f928b1560b0669291b3f7d562aa78c99ac4f927375939ca97fd3c3e7494cb91
uv.lock 8fd868b9da8b6bc2f4aa94a845e210eccdd5e31be7a0b404f0a8527ced0fddec
```

The hashes above are historical local-source audit values. R0.1 does not
substitute them for official-commit hashes: exact-commit checkout and
comparison of these registered files remain blocked by the failed network
fetch. The official source must be used as-is when the gate is retried.
