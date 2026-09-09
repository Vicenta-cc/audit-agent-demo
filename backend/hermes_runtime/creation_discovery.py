"""Creation-only descriptions; preserve native deferred tool discovery."""
from contextlib import contextmanager
from copy import deepcopy

BRIDGE_GUIDANCE = {
    'tool_search': (
        '仅未知业务工具名时搜索。返回工具名和用途，不是参数 schema；下一步直接调用 tool_describe。'
    ),
    'tool_describe': (
        '直接调用本函数，参数示例 {"name":"create_ruleset_proposal"}。不要通过 tool_call 调用本函数。首次使用业务工具且完整参数 schema '
        '未在上下文时，先读取它；已读过则复用。'
    ),
    'tool_call': (
        '仅执行业务工具。必须同时传 name 和 arguments，例如 '
        '{"name":"query_investigation_options","arguments":{"platform":"dy","mode":"search"}}。name '
        '不得是 tool_search/tool_describe/tool_call。arguments 必须符合已读取的业务 '
        'schema；create_ruleset_proposal 使用 arguments.content，不是 canonical_content。错误后按返回 schema '
        '修正，勿盲目重发。'
    ),
}

@contextmanager
def creation_discovery_guidance(bridge):
    """Use under the product-mode lock and restore shared state even on failure."""
    original = bridge.bridge_tool_schemas
    def schemas(*args, **kwargs):
        result = deepcopy(original(*args, **kwargs))
        for entry in result:
            function = entry["function"]
            guidance = BRIDGE_GUIDANCE.get(function["name"])
            if guidance:
                function["description"] = guidance + " " + function["description"]
        return result
    bridge.bridge_tool_schemas = schemas
    try:
        yield
    finally:
        bridge.bridge_tool_schemas = original
