# Camoufox release tooling

Candidate only. G0 security review is unresolved; these files do not approve deployment.
No script contacts production, deletes containers/profiles, restarts units, or falls back to Cloak.
The current worktrees and production deployment remain separate: build only an explicitly
reviewed, committed App/Crawler pair. Production stays systemd (8298/3298, relay13298).
Docker is an isolated Linux delivery rehearsal, not an unrequested deployment conversion.
The current operator instruction prohibits local Mac Docker/browser-window testing; do not
start Docker Desktop or open local browser windows. Run Linux rehearsal only in the approved
isolated Linux environment. Linux headed private Xvfb remains required.

## Configuration contract

`control.py validate runtime.json` validates exact manifest keys. Unknown keys and environment
overlays are rejected. All artifact paths must be absolute and SHA256 pinned. `hash PATH`
computes a file hash or a deterministic relative-path/content tree hash; symlinks are rejected.
Artifact hashes cover App, Crawler, separate frontend hotfix **dist**, runtime-support, both
fully transitive hashed requirements locks, offline wheelhouse, browser archive, actual
executable and immutable browser/model assets. Source commits include App, Crawler and
frontend hotfix independently. A manifest is input created after review, not a build receipt.
Do not hash an active mutable checkout/cache; first freeze release snapshots.

The strict schema is executable in `control.py` (`TOP_KEYS`, `RUNTIME_KEYS`, `ARTIFACT_KEYS`,
`validate`); a complete structural fixture is in `tests/test_control.py:manifest`.
The fixture contains invented digests and is not deployable. No example claims real asset hashes.

Runtime: `state_root` owns `profiles`, cache, home and temporary files, mode0700, dedicated
nonroot UID/GID. `logical_profile_root` retains existing account locks. Business account,
owner and scheduler DB paths stay outside child state and unreadable by the child. App remains
the original owner and mediates authority FD operations. No production DB copy is used to
make preflight pass. Profile metadata (`profile.json`, `launch-identity.json`) must agree with
the engine, Linux OS, browser version and persisted fingerprint hash.

New runtime-support clears inherited process environment and accepts only manifest allowlisted
App settings. It does not read the old environment.json/secrets.env or inject browser145.
**Operator must add every required business integration configuration to the reviewed allowlist**
before releasing; do not blindly inherit the legacy process environment. Secrets must not be put
in public manifests or receipts. The App/browser child boundary applies its separate, smaller
allowlist; App environment is not a browser-child environment.

App Settings also refuses checkout `.env` loading whenever
`CAMOUFOX_RELEASE_MANIFEST` is present. Both API and worker install this marker
before importing Settings, independently of python-dotenv's version-specific
disable flag. Missing business configuration must be supplied by reviewed
manifest/private-file keys, never silently filled from a packaged `.env`.
Unmanaged development keeps its existing dotenv behavior; restricted browser
children retain their separate no-dotenv boundary. See the offline entrypoint
regression in `tests/test_release_dotenv_boundary.py` and the current
[deployment readiness inventory](../../docs/runtime/DEPLOYMENT-READINESS-20261019.md).

Maintenance contract: `CRAWLER_BROWSER_MAINTENANCE=true` blocks new Douyin login, crawl
and recovery admissions while business APIs/reports can start. API/worker entrypoints consume
the same manifest. `APP_AUTH_DB` is fixed to `runtime.owner_db`, archive root is pinned by
`CRAWLER_CAMOUFOX_ARCHIVE_ROOT`, and explicit executable plus SHA256 is provided under
both App and child variable names. The engine validates installed version metadata and the
actual executable hash before starting. A candidate manifest always starts with maintenance enabled. This is not
proof that the old pre-upgrade App understands maintenance: during first migration the operator
must stop the actual formal units and prove quiescence before backup/migration.

## Offline construction and rehearsal

1. Close G0 with asset-matched security review; prepare reviewed source snapshots, standalone
   hashed locks and wheelhouse. Download/extract browser and model assets in a build environment,
   record actual URLs/SHA256. Do not run `camoufox fetch` on production.
2. `python deploy/camoufox/build.py runtime.json /NEW/absolute/build-root` creates fresh
   App/Crawler venvs; pip uses `--no-index --require-hashes --only-binary=:all:` and `pip check`.
   It refuses an existing destination and writes build-receipt only after both installs succeed.
   Failed builds remain incomplete for inspection; they have no success receipt.
