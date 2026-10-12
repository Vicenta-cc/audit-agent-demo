"""Contract-level recovery. Never rewrite arguments or infer a user's intent."""
from pydantic import ValidationError


def argument_error(name, arguments):
    if name not in {'open_resource_edit', 'update_resource_edit', 'update_ruleset_proposal'}:
        return None
    from backend.investigation_creation.tools import M3_TOOL_INPUTS
    model = M3_TOOL_INPUTS[name]
    try:
        model.model_validate(arguments)
        return None
    except ValidationError as exc:
        details = {
            'mutation_applied': False, 'receipt_created': False,
            'validation_errors': exc.errors(include_url=False, include_context=False, include_input=False),
            'parameters': model.model_json_schema(),
        }
    if name == 'update_ruleset_proposal':
        # Invalid full replacement must not become an instruction to invent a full body.
        details.update(
            recovery='本次未修改。此工具仅接受用户提供的完整替换正文。若请求是局部修改，不要为通过校验而抄写整套规则；使用 update_resource_edit，只提交变化。若确为完整替换，按原请求修正参数。',
            alternative_tool='update_resource_edit',
            alternative_parameters=M3_TOOL_INPUTS['update_resource_edit'].model_json_schema(),
        )
        if isinstance(arguments.get('proposal_id'), str):
            details['read_current_edit'] = {'name': 'get_resource_edit', 'arguments': {'edit_id': arguments['proposal_id']}}
    elif name == 'open_resource_edit':
        details['recovery'] = '未建立副本。kind 必填；resource_id 是资源编号，不是版本编号。复制 read_resource 的 open_arguments；指定 v2 用 published_version=2 或精确 revision_id，不使用 version。无法定位时先读取资源，不回退最新版。'
    else:
        details['recovery'] = '本次未修改。使用读取结果 update_arguments，加 changes 操作数组。改风险用 update_rule/target_id/values；增豁免用 add_exemption/values，values 内是 exemption_id/name/condition；无 set_rule、update_content、scope 或 exemption 参数。仅修正原请求，不改成整稿替换。'
    return {'status': 'error', 'error': {'code': 'INVALID_TOOL_ARGUMENTS', 'message': '参数不符合工具契约，本次未执行。', 'details': details}}
