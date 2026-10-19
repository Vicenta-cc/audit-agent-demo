# SQLite migration and business rollback rehearsal

This tool operates on **explicit immutable database backups**, not live database directories. It creates a new private directory, never overwrites an existing destination, and does not install a runtime, copy browser profiles, start collection, remove protection state or change production configuration.

## Prepare the copy

Use a stopped or independently captured immutable backup containing all seven files: `audit_index.sqlite3`, `historical_report_demo.sqlite3`, `investigation.sqlite3`, `investigation_creation.sqlite3`, `report-collections.sqlite3`, `report_checkpoints.sqlite3`, and `request_scheduler.sqlite3`. Each online SQLite backup is internally consistent; a coordinated cross-database cutoff additionally requires application quiescence. An existing independently backed-up snapshot does not prove that cross-database cutoff.

A live WAL database must **not** be presented as immutable. The tool rejects journal sidecars, missing files, symlink paths, hardlinked inputs and source/destination overlap. `--immutable-backup` is the caller's explicit assertion that writers cannot change the input. The tool also verifies source file hashes after copying.

```sh
python3 -B deploy/camoufox/recovery.py prepare \
  --source /opt/content-audit-candidate-20261019-e50f82b/production-recovery-normalized-20261019 \
  --destination /opt/content-audit-candidate-20261019-e50f82b/recovery-rehearsal-v1 \
  --immutable-backup
```

The destination is mode `0700`; files are `0600`. A private `recovery-baseline.json` holds original columns and keyed row digests, including duplicate counts. Do not publish this baseline. Public stdout contains table inventories and bounded verification scope, never row values or credentials. A failed copy may leave an incomplete private directory; it has no completed baseline and must not be used for migration.

## Apply the pinned schema to copies and verify

Run the selected release's existing schema constructors separately, with every database/config path directed at the rehearsal directory. Disable implicit dotenv loading and ensure required auth/creation paths refer to the same copied `investigation_creation.sqlite3` when that file contains `app_users`/`login_sessions`. Do not split an existing collocated auth database into an empty new auth file. Do not import application modules with production configuration: several modules construct global stores at import time.

The tool intentionally does not execute a shell callback or discover environment-dependent application constructors. The operator's explicit copy-only initialization script and exact release commit must be recorded as separate rehearsal evidence.

```sh
python3 -B deploy/camoufox/recovery.py verify \
  --directory /opt/content-audit-candidate-20261019-e50f82b/recovery-rehearsal-v1
```

Verification permits added schema and new rows but fails on any missing or modified original-column row, including ownership, password/token hashes, encrypted auth, proxy bindings and risk state. A migration that intentionally rewrites original data needs explicit review; a matching row count is insufficient. New columns are not part of the old baseline. The verification does not prove new-column semantics or application-level behavior.

## Rehearse rollback after new writes

Never restore the pre-upgrade backup over newly accepted business writes. Pause collection and all writers, then take a **fresh** complete post-upgrade snapshot. Use that immutable snapshot as the rollback source, preserving new columns, tables and rows:

```sh
python3 -B deploy/camoufox/recovery.py business-rollback \
  --source /private/post-upgrade-quiesced-backup \
  --destination /private/new-business-rollback-copy \
  --immutable-backup --business-only-maintenance
python3 -B deploy/camoufox/recovery.py verify \
  --directory /private/new-business-rollback-copy
```

Run real old-version **business read** probes (login/permissions, reports, sessions and posts) against that copy while collection/login mutation remains paused, and retain exact old/new commits and results. The tool reports `old_binary_verified: false` until separate real evidence exists; its synthetic regression test proves only the old-column SQLite read pattern. It never changes engine metadata, reactivates accounts or invokes an old crawler as fallback. Old business compatibility must be established separately from browser-engine compatibility.

## Additional cutover requirements

Database row preservation alone does not migrate a browser login. Profile migration needs stopped source browsers, a private validated copy of profile assets, exact engine identity and credential configuration, and runtime verification through the protected login/route paths. No running profile copy or logged-in status fabrication is permitted. Retain auth, role, ownership, encrypted state, risk pauses, proxy/route bindings and exit freshness semantics; a preserved old cookie does not establish a valid new-engine session. Where incompatible, the protected account requires normal login rather than automatic old-engine fallback.

Keep collection paused on the business rollback runtime until a separately verified supported crawler configuration exists. Do not mark this rehearsal as a production rollback, profile migration or cutover success. Candidate functional testing and the explicit security review status remain separate evidence.

## 2026-10-19 actual copy rehearsal

The original seven production backups were retained byte-for-byte. Two leftover sidecars on `report_checkpoints.sqlite3` correctly blocked the strict tool. A separate private scratch copy retained those journals; SQLite backup then produced `production-recovery-normalized-20261019`. No journal was deleted from the source or ignored with immutable mode.

The pinned candidate `eb6f2539304e1b9eaef2763b961e3e43c8b05ca7` initialized the real production-data copies in a network-disabled namespace. Verification preserved every original-column row across all seven databases, including 10 crawler accounts, 8 application users, 159 login sessions and 2 private account owner bindings. Auth remained collocated with the creation database. One completed, non-collecting rehearsal job was added only to the copy to exercise post-upgrade write preservation.

Evidence lives outside source artifacts under candidate `functional-test/receipts/production-backup-normalization.json`, `production-schema-rehearsal.json` and `business-rollback-rehearsal.json`. Scope and any later failures must be read from those receipts. This is not a coordinated live production cutoff, browser Profile migration, actual account reauthentication, full old application compatibility or a completed cutover. The old collector does not enforce new migration/route fields and must not run against upgraded account data as fallback. A business rollback keeps collection paused and preserves a fresh post-upgrade snapshot.