3. Package venvs at their final absolute prefix (venv scripts are not relocatable), browser,
   assets and this deploy directory in `payload/`. Docker base is an explicitly reviewed
   digest-pinned Debian/Ubuntu image already containing Python3.11, Xvfb, fontconfig/fonts,
   GTK3/DBus-GLib/NSS/X11/ALSA/GBM and trusted CA certificates. No apt/pip/fetch is in Dockerfile.
4. Build `docker build --build-arg BASE_IMAGE=IMAGE@sha256:ACTUAL_DIGEST ...`; require linux/amd64.
   Launch isolated: `docker run --rm --network none --read-only --shm-size=1g --pids-limit=256
   --memory=4g --cpus=2 --tmpfs /tmp:rw,noexec,nosuid,size=256m IMAGE` with a dedicated private
   `/state` volume owned by the runtime UID; no production mounts, ports or queue/DB access.
   `/state/home/.camoufox` must exist before launch. UID/GID is10001 unless an operator pins another.
5. `control.py owner-preflight runtime.json` runs as App owner and checks all source/artifact
   hashes plus restricted parent environment files. `control.py preflight runtime.json` runs as
   the exact browser UID and checks only browser binary/assets/runtime-support hashes, executable,
   ldd, private state, unreadable DBs, read-only sources, display binary, FD>=4096, disk>=3GiB,
   shm>=1GiB, explicit staging allowlist/port conflicts and no sandbox-disable env. It does not
   claim sandbox proof. `smoke.py runtime.json --receipt /state/smoke.json` launches a private
   Xvfb, headed browser, local data fixture and real scroll, checks effective content sandbox,
   closes browser then Xvfb. Network-isolated execution proves no runtime downloads.
6. Run the same checks under the actual systemd child boundary; complete plan G1–G10 including
   repeated profile identity, QR/login, route agreement, cancellation and four-hour scroll run.
   This short smoke is not the full capacity/long-run test.

SDK0.5.8 / Playwright1.62.0 are fixed. beta39 is a prerelease candidate for the Linux hanging
fix; beta36 remains a tested local baseline, not a production fallback. Security evidence must
match browser archive SHA and contain reviewer/time, SDK compatibility and each CVE's
patched/non-applicable evidence. An unresolved or missing G0 file stops cutover first; there
is no `--force` or default bypass.

## Cutover operator procedure

- Inventory and stage per-account migration; preserve IDs, permissions, original safety/DBs,
  stable locks and proxy bindings. Obtain maintenance window and archive/backup space.
- Stop receiving work using the rehearsed external entrypoint/old-runtime shutdown; stop formal
  API/worker units and prove all browser/node/Xvfb/process groups ended. Preserve interrupted task
  records; do not delete locks merely because a launcher ended.
- Back up all business/owner/scheduler/task databases consistently via SQLite backup plus integrity
  checks in the stopped-write window, restricted keys/configs and closed profiles. Use the separate
  migration service for idempotent profile archive/create and admission ledger. Do not import old
  Chromium cookies or reuse Chromium directories in Firefox.
- Prepare quiescence, backup, migration, Linux smoke and Docker smoke evidence JSONs. Each has
  `passed:true` and exact `release_id`; cutover receipt pins every evidence file's actual SHA256,
  manifest canonical SHA256, operator and `maintenance_verified:true`. Evidence must be collected
  by its real runner; this tool does not fabricate it or sign an unrun test.
- `control.py cutover candidate.json --target /EXISTING/runtime.json --receipt cutover.json`
  revalidates G0, source commits, all artifact hashes and receipts before fsync+atomic manifest
  replacement. It intentionally does not restart services. Operator starts the same formal units,
  retaining relay target and separate frontend hotfix dist, checks runtime identity and business
  health while Douyin remains paused.
- Authorised owner reauthenticates each original account and verifies original platform identity,
  stable fingerprint and route. Only committed migration records and original safety policy permit
  restoration. `maintenance.py ... disable --receipt ...` requires an explicit release/health/
  migration/operator receipt; restarting and verifying all admissions is still required.
- Failed first cutover leaves collection paused; repair Camoufox or keep business-only maintenance.
  No Cloak fallback or whole-DB overwrite. Old runtime components are cleaned only after verified
  migration inventory and pilot health; this tooling never deletes them. Old containers remain
  stopped pending the user's separate approval to delete them.

## App-owner secrets and stopped-write evidence

