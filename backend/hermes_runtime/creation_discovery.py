"""Creation-only descriptions; preserve native deferred tool discovery."""
from contextlib import contextmanager
from copy import deepcopy
import json

BRIDGE_GUIDANCE = {
    'tool_search': (
        '资源工具选择：新建独立规则/词库才用 create_ruleset_proposal/create_lexicon_edit。'
        '修改已有内容必须用 update_resource_edit；'
        '正式资源先 open_resource_edit 复制指定版本，已有会话稿先 get_resource_edit。'
        '禁止用生成或整稿替换代替局部编辑。'
        ' 仅未知业务工具名时搜索。'
        '返回工具名和用途，不是参数 schema；'
        '下一步直接调用 tool_describe。'
    ),
    'tool_describe': (
        '资源工具选择：新建独立规则/词库才用 create_ruleset_proposal/create_lexicon_edit。'
        '修改已有内容必须用 update_resource_edit；'
        '正式资源先 open_resource_edit 复制指定版本，已有会话稿先 get_resource_edit。'
        '禁止用生成或整稿替换代替局部编辑。'
        ' 直接调用本函数，参数示例 {"name":"update_resource_edit"}。'
        '不要通过 tool_call 调用本函数。'
        '首次使用业务工具且完整参数 schema 未在上下文时，先读取它；'
        '已读过则复用。'
    ),
    'tool_call': (
        '资源工具选择：新建独立规则/词库才用 create_ruleset_proposal/create_lexicon_edit。'
        '修改已有内容必须用 update_resource_edit；'
        '正式资源先 open_resource_edit 复制指定版本，已有会话稿先 get_resource_edit。'
        '禁止用生成或整稿替换代替局部编辑。'
        ' 仅执行业务工具。'
        '修改已有规则或词库（包括临时修改版）先用 open_resource_edit 复制指定来源，再用 update_resource_edit 提交局部改动；'
        '已有会话副本直接编辑。'
        'create_ruleset_proposal 是从零生成，不用于保留原文的修改。'
        '必须同时传 name 和 arguments，例如 {"name":"query_investigation_options","arguments":{"platform":"dy","mode":"search"}}。'
        'name 不得是 tool_search/tool_describe/tool_call。'
        'arguments 必须符合已读取的业务 schema；'
        '从零生成用 generation_request，content 仅原样导入完整正文。'
        '参数报错先核对工具是否适用，不要为了补齐必填正文而把局部编辑改成整稿替换。'
    ),
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
