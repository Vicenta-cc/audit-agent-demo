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
