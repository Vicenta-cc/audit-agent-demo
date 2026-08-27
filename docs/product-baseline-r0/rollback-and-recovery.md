# R0 Rollback and Recovery

## Rollback

The original demo worktree remains unchanged and on
`cd62a96e1e04609d346001be613ec1c2544c1e73`. The R0 candidate is an independent
worktree on `codex/product-baseline-r0`. Rollback is therefore a branch/worktree
disposal or reset of the candidate only; it does not touch the original demo,
MediaCrawler, or Hermes source worktrees.

R0.1 is a separate worktree on `codex/product-baseline-r0-1`, based on
`c3b03689117981ad1a1a67ae62ed54e2e3c585ba`. The MediaCrawler source-only
checkpoint is independently recoverable at commit
`a77d8f4ad99b692641711c8e170c73dc7ebdf627`; neither source worktree is modified
by rollback of the candidate.

No accepted tag is created until every clean-room gate passes.

R0.2.1 is a separate worktree on `codex/product-baseline-r0-2-1`, based on
`2fed1e5187cfee8df2c5063ae78dc6d5bcb46af6`. Roll back this increment by
checking out that base commit or the preserved R0.2 tag in a new worktree.
Do not move the R0.2 tag. The private Hermes transcript table is additive;
R0.2 code ignores it, so no database down-migration is required for rollback.
Worker-local registry entries and per-Session ledgers can be recreated from
the Product Store Session/ReportVersion relationship.

## Recovery

The archive `/Users/ext.wanghongtao6/Documents/Codex/archives/product-baseline-r0-20260826`
contains verified Git bundles for the demo, Hermes worktree refs and
MediaCrawler refs, status/diff manifests, artifact hashes and the Hermes source
manifest. Recovery is performed by `git clone`/`git bundle` into a new worktree,
never by copying credentials or live databases.

Pre-Hermes experiments are recovered from the demo bundle and their recorded
paths, then re-audited against the five approved preservation reasons before any
source integration.

The R0.1 Hermes clean-room acquisition is currently blocked by network failure;
the empty fetch repository and command/error are documented in
`hermes-clean-room-report.md`. Recovery must retry the official URL and exact
commit, never substitute a local Hermes directory or historical hash.

R0.2.1 repeated the official exact-commit install and received an HTTP/2
framing failure. The fallback was the preserved non-editable 0.20.4 wheel with
SHA-256 `b849e1cc9df474ecb121b8f912621eaae1654b619e068f990d15d6320d48b0f8`.
All seven packaged files registered in `HERMES_RUNTIME_MANIFEST.md` matched.
Recovery should still prefer a fresh official exact-commit build when network
access is restored; it must reproduce those hashes before replacing the wheel.
