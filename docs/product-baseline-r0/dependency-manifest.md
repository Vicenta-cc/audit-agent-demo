# R0 Dependency Manifest

| Dependency | Boundary | Version / source | Reproducibility status |
|---|---|---|---|
| Product Python/FastAPI stack | `requirements.txt` | Python 3.12.10; resolved manifest `python-resolved-dependencies-r0-1.txt` (freeze payload SHA-256 `95f99517ef9efce2905dbfbec2fab93838afff8474db630274cba9ed1e171323`) | 70-package candidate environment recorded; direct/key versions are in the manifest. The source requirements still contain ranges, so a future resolver is not guaranteed byte-identical without a frozen lockfile |
| Hermes Agent | `backend/hermes_runtime`, `requirements-hermes.txt` | Git URL pinned to `e624e9fde561e1add9388384012b295fde669ade`, version 0.20.4 | **BLOCKED**: fresh exact-commit fetch failed with an HTTP2 framing error before any object was obtained; no claim of commit absence is made |
| Hermes product plugin | `.hermes/plugins/xhs-investigation` | Candidate source, manifest v2 | Offline manifest/import check |
| Hermes M1/M2.2 domain tools | `hermes_m0/` whitelist | Hermes worktree `0a50905` | Included, fixture hashes recorded |
| MediaCrawler | `backend/audit_agent/crawler_adapter.py` | Source-only checkpoint `a77d8f4ad99b692641711c8e170c73dc7ebdf627`, based on tracked HEAD `ec56ebfe638dcfc8680f41711215ee9618a843e2` | Reproducible source checkpoint; offline auth/rate-limit tests `8 passed`; no live collection |
| Qwen / provider APIs | Existing client boundaries only | Environment-provided credentials | Not invoked by deterministic gate; no credentials committed |

Runtime paths are environment-driven. No candidate file may import or execute
from the audited absolute Hermes or MediaCrawler worktree paths.
