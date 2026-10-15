"""Resource lifecycle tools; generation uses an isolated author inside these tools."""
from .contracts import ReadResourceInput, CreateLexiconInput, OpenResourceInput, GetEditInput, UpdateEditInput, SaveResourceInput, GetSaveInput
from .snapshot_refs import tool_view
from .keyword_profiles import KEYWORD_PROFILE_CATALOG_DESCRIPTION
from .recall_prompt import SEARCH_TERMS_MAX
from .authoring_guidance import (
    RESOURCE_EDIT_GUIDANCE, RULESET_AUTHORING_GUIDANCE,
)

RESOURCE_TOOL_INPUTS = {
    'read_resource': ReadResourceInput,
    'create_lexicon_edit': CreateLexiconInput,
    'open_resource_edit': OpenResourceInput,
    'get_resource_edit': GetEditInput,
    'update_resource_edit': UpdateEditInput,
    'save_resource': SaveResourceInput,
    'get_resource_save': GetSaveInput,
}
RESOURCE_MUTATIONS = frozenset({'create_lexicon_edit', 'open_resource_edit', 'update_resource_edit', 'save_resource'})
RESOURCE_DESCRIPTIONS = {
    'read_resource': '查询或读取后台正式规则 ruleset 或词库 lexicon。resource_id 为空时按 query 分页检索；指定真实 ID 时返回完整内容和版本。有已发布版本的正式规则和正式词库返回 resource_ref，分别用于 configure_investigation_draft 的 ruleset_ref/lexicon_ref，引用绑定当前用户读取的精确版本，由后端校验，不需要填写 hash。规则 resource_ref 为空表示当前正文尚无相同的已发布版本，应打开编辑副本并按展示采用流程处理，不能猜引用。引用过期时先重新读取并核对差异，不直接采用新版。只读，不创建编辑副本、任务或保存。任务资源选择仍可用 query_investigation_options。',
    'open_resource_edit': '用户要求编辑已存在后台资源时，按真实 kind/resource_id 读取完整内容并建立当前会话副本，返回 edit_id、精确版本和来源。不会修改正式资源、采用资源或启动任务。系统规则仅可另存。规则可传 revision_id 复制指定已发布版本（如某轮冻结配置使用的版本），留空复制当前内容。规则副本仍为原 M3 Proposal，后续采用必须使用其已展示版本。',
    'get_resource_edit': '读取当前会话完整编辑内容、版本、来源、保存回执和实际 search_terms。规则和词库均返回绑定当前会话及版本的 resource_ref；把引用值分别传给 configure_investigation_draft 的 ruleset_ref/lexicon_ref，不传整份 recall_plan，不重抄内容或 hash。编辑后必须使用新引用，旧引用不可用；后台检查规则已展示版本。不能把已保存旧版本误说成当前修改已保存。',
    'update_resource_edit': '按 edit_id/expected_version 局部修改，changes 是操作数组。词库用 upsert_entry（target_id 为已有词条 ID，新增省略 target_id）、remove_entry（删变体只删该条，删主词会连同其变体删除，须在用户明确删除范围内）；规则用 update_rule/remove_rule（target_id 为 rule_id）、add_rule（target_id 为 category_id）。豁免按条操作：add_exemption 的 values 提供 exemption_id/name/condition，省略 target_id；update_exemption 的 target_id 为豁免 ID，values 仅传改动的 name/condition/enabled；remove_exemption 只传 target_id。默认通用豁免；规则内豁免额外传 rule_id。不要抄写整个豁免列表；set_exemptions 仅兼容明确要求整列表替换的操作；元数据用 set_metadata：词库允许 title（名称）、description（整库说明，最多2000字）、risk_label（风险标签）；规则允许 name、domain、audit_goal。词库说明不能写入词条 note。values 只包含需要修改的字段。变体归属使用固定 ID，不因改名而丢失。相同term/platform/match_type不可重复，主词与变体也一样；停用不消除重名。改名与保留要求冲突时说明冲突并等待用户选择，不能自行删除、改名或改变其他词条属性来绕过校验。更新校验失败不会提交这次修改；失败不授予删词权限。用户已明确选择方案后按所选范围修改，无需再次确认。不会保存、采用或启动。版本冲突先读并核对，不强行更新预期版本。',
    'save_resource': '用户明确要求保存时，把 edit_id/expected_version 的精确内容正式保存并发布，一次动作完成。mode=new 保存新生成资源，update 保存回可编辑原资源，copy 另存。operation_id 标识这一次逻辑保存，重试保持同一个值；结果未知先 get_resource_save。规则与词库分别调用，允许同轮保存多个资源。返回正式保存版本的 resource_ref；若用户要求草案换用已保存版本，把该引用传给 configure_investigation_draft。旧编辑稿引用仍指向原编辑版本，不自动改指正式资源。保存不等于采用、绑定 Draft 或启动；任务可以继续使用未保存内容。',
    'get_resource_save': '按原 operation_id 查询实际正式保存回执。用于响应丢失后的恢复，避免更换 ID 重复创建。只读，not_found 表示未查到已提交保存。',
}
RESOURCE_DESCRIPTIONS['read_resource'] = (
    '只读正式资源。必填 kind=ruleset|lexicon；resource_id 用资源ID，不用版本ID。\n'
) + RESOURCE_DESCRIPTIONS['read_resource']

