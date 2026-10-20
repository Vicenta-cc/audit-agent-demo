# Synthetic historical SQLite report fixture

These files contain synthetic audit records only, not production data.

`snapshot.json` was produced by the **unmodified** `build_immutable_snapshot`
from application commit `cc82d62d9595f2908ac5e5a13e5895d64cccec23`, executed in
an isolated Python process. It uses the historical raw SQLite file digest and
has no `source_db_digest_kind` metadata. `source.sqlite3.gz` contains the exact
standalone database bytes hashed by that builder. `provenance.json` records the
source and snapshot hashes. The account corpus is built from the same data.

The test decompresses the source, loads the historical snapshot, runs the real
R3.1 graph with explicitly synthetic provider responses, and publishes a real
correction into a separate report store. All schema, semantic, publication,
snapshot, and Hermes graph validation remains enabled. No model steps are
inserted manually. The test also verifies rejection after changing the source.

Tests require neither Git objects nor network access. To regenerate the files
intentionally, run from the repository root with the application environment:

```sh
PYTHONPATH=. python tests/fixtures/reporting_legacy_cc82/generate.py
```

Regeneration requires the recorded historical commit in local Git. The fixture
includes pass, low, medium, and high records and direct OCR evidence because
the historical R3.1 generation path expects those metric groups and evidence.
This verifies compatibility with that path, not support for arbitrary audit
input combinations or production LLM quality.
