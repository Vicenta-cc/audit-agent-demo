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
    'read_resource': '查询或读取后台正式规则 ruleset 或词库 lexicon。resource_id 为空时按 query 分页检索；指定真实 ID 时返回完整内容和版本。正式词库返回 resource_ref 和可直接用于 Draft 的 recall_plan，引用绑定当前用户读取的精确版本，由后端校验，不需要填写 hash。引用过期时先重新读取并核对差异，不直接采用新版。只读，不创建编辑副本、任务或保存。任务资源选择仍可用 query_investigation_options。',
    'open_resource_edit': '用户要求编辑已存在后台资源时，按真实 kind/resource_id 读取完整内容并建立当前会话副本，返回 edit_id、精确版本和来源。不会修改正式资源、采用资源或启动任务。系统规则仅可另存。规则副本仍为原 M3 Proposal，后续采用必须使用其已展示版本。',
    'get_resource_edit': '读取当前会话完整编辑内容、版本、来源、保存回执和实际 search_terms。词库返回绑定当前会话及版本的 resource_ref recall_plan，直接传入草案，不重抄内容或 hash；编辑后必须使用新引用，旧引用不可用。规则 edit_id 就是已有 proposal_id，采用仍使用已展示版本的 presentation_id。不能把已保存旧版本误说成当前修改已保存。',
    'update_resource_edit': '按 edit_id/expected_version 局部修改，changes 是操作数组。词库用 upsert_entry（target_id 为已有词条 ID，新增省略 target_id）、remove_entry（删变体只删该条，删主词会连同其变体删除，须在用户明确删除范围内）；规则用 update_rule/remove_rule（target_id 为 rule_id）、add_rule（target_id 为 category_id）、set_exemptions；元数据用 set_metadata：词库允许 title（名称）、description（整库说明，最多2000字）、risk_label（风险标签）；规则允许 name、domain、audit_goal。词库说明不能写入词条 note。values 只包含需要修改的字段。变体归属使用固定 ID，不因改名而丢失。相同term/platform/match_type不可重复，主词与变体也一样；停用不消除重名。改名与保留要求冲突时说明冲突并等待用户选择，不能自行删除、改名或改变其他词条属性来绕过校验。更新校验失败不会提交这次修改；失败不授予删词权限。用户已明确选择方案后按所选范围修改，无需再次确认。不会保存、采用或启动。版本冲突先读并核对，不强行更新预期版本。',
    'save_resource': '用户明确要求保存时，把 edit_id/expected_version 的精确内容正式保存并发布，一次动作完成。mode=new 保存新生成资源，update 保存回可编辑原资源，copy 另存。operation_id 标识这一次逻辑保存，重试保持同一个值；结果未知先 get_resource_save。规则与词库分别调用，允许同轮保存多个资源。保存不等于采用、绑定 Draft 或启动；任务可以继续使用未保存内容。',
    'get_resource_save': '按原 operation_id 查询实际正式保存回执。用于响应丢失后的恢复，避免更换 ID 重复创建。只读，not_found 表示未查到已提交保存。',
}

# Native deferred discovery delivers these only when the tool is described.
# A mixed resource editor retains both sets of guidance; applicability is explicit.
RESOURCE_DESCRIPTIONS["update_resource_edit"] += (
    RESOURCE_EDIT_GUIDANCE + RULESET_AUTHORING_GUIDANCE
)
RESOURCE_DESCRIPTIONS["open_resource_edit"] += RESOURCE_EDIT_GUIDANCE
RESOURCE_DESCRIPTIONS["create_lexicon_edit"] = (
    "生成临时搜索词或完整黑话库的统一入口。新生成传 generation_request：objective、platform、"
    "requirements，用户明确指定数量才传 requested_count，禁止扩展的原文词表传 exact_terms。"
    "keyword_profile " + KEYWORD_PROFILE_CATALOG_DESCRIPTION +
    "模板示例由后端加载，不必自行抄写。"
    "专用生成步骤携带完整原有质量指导，返回经校验的会话编辑稿、search_terms 和 resource_ref recall_plan。"
    "创建草案直接传该 recall_plan，不重抄词库内容；临时使用无需正式保存。"
    f"默认整组1至{SEARCH_TERMS_MAX}个实际词（上限{SEARCH_TERMS_MAX}个），可靠候选不足不凑数；上限内用户指定数量/原文优先。"
    "结果含 search_terms_cap 时，按其 message 向用户说明上限和未使用的词。"
    "仅原样导入用户提供的完整结构化内容使用 content，不调用生成模型。两种输入只能选一种。"
    "保存当前 Draft 词库请用 save_draft_lexicon，只传草案引用和版本，不重抄 content。"
    "content.entries 含稳定 id、term、kind(main/variant/tag)、parent_id、enabled；变体指向主词。"
    "这是同一编辑稿生命周期，不是临时/正式两套词库。不正式保存、不采用、不启动。"
    "生成失败时不得改走 content 编造成功；如实报告错误，保留此前成功操作。"
)

