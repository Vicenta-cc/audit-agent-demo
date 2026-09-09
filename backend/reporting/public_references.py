"""Stable public aliases derived from immutable snapshot order."""

def public_post_and_finding_refs(snapshot):
    return (
        {item.ref: f"post-{index:03d}" for index, item in enumerate(snapshot.posts, 1)},
        {item.ref: f"audit-finding-{index:03d}" for index, item in enumerate(snapshot.findings, 1)},
    )
