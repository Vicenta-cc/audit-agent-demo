"""Task-bound child-owned continuation storage and bounded public receipts."""
import json
import os
import re
from pathlib import Path

from .crawler_child_outputs import _directory
from .crawler_child_runtime import child_environment, child_process_options
from .config import settings


def prepare_continuation(scope_id, query_id, batch_id):
    if not all(isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v)
               for v in (scope_id, query_id, batch_id)):
        raise ValueError('invalid geographic continuation identity')
    child_environment()  # Use the existing provisioned child state boundary.
    options = child_process_options()
    uid, gid = options.get('user', os.getuid()), options.get('group', os.getgid())
    parent_path = Path(settings.crawler_camoufox_state_root).absolute() / 'outputs'
    parent = _directory(parent_path)
    name = 'geographic-' + scope_id
    try:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            created = False
        else:
            created = True
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            if created and os.geteuid() == 0:
                os.fchown(fd, uid, gid)
            st = os.fstat(fd)
            if st.st_uid != uid or st.st_mode & 0o077:
                raise ValueError('geographic continuation directory ownership mismatch')
        finally:
            os.close(fd)
    finally:
        os.close(parent)
    return dict(state_path=str(parent_path / name / 'continuation.sqlite'),
                scope_id=scope_id, query_id=query_id, batch_id=batch_id)


def read_continuation_receipt(save_root, expected):
    receipts = []
    for path in (Path(save_root) / 'douyin' / 'collection_status').glob('*geographic_summary.json'):
        if path.is_symlink() or path.stat().st_size > 16384:
            raise ValueError('unsafe geographic continuation receipt')
        value = json.loads(path.read_text())
        if all(value.get(k) == expected[k] for k in ('scope_id', 'query_id', 'batch_id')):
            receipts.append(value)
    if not receipts or any(r != receipts[0] for r in receipts):
        raise ValueError('missing or conflicting geographic continuation receipt')
    result = receipts[0]
    if result.get('status') != 'complete' or type(result.get('exhausted')) is not bool or type(result.get('count')) is not int or result['count'] < 0:
        raise ValueError('incomplete geographic continuation receipt')
    return result
