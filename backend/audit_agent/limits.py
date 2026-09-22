"""Stable compatibility limits for frozen investigation task snapshots."""


# This is a storage/execution compatibility boundary, not the current product
# setting. Lowering INVESTIGATION_MAX_POSTS later must not make an already
# confirmed task with a larger frozen snapshot impossible to resume.
MAX_SUPPORTED_INVESTIGATION_POSTS = 30