# Native deferred discovery delivers these only when the tool is described.
# A mixed resource editor retains both sets of guidance; applicability is explicit.
RESOURCE_DESCRIPTIONS["update_resource_edit"] += (
    RESOURCE_EDIT_GUIDANCE + RULESET_AUTHORING_GUIDANCE
)
RESOURCE_DESCRIPTIONS["open_resource_edit"] += RESOURCE_EDIT_GUIDANCE
RESOURCE_DESCRIPTIONS['read_resource'] += ' 规则返回 published_versions：各正式版本附可直接传入 open_resource_edit 的 open_arguments；顶层 open_arguments 读取当前内容，不代表历史版本。'
RESOURCE_DESCRIPTIONS['open_resource_edit'] = (
    '仅复制已有资源供局部编辑。完整参数示例 {"kind":"ruleset","resource_id":"读取到的资源ID","published_version":2}；'
    '也可直接复制 read_resource.published_versions 中目标版本的 open_arguments。不要传 version，不把版本ID放进 resource_id。'
    '返回 update_arguments，后续原样使用其 edit_id/expected_version，再加 changes。'
) + RESOURCE_DESCRIPTIONS['open_resource_edit']
RESOURCE_DESCRIPTIONS['update_resource_edit'] = (
    '已有规则/词库局部修改使用本工具。复制最新结果的 update_arguments，再加 changes。'
    '改风险示例 {"operation":"update_rule","target_id":"原规则ID","values":{"suggested_risk_level":"high"}}；'
    '新增通用豁免示例 {"operation":"add_exemption","values":{"exemption_id":"新ID","name":"名称","condition":"用户原文"}}。'
) + RESOURCE_DESCRIPTIONS['update_resource_edit']
RESOURCE_DESCRIPTIONS["create_lexicon_edit"] = (
    "仅从零创建独立词库；新增/修改已有词条、变体、名称用 update_resource_edit，不用本工具重生成。"
    "生成临时搜索词或完整黑话库的统一入口。新生成传 generation_request：objective、platform、"
    "requirements，用户明确指定数量才传 requested_count，禁止扩展的原文词表传 exact_terms。"
    "keyword_profile " + KEYWORD_PROFILE_CATALOG_DESCRIPTION +
    "模板示例由后端加载，不必自行抄写。"
    "专用生成步骤携带完整原有质量指导，返回经校验的会话编辑稿、search_terms 和 resource_ref recall_plan。"
    "配置草案把 resource_ref 传入 configure_investigation_draft.lexicon_ref，不重抄词库内容；临时使用无需正式保存。"
    f"默认整组1至{SEARCH_TERMS_MAX}个实际词，可靠候选不足不凑数；用户指定数量/原文优先，词库不截断，但每个任务只搜索前{SEARCH_TERMS_MAX}个。"
    "结果含 search_terms_cap 时，按其 message 告知用户。"
    "仅原样导入用户提供的完整结构化内容使用 content，不调用生成模型。两种输入只能选一种。"
    "保存当前 Draft 词库请用 save_draft_lexicon，只传草案引用和版本，不重抄 content。"
    "content.entries 含稳定 id、term、kind(main/variant/tag)、parent_id、enabled；变体指向主词。"
    "这是同一编辑稿生命周期，不是临时/正式两套词库。不正式保存、不采用、不启动。"
    "生成失败时不得改走 content 编造成功；如实报告错误，保留此前成功操作。"
)

