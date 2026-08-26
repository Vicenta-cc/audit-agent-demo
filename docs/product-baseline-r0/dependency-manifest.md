# R0 Dependency Manifest

| Dependency | Boundary | Version / source | Reproducibility status |
|---|---|---|---|
| Product Python/FastAPI stack | `requirements.txt` | Candidate copy of reviewed demo change | Deterministic install subject to lock/environment |
| Hermes Agent | `backend/hermes_runtime`, `requirements-hermes.txt` | Git URL pinned to `e624e9fde561e1add9388384012b295fde669ade`, version 0.20.4 | **Gate pending**: external source metadata lacks `.git`; clean-room install/import must verify |
| Hermes product plugin | `.hermes/plugins/xhs-investigation` | Candidate source, manifest v2 | Offline manifest/import check |
| Hermes M1/M2.2 domain tools | `hermes_m0/` whitelist | Hermes worktree `0a50905` | Included, fixture hashes recorded |
| MediaCrawler | `backend/audit_agent/crawler_adapter.py` | External worktree HEAD `ec56ebfe638dcfc8680f41711215ee9618a843e2` | **Gate pending**: required auth/rate-limit/streaming changes are dirty and uncommitted; source not copied |
| Qwen / provider APIs | Existing client boundaries only | Environment-provided credentials | Not invoked by deterministic gate; no credentials committed |

Runtime paths are environment-driven. No candidate file may import or execute
from the audited absolute Hermes or MediaCrawler worktree paths.
