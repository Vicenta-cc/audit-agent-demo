# SQLite report snapshot lock repair — 2026-10-20

Application baseline: `cc82d62d9595f2908ac5e5a13e5895d64cccec23`.
Crawler baseline remains `3a433ed2748c4c7262fd870c5e381035f4573788`.
This patch does not change runtime credentials, proxy settings, Nginx, database
journal mode, account data, or the crawler.

## Failure and repair

Report freezing opened and closed the active SQLite main file to compute a
SHA-256 digest. On affected POSIX environments, closing that descriptor releases
locks held by other SQLite connections in the same process. An independent
connection can then remove WAL files still used by the worker: its next commit
can be invisible to the API.

The new `sqlite_snapshot()` context manager uses SQLite's online backup API to
create a private, standalone database image, explicitly closes both connections,
and hashes only that image. Backup retries have a 30-second deadline. Temporary
images are cleaned up on success and failure.

`CanonicalReportSource.frozen_database()` preserves source subclass behavior and
binds database reads to the private image. Snapshot rows, configuration, title,
coverage database queries, and target identity therefore share that image.
Connections opened by the canonical source are explicitly closed. Existing
validation of external evidence files and external source metadata is retained;
the backup is not a filesystem-wide snapshot.

New snapshots identify the digest as `sqlite-backup-sha256/v1`. That marker and
the frozen target identity participate in the snapshot hash. Both the application
and Hermes report readers recognize the optional fields. Historical snapshots,
stored hashes, report contents, and identifiers are not migrated or rewritten.
Account projections consume the frozen identity for new snapshots.

The old R3.1 account correction entry has two explicit validation contracts:

- New snapshots: compare all report-scoped frozen inputs, including configuration,
  content, evidence, relations, statistics and target identity. Exclude only the
  database-image digest and the snapshot identifiers derived from it, because
  persisting a report or running another task legitimately changes the whole DB.
- Legacy snapshots: retain the original main-file hash comparison, but perform
  raw file reads in a fresh isolated Python process, never the SQLite-owning
  process. This compatibility path does not upgrade the historical hash into a
  WAL-inclusive digest. Mismatch, timeout and unknown formats fail explicitly.

The correction operation still requires the original successful model steps.
Unsupported deterministic reports now fail before creating a new version.
This patch does not extend that legacy operation to unified report templates.

## Verification

Two new regression tests failed on the original code: second-task/log records
became invisible across processes after report freezing, and snapshot inputs were
read from the changing live database. Both pass with the patch.

Local Python 3.12: 160 distinct tests passed across the main regression suite,
correction safety tests and backup/identity tests. Coverage includes same-session
two-round publishing/querying, unified reports, source configuration, geographic
snapshots and report collections. Modified Python files pass AST parsing;
`git diff --check` passes. Independent review found no remaining code blockers.

Linux, using the production Python 3.11.6 / SQLite 3.42.0 interpreter against an
isolated source tree and temporary synthetic databases:

- 14 snapshot, WAL, correction-guard and backup-failure tests passed.
- 86 business tests passed, including same-session two-round publishing/querying,
  unified report combinations and report-reader snapshots.
- 1 additional test passed for frozen account identity during report generation.
- All 10 tested Python files matched the local patch by SHA-256.

A combined cloud test attempt exceeded its 180-second budget. The 101 successful
Linux tests above were subsequently run as bounded groups; do not interpret the
timed-out combined attempt as a full-suite pass. Test-only Python modules were
placed in the diagnostic directory; production dependencies were not modified.

Against the preserved 16:21 database copy, all 32 published reports passed snapshot,
frontend and Hermes graph validation under both baseline and patched code.
All 96 normalized output digests matched between versions.

The 431,050,752-byte preserved database was also backed up by the new helper in
3.41 seconds: `quick_check=ok`, 32 published reports and the second task present,
temporary image removed on exit.

Detailed receipts are in the local untracked artifact (not included in the patch):
`reports/sqlite-report-lock-repair-20261020/linux-validation.json`.
Cloud diagnostic receipts are under:
`/opt/content-audit-diagnostics-20261020-1614-second-run/repair-validation-v1/`.

## Release boundary

No production code, service, configuration or active database was changed during
this repair. Neither local main baseline was moved.

The full R3.1 correction acceptance gap is now closed in synthetic fixtures:
both the new backup-digest format and a genuine legacy snapshot generated by
the unmodified cc82d62 builder pass generation, correction publication and Hermes
loading of both versions. The old report remains unchanged; modifying its source
is rejected without additional versions or generation runs. The new-format test
also adds an unrelated task before correction. Model responses are synthetic;
all structure, semantic and publication validators execute. This is not a claim
that production historical reports were themselves corrected. The focused local
suite now passes 16 tests, with independent review of both complete paths.
Live crawler/provider execution is not part of the synthetic regression suite.

Production recovery remains a separate operation. Re-preserve the current main
file, WAL/SHM files and open deleted file descriptors before stopping writers;
the earlier 16:21 copy must not silently replace later data. Reconcile tasks,
logs, reports and owner/admission state, then validate the chosen recovered image.
Deploy the tested application patch with existing runtime configuration, and only
then perform a controlled restart and business visibility checks. Do not delete
active WAL files or use an unpatched restart as the repair.
