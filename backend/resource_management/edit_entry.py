"""Explicit, per-turn editing capability. Never inferred from chat or model arguments."""
from contextlib import contextmanager
from contextvars import ContextVar
from pydantic import Field, StrictInt
from .contracts import StrictModel, ResourceError


class EditTarget(StrictModel):
    edit_id: str = Field(min_length=1, max_length=200)
    expected_version: StrictInt = Field(ge=1)


active_edit = ContextVar('resource_edit_entry', default=None)


@contextmanager
def editing_entry(target):
    token = active_edit.set(target)
    try:
        yield
    finally:
        active_edit.reset(token)


def require_edit_operation(target, name, arguments, mutations):
    if not target or name not in mutations:
        return
    if name not in {'open_resource_edit', 'update_resource_edit', 'save_resource'}:
        raise ResourceError('当前入口只编辑已选副本；不能重新生成或整套替换。请读取该副本并提交局部改动。',
                            code='RESOURCE_EDIT_ENTRY_ONLY')
    if name != 'open_resource_edit' and arguments.get('edit_id') != target['edit_id']:
        raise ResourceError('当前编辑入口不能修改其他副本。', code='RESOURCE_EDIT_TARGET_MISMATCH')
    changes = arguments.get('changes', [])
    if not isinstance(changes, list):
        return  # The regular tool schema reports malformed arguments.
    for change in changes:
        if not isinstance(change, dict):
            continue
        if (change.get('operation') == 'set_exemptions'
                or isinstance(change.get('values'), dict) and 'rule_exemptions' in change['values'] and change.get('operation') == 'update_rule'):
            raise ResourceError('当前编辑入口的豁免请按条增删改，不接受整份列表替换。', code='RESOURCE_EDIT_ENTRY_ONLY')


def context_text(target):
    if not target:
        return ''
    return ('\n用户已进入局部编辑入口：edit_id=' + target['edit_id']
            + '，起始版本=' + str(target['expected_version'])
            + '。先读取 get_resource_edit，再用 update_resource_edit 改指定字段；本轮不支持新建或整套替换。')