RESOURCE_EDIT_TOOL_GUIDANCE = {
    'create_lexicon_edit': (
        '【仅新建独立词库】最佳用途：生成全新的词库或搜索词集合，或原样导入已给定的完整新词库。'
        '【不允许用于修改已有词库】给已有词库增删关键词、增加变体、改名、改风险等级或停用，必须用 open_resource_edit/get_resource_edit + update_resource_edit。'
    ),
    'open_resource_edit': (
        '【正式资源局部修改的首选入口】按用户指定的真实资源及版本建立原样副本，然后必须用 update_resource_edit 只改所需字段。'
        '临时修改也走此入口，不调用生成工具。'
        '已有会话编辑稿无需再复制，直接 get_resource_edit。'
    ),
    'get_resource_edit': (
        '只读当前编辑稿，仅传 edit_id；不传 expected_version。\n'
        '原样复制 read_arguments。需要查看或继续修改已存在的编辑稿时用本工具读取真实内容和版本；'
        '后续局部修改必须用 update_resource_edit，不重新生成。'
    ),
    'update_resource_edit': (
        '【局部修改必选】已有规则或词库的新增、删除、修改条目、条件、风险等级、豁免与元数据，必须使用本工具。'
        '只提交变化的字段，不抄整份内容。'
        '豁免使用 add_exemption/update_exemption/remove_exemption。'
        '不要改走生成工具或 update_ruleset_proposal。'
    ),
}
for _name, _guidance in RESOURCE_EDIT_TOOL_GUIDANCE.items():
    RESOURCE_DESCRIPTIONS[_name] = _guidance + "\n" + RESOURCE_DESCRIPTIONS[_name]

RESOURCE_PROMPT = """
规则库与黑话库的创建、编辑原则：
1. 新建独立资源：新规则用 create_ruleset_proposal，新词库用 create_lexicon_edit，仅传 generation_request；原样导入用户给定的完整结构化正文用 import_resource(kind, content)。
2. 修改已有资源：先定位目标及版本；正式资源用 open_resource_edit 复制，已有会话编辑稿用 get_resource_edit 读取。再用 update_resource_edit，只提交指定字段或条目变更，保留其余内容、编号和顺序。豁免按条增删改。
3. “新增一条”“生成一个变体”“做临时修改版”只要针对已有资源，都属于编辑，不以整套生成替代。临时、不保存只决定是否正式保存，不决定使用生成工具。
4. 明确要求整体重做时，区分独立新资源与已有稿整体替换；后者先读取原稿，按工具契约用 update_ruleset_proposal，不把局部修改扩大为整体替换。
5. 目标或意图确实无法确定才澄清；已有上下文足够时直接执行。工具参数与操作结果以实际契约和回执为准。

展示资源名称、版本、保存状态和实际业务内容，隐藏内部 ID、哈希及机器字段。
用户要求可用资源时，通过上述工具生成或修改会话编辑稿，再展示真实结果；纯讨论、构思、示例可以仅给文字。要求规则和词库两者时分别完成，不能只完成一个。
会话编辑稿可临时使用，不等于正式保存。明确要求保存才用 save_resource；一次保存即发布，无需另问发布。保存已有稿引用 edit_id/expected_version，不重生成。用户同时授权生成并保存可同轮完成；保存不等于采用或启动。

词库主词用于主题归类；启用主词有启用变体时搜索变体，否则搜索主词。停用主词整组不参与；标签不参与搜索。新建完整黑话库默认生成变体。
用户明确指定搜索词且未要求扩展时，实际搜索词只包含指定词。可加不参与搜索的主题主词归类，不自行扩展召回词或标签；明确授权扩展时先说明范围。采集规模沿用系统配置，不能把采集量当作资源条数。
新生成时，generation_request 完整传递主题、平台及约束；原文词表用 exact_terms，明确指定资源条数才用 requested_count。专用生成步骤负责质量指导与结构化生成；import_resource 仅用于已有完整结构化内容的原样导入，不用于绕过生成失败。失败回执 retryable=true 才可本轮纠正一次；同一调用身份只重放回执，不重新生成。

编辑操作、参数、冲突与删除范围要求见 open_resource_edit / update_resource_edit 的完整说明，调用前读取。版本冲突先读取并核对，不强行写入；明确保留的内容不为消除冲突而改动。
用户要任务使用资源时，沿用展示采用、Draft 预览、明确启动流程。规则采用须遵循上文的后续回合确认；保存不代表采用。已确认 Draft 不修改，按新调查流程准备新 Draft，不修改既有任务。
规则和词库通过 configure_investigation_draft 使用统一版本引用：ruleset_ref/lexicon_ref。优先直接复用生成、读取或编辑成功回执中的 resource_ref；旧临时稿缺引用才用 get_resource_edit，正式资源用 read_resource。正式保存后可用 save_resource/get_resource_save 返回的 resource_ref，不自行组装哈希、补入主题主词或标签。新建临时词库 source_lexicon_ids=[]，edit_id 不能当正式词库 ID。查询选定资源后读取其真实引用。词库配置错误不通过重新生成已采用规则绕过。
保存当前 Draft 词库遵循上文 save_draft_lexicon 约束。一次回合可处理多个资源，每个保存用独立 operation_id 和真实回执。保存刚才的资源依据回执定位 edit_id/expected_version，有歧义才澄清。
参数错误最多纠正一次且不改变用户意图；业务冲突需用户选择。写入结果未知查询原回执，保存用原 operation_id 查询 get_resource_save。没有成功回执不能宣称完成。
生成拦截、截断或失败时，本轮不换说法、content 或模型重试。保留已成功步骤，仅继续未完成且获授权的部分。
"""