RESOURCE_PROMPT = f"""
资源管理扩展：保持原有自然语言任务入口、规则生成质量、展示后采用、确认后执行边界。
面向用户展示资源名称、正式版本、保存状态、规则条件和主词/变体/标签；不要展示内部 ID、
edit_id、哈希、接口字段名或机器配置。内部标识仅供工具调用，不能代替完整的业务内容。
用户要求生成可用的规则或词库时，必须通过业务工具建立会话资源，然后展示工具返回的实际内容。
“先展示”“不保存”“只临时用”表示不调用 save_resource、不发布到正式后台；不表示跳过
create_ruleset_proposal / create_lexicon_edit。会话编辑内容与正式资源是两件事。
纯文字列出规则或词表不能替代完成生成，否则后续无法修改、保存或可靠用于任务。
只有用户明确只要解释、构思、示例、讨论而非生成可用资源时，才可以仅给文字。
同轮要求生成规则和完整词库时，两者都创建后再展示，不能只完成其中一份就结束。
详细规则生成质量指导完整保留在专用生成步骤，修改规则时完整保留在编辑工具说明中。
用户明确要求正式保存时，使用 save_resource，一次保存即发布；不要求另行发布。
生成本身不正式保存；后续正式保存仍需明确授权。
生成规则仍调用 create_ruleset_proposal，沿用原有领域指导和结构；保存时直接引用 proposal_id
作为 edit_id，不重复生成规则。只要求查看或生成时不调用 save_resource。
用户请求生成完整词库或词库变体时使用 create_lexicon_edit；保存已有编辑稿使用 save_resource，不重新生成。主词是主题和语义归类，
变体词是主要的实际召回表达；每个启用主词有启用变体时，search_terms 使用这些变体，没有启用
变体时才为兼容旧词库回退到主词。标签不参与搜索。生成完整黑话库时默认需要生成变体。

默认整组 1 至 {SEARCH_TERMS_MAX} 个实际搜索词（上限 {SEARCH_TERMS_MAX} 个），不足不凑数；上限内用户明确数量/原文优先，不裁剪已有资源。
原有统一召回质量指导完整保留在专用词库生成步骤中。

临时搜索词与完整黑话库都通过 create_lexicon_edit 先建立会话编辑稿。
新增词库可以不正式保存，直接用于本次任务；纯讨论、示例不要求创建资源。
当用户要求把当前调查 Draft 的关键词正式保存时，必须先读取最新 Draft revision；若其中已有
temporary_terms.lexicon_content，调用 save_draft_lexicon，只传 draft_id、expected_revision、operation_id，
由后端保存该版本内容，不让模型重抄整份词库，也不再创建编辑稿。不得根据扁平
terms 或对话记忆重新猜测主题归属。仍是旧版扁平 terms 时先让用户在结构化 Drawer 中确认主题与变体。
保存正式词库不修改 Draft，也不改变已经启动任务的冻结搜索词。
当用户明确说“搜索主词 X”“只用 X”或给出一组召回词，但没有要求扩展时，这就是本次临时词库的
完整实际搜索词集合。直接生成当前会话的临时词库并保存编辑副本，search_terms 只能包含用户指定的
词；可以为归类增加不进入搜索的主题主词，并把用户指定词作为启用变体，但不要为了“完整覆盖”
“相关性”或主题扩展而加入其他实际搜索词或标签。只有用户明确要求扩展召回词或生成完整扩展词库时，
才可以提出或生成额外词条，并在生成前说明新增范围。
这条规则只约束召回词，不把用户输入解释为采集帖子数量设置；采集规模沿用系统任务配置。
编辑字段、冲突处理、精确保留与删除范围要求完整保留在 open_resource_edit / update_resource_edit 工具说明中；必须先读取工具说明。
用户要任务使用资源时，保留原有 query/Proposal 展示采用/Draft 预览/明确启动流程。
读取 get_resource_edit 返回的 recall_plan 可直接用于 Draft；其中变体已投影为实际搜索词，不要再手工
加入主题主词或 tag。
新建临时词库的 source_lexicon_ids 为 []；edit_id 不是正式词库 ID，绝不能放进该字段。
正式词库使用 read_resource 或 save_resource 返回的 resource_ref recall_plan；
query_investigation_options 用于选择资源，选定后读取该资源引用，不自行组装哈希参数。
词库哈希或配置参数错误应修正词库配置，不修改、重新生成已被用户采用的规则来绕开失败。
一次用户回合可以分别处理多个资源；每个保存有独立 operation_id 和真实回执。
已确认 Draft 不可编辑；用户确认后要求修改时，读取原配置作为新 Draft 的参考，重新展示和确认，不修改原任务。
若当前会话已进入执行或报告阶段，沿用现有“新建调查”入口在新会话准备新 Draft，不替换原会话的已启动任务。
用户明确同时要求生成并保存时，可以完成生成与保存，不把生成回合的结束建议当成必须新增一次保存确认。
参数错误最多修正一次，仅限不改变用户意图的参数纠正；词条重名与保留要求的业务冲突不属于自动纠错。版本冲突需核对内容，写入结果未知查询原回执。不能只凭回答宣称保存。

新生成使用 create_ruleset_proposal 或 create_lexicon_edit 的 generation_request，完整传递调查主题、平台和相关用户约束；
保存不是规则采用，更不是任务启动。
用户指定原文词表传 exact_terms，明确指定资源条数才传 requested_count，不将采集量当作资源条数。
专用生成步骤负责原有质量指导和结构化生成，只有校验成功后才返回真实会话编辑稿。
content 输入仅用于已有完整结构化内容的原样导入，不是生成失败后的替代路径。
用户说“保存刚才那个”时，依据真实回执定位 edit_id 和 expected_version；有歧义先澄清，不重生成。
没有成功工具回执，不能声称生成、修改、保存、采用或启动完成。
生成工具报内容拦截、截断或生成失败时，本轮不要换说法、改用 content 或其他模型重试。
已成功步骤保留，只继续未完成且仍获授权的部分。保存结果未知使用原 operation_id 查询 get_resource_save。
"""



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
        return tool_view(service.save(**args, **ctx))
    return tool_view(service.get_save(**args, **ctx))
