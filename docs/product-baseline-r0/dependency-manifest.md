# R0.2 Dependency Manifest

| Dependency | Boundary | Version / source | Reproducibility status |
|---|---|---|---|
| Product Python/FastAPI stack | `requirements.txt` | Python 3.12.10; resolved manifest `python-resolved-dependencies-r0-2.txt` | Candidate environment recorded. Source requirements still contain ranges, so the resolved manifest does not guarantee future resolver equivalence |
| Hermes Agent | `backend/hermes_runtime`, `requirements-hermes.txt` | Requirement pins `e624e9fde561e1add9388384012b295fde669ade`; installed frozen runtime is 0.20.4 | Candidate-local non-editable wheel; public imports and all registered critical hashes verified; no runtime dependency on the source directory or `PYTHONPATH` |
| Hermes product plugin | `.hermes/plugins/xhs-investigation` | Manifest v2, plugin 0.3.0 | Real discovery: enabled, 11 final M2.2 tools, one execution middleware |
| Hermes M1/M2.2 domain tools | `hermes_m0/` whitelist | Hermes worktree `0a50905` | Included, fixture hashes recorded |
| MediaCrawler | `backend/audit_agent/crawler_adapter.py` | Source-only checkpoint `a77d8f4ad99b692641711c8e170c73dc7ebdf627`, based on tracked HEAD `ec56ebfe638dcfc8680f41711215ee9618a843e2` | Reproducible source checkpoint; offline auth/rate-limit tests `8 passed`; no live collection |
| Frontend toolchain | both `pnpm-lock.yaml` files | Node 24.19.0; pnpm 11.19.0 | Frozen installs reused the local content-addressed store; zero packages downloaded; both production builds passed |
| Python SQLite | Python standard library | 3.49.1 | Hermes warned about the upstream WAL reset issue and selected `journal_mode=DELETE` in isolated smoke state; production concurrent write validation is outside R0.2 |
| Qwen / provider APIs | Existing client boundaries only | Environment-provided credentials | Not invoked by any gate; no API key or Provider request used |

Runtime paths are environment-driven. No candidate file may import or execute
from the audited absolute Hermes or MediaCrawler worktree paths.