`parent_env_files` contains only `{path, sha256, expected_uid, reviewed_allowlist}` references
to restricted JSON objects. Values remain outside manifests/receipts. Files must be regular,
non-symlink, exactly0600, owned by the expected App owner; opening is no-follow, digest checked,
keys are exactly the reviewed allowlist, and duplicate JSON keys/overlaps are rejected. This
allows the App's real business/authentication integrations to keep their settings, without
passing inherited private environment to the browser child. Review and convert the formal
`environment.json` and `secrets.env` into these private JSON files before release. Runtime/browser
controlled variables cannot be overridden by legacy values. UID/GID become `CRAWLER_CHILD_UID`
and `CRAWLER_CHILD_GID`; the SDK browser version is also supplied as
`MEDIACRAWLER_CAMOUFOX_BROWSER_VERSION`. Do not share private JSON files with browser UID.

`quiescence_scope` pins explicit formal `.service` unit names and old-runtime process roots.
Run `quiescence.py capture MANIFEST RECEIPT` locally on the Linux host as OS administrator.
It proves loaded units are inactive/dead with MainPID0, their cgroups have no remaining PIDs,
and `/proc` has no matching executable/cwd/command under the scoped roots. Receipt binds
host boot ID, exact unit-definition hashes, last-start identities, manifest hash and timestamp.
`verify_receipt` accepts evidence no older than300seconds and rechecks live state. It makes no
claim that the old deployed App recognises a new migration fence; formal units must be stopped.
Migration CLI consumes this receipt plus exact account/owner/scheduler/logical/state/archive
paths from the same manifest. A `passed:true` object alone is rejected. `archive_root` is
App-owner storage outside browser-child state, not a browser-writable backup location.

Child preflight deliberately does not run git or open App secret files. It verifies those
files/DBs/archive are unreadable to the browser UID, then validates only child-required
immutable assets. Owner preflight handles private file contents and full release source checks;
neither relaxes file permissions to make child validation succeed.

The private Linux Profile binding includes **schema, engine, account_key, version, os,
generation, display_mode**. Generation is mandatory and Linux must be `headed`; an existing
Mac/headless Profile is not a publishable Linux Profile. The deployment contract test imports
the actual Crawler initializer and checks its written identity, preventing independent stale
fixtures from silently accepting a different schema.

The frontend preserves the existing login WebSocket only at the exact login-session stream
path, forwarding Cookie, Origin and the original one-use query ticket to backend authorization.
It forwards bounded 401/403 responses, caps the handshake/error body at64KiB, uses64KiB tunnel
chunks and a10second stalled-write timeout, and closes both sides on disconnect. Query strings
are redacted from access and error logs. Real loopback tests cover upgrade, both directions,
auth denial, disconnect, invalid paths/body and backpressure without opening a browser.

For nonroot crawler output, App creates one `ChildOutputSpool` and binds its actual newly
launched process-group ID with `bind_process_group(pid)` immediately after `Popen`. After the
entire group has been proven gone: `sync_final()` safely hands off files, then `cleanup()`
removes only that run's spool and staged inputs. Cleanup checks original path/inode/owner
identities and rejects links or special files; it never recursively deletes the state root.
`cleanup(failed=True)` still requires a drained bound group and retains only a small failure
receipt instead of duplicate media; parent-owned task stdout/stderr remain available.

## Login V2 must be explicitly deployable

Camoufox release environment always sets `CRAWLER_LOGIN_V2_ENABLED=true`; neither public
manifest overlays nor reviewed private files may disable it. V1 is not a fallback. The App
configuration default is unchanged; these release entrypoints supply the required setting.

Specify `CRAWLER_LOGIN_V2_ORIGINS` from **exactly one reviewed source**: either manifest
`environment` or one `parent_env_files` entry's exact reviewed allowlist. Its value is a nonempty
comma-separated list of HTTP(S) origins such as `https://content-audit.guixucloud.cn` or an
explicit staging `http://127.0.0.1:13299`. Paths (including a trailing `/`), query strings,
fragments, credentials, wildcards, empty members and invalid ports are rejected. Do not list
the backend loopback origin as a substitute for the browser's real public frontend origin.
Changes are manifest/private-file-hash reviewed; no inherited `.env` silently fills this value.

The same final merged value is checked by `owner-preflight`, API/worker startup and cutover
before the manifest is replaced. Child preflight still never reads parent-private files; it
can verify a declared private origin source structurally, leaving content validation to the
App owner. A missing/invalid origin is a release error even while maintenance is enabled.

`runtime.json.example` illustrates the complete shape and required production origin. It
contains zero placeholder commits/hashes and a placeholder asset name; **it cannot pass real
artifact/G0 checks and is not a release receipt or approval**. Replace every source, artifact,
path and security evidence value with reviewed measured values before considering deployment.
The security gate remains unresolved, and no production change is authorized by the example.
