"""Creation-only descriptions; preserve native deferred tool discovery."""
from contextlib import contextmanager
from copy import deepcopy
import json

BRIDGE_GUIDANCE = {
    'tool_search': '未知业务工具名时搜索。返回名称和用途；参数定义用 tool_describe 读取。已知工具名可直接读取定义。',
    'tool_describe': '直接调用本函数读取业务工具完整定义，例如 {"name":"update_resource_edit"}；不要通过 tool_call 调用。已在上下文的定义可复用。',
    'tool_call': ('按已读取的业务定义调用，必须同时传 name 和 arguments。name 是业务工具名，不是 tool_search/tool_describe/tool_call。'
                  '新建、局部编辑、整稿替换、保存和采用是不同操作，按当前用户要求选择。'
                  '读取问题使用只读工具；修改前定位真实资源和版本，已有编辑稿可继续使用。'
                  '参数错误核对定义，不通过生成整稿替代局部修改。'),
}


@contextmanager
def creation_discovery_guidance(bridge):
    """Use under the product-mode lock and restore shared state even on failure."""
    original = bridge.bridge_tool_schemas
    original_validate = bridge.validate_deferred_call_args
    def validate(name, arguments):
        from backend.resource_management.tool_recovery import argument_error
        error = argument_error(name, arguments)
        if not error:
            return original_validate(name, arguments)
        # Hermes' executor reads only the flat error and parameters fields here.
        # Keep recovery in that visible message, not in a nested service envelope.
        details = error['error']['details']
        message = details['recovery']
        parameters = details['parameters']
        if details.get('alternative_tool'):
            message += ' 以下参数属于 ' + details['alternative_tool'] + '；确需完整替换时用 tool_describe 读取原工具契约。'
            parameters = details['alternative_parameters']
        if details.get('read_current_edit'):
            message += ' 读取当前副本：' + json.dumps(details['read_current_edit'], ensure_ascii=False)
        return json.dumps({'error': message, 'parameters': parameters}, ensure_ascii=False)
    def schemas(*args, **kwargs):
        result = deepcopy(original(*args, **kwargs))
        for entry in result:
            function = entry["function"]
            guidance = BRIDGE_GUIDANCE.get(function["name"])
            from backend.resource_management.edit_entry import active_edit, context_text
            if active_edit.get() and function["name"] == 'tool_call':
                guidance = context_text(active_edit.get()) + ' open_resource_edit 只返回这份已选副本。'
            if guidance:
                function["description"] = guidance + " " + function["description"]
        return result
    bridge.bridge_tool_schemas = schemas
    bridge.validate_deferred_call_args = validate
    try:
        yield
    finally:
        bridge.bridge_tool_schemas = original
        bridge.validate_deferred_call_args = original_validate
