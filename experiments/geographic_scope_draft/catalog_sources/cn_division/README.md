# P1 cn-division offline catalog

This directory contains the user-authorized third-party base dataset pinned to
`kk-418/cn-division@88f2021ea21769bdb95d93699d8a625fcd9165ef` (package 2026.0.1).
It is not an official publication. Its Git commit date, 2026-09-02, is a version
date, not an official effective date. Retrieval/review date: 2026-10-15.
The MIT license is retained verbatim in `upstream/LICENSE` and in the snapshot.
No upstream code is installed or executed. No runtime network lookup is needed.

## Reproduce

From the P1 checkout, with Python 3:

```sh
python3 scripts/import_cn_division_catalog.py \
  --source-dir experiments/geographic_scope_draft/catalog_sources/cn_division \
  --output backend/investigation_creation/data/geographic_catalog.json
```

`upstream/` retains exact downloaded bytes of three original JSON arrays, the
license and source scripts/package metadata. `review.json` is a separate derived
review input containing three province supplements, their official evidence
URLs, and the complete explicit alias list. `snapshot.json` losslessly embeds
all original inputs as Base64 with fixed-commit URL/SHA256 and that review.
It is labelled a derived audit envelope, not original upstream raw data.
`manifest.json` pins separate snapshot and normalized hashes and the backend
transform implementation hash. `normalized.json` is deterministically rebuilt
from snapshot inputs; `generation_report.json` records all original input hashes,
coverage shape, conflicts and excluded counts. `excluded.json` preserves each
excluded original row and reason. Tests reproduce snapshot, normalized bytes and
production catalog exactly in a fresh temporary directory.

## Scope and preservation

- 34 province-level records for parent attribution, including four supported
  municipalities and the official Taiwan/Hong Kong/Macao supplements. Ordinary
  provinces and special administrative regions are not city targets.
- 333 actual prefecture-level records: 293 prefecture cities, 30 autonomous
  prefectures, seven prefectures, three leagues. Only cities are supported for
  resolved P1 scope; other real types remain available to explain pending scope.
- 2,851 six-digit county-level auxiliary records. The 33 six-digit
  province-direct county rows appearing in upstream `cities.json` remain county
  level. None are promoted to prefecture cities.
- Five virtual municipality layers and 84 twelve-digit township/street rows are
  preserved in the exclusion audit instead of becoming supported targets.
- 3,004 reviewed explicit aliases are materialized at build time. For known
  prefecture cities/county cities/districts/counties, the permitted exact short
  form removes the final 市/区/县 only. Province aliases are an explicit list.
  Runtime resolution never strips suffixes, does fuzzy matching, or substitutes
  a parent region. Autonomous-area ethnic names are not loosely abbreviated.
- 120 canonical/alias collisions remain preserved in the data, including 克拉玛依
  (`650200` prefecture city / `650203` district), 朝阳 (city and districts), 吉林
  (province/city), and 海南 (province/district). Runtime scope is restricted to
  prefecture cities and municipalities: 克拉玛依, 朝阳 and 吉林 therefore resolve
  to their supported city, without province/county collision questions. Explicit
  province/county names stay pending; they never fall back to a parent city.
- Kind parsing treats only 神农架林区 as 林区. 万柏林区、金林区、北林区、新林区、碑林区
  retain district type; a broad 林区 suffix rule would be wrong.

The importer checks original byte hashes, exact source pin, six-digit normalized
codes, province and raw city parent relationships, real level/kind, all province
coverage, duplicate identities, exact reviewed aliases, cross-level collisions,
normalized-byte reproduction and packaged identity hashes. These mechanical
checks do not prove official completeness. Read the official verification
material and the manifest's coverage review for the external coverage evidence.

## Independent province/prefecture verification

The 367 province/prefecture identities, including all 297 P1-supported cities
(293 prefecture cities and four municipalities),
match `../official_verification/province_prefecture_benchmark.json` exactly by
code, name, level, kind and province parent. The benchmark starts from a 2016
historical official complete table, applies independently officially evidenced
Laiwu removal and Nagqu reclassification, and is corroborated by official 2024
aggregate counts. It is explicitly not a fresh 2026 official nationwide table.
The snapshot embeds that separately reviewed benchmark and its artifact hash;
the transform enforces every identity on each rebuild, rather than counts only.
See `../official_verification/comparison_report.json` and `EVIDENCE.md` for sources,
retrieval boundaries and offline reproduction. County auxiliary rows have not
all been independently officially checked; their role is auxiliary type
validation and explanation of unsupported inputs. They never become P1 targets.