RESOURCE_DESCRIPTIONS['save_resource'] += ' 用户指定保存名称时传 name，保存与命名原子完成；省略才沿用稿内名称。不会改名原编辑稿或冻结任务。'
RESOURCE_DESCRIPTIONS['open_resource_edit'] += ' 编辑或另存某轮实际使用的版本，可选 read_session_resource 返回的 open_arguments（kind/session_resource_key）；按冻结正文复制，不读取最新版替代。仅查看请直接读取，不必打开副本。'
RESOURCE_DESCRIPTIONS['get_resource_edit'] += ' 本工具只返回当前稿；历史版本通过 list_session_resources / read_session_resource 读取，不能由当前稿结果断言历史快照不存在。item_index 按完整内容展示顺序给出一基 position 与 target_id 对应关系；按当前目标版本定位，不从相邻资源沿用条目 ID。'
RESOURCE_DESCRIPTIONS['get_resource_edit'] += ' 旧临时规则未返回引用时，直接传 edit_id=真实 proposal_id 即可取得 resource_ref，无需先转换、复制或正式保存。'
RESOURCE_DESCRIPTIONS['update_resource_edit'] += ' 回执 applied_diff 是实际持久化前后差异，解释操作时以它及返回的目标资源为准。'


def execute_resource(service, name, parsed, *, session_id, principal):
    args = parsed.model_dump(mode='json')
    ctx = dict(session_id=session_id, principal=principal)
    if name == 'read_resource':
        return tool_view(service.read(**args, principal=principal))
    if name == 'create_lexicon_edit':
        return tool_view(service.create_lexicon(content=args['content'], **ctx))
    if name == 'open_resource_edit':
        return tool_view(service.open(**args, **ctx))
    if name == 'get_resource_edit':
        return tool_view(service.get_edit(**args, **ctx))
    if name == 'update_resource_edit':
        return tool_view(service.update(**args, **ctx))
    if name == 'save_resource':
        from .ruleset_refs import saved_view
        return tool_view(saved_view(service, service.save(**args, **ctx), principal=principal))
    from .ruleset_refs import saved_view
    return tool_view(saved_view(service, service.get_save(**args, **ctx), principal=principal))
