"""Additional creation-mode tools; generation guidance stays in the existing M3 tools."""
from .contracts import ReadResourceInput, CreateLexiconInput, OpenResourceInput, GetEditInput, UpdateEditInput, SaveResourceInput, GetSaveInput

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
    'read_resource': '查询或读取后台正式规则 ruleset 或词库 lexicon。resource_id 为空时按 query 分页检索；指定真实 ID 时返回完整内容和版本。正式词库返回可直接用于 Draft 的 recall_plan；其中 runtime_content_hash 是实际搜索指纹，content_hash 是完整编辑内容指纹，两者不可混用。只读，不创建编辑副本、任务或保存。任务资源选择仍可用 query_investigation_options。',
    'create_lexicon_edit': '用户授权生成词库时，将完整词库持久化为当前会话编辑内容，不正式保存。content.entries 每项含稳定 id、term、kind(main/variant/tag)、parent_id、enabled。变体 parent_id 指向本词库的主词 id。仅启用 main 进入 search_terms；不自动生成变体或标签，只按需求生成。与临时规则一起生成时保持原有规则生成、展示、后续采用流程。',
    'open_resource_edit': '用户要求编辑已存在后台资源时，按真实 kind/resource_id 读取完整内容并建立当前会话副本，返回 edit_id、精确版本和来源。不会修改正式资源、采用资源或启动任务。系统规则仅可另存。规则副本仍为原 M3 Proposal，后续采用必须使用其已展示版本。',
    'get_resource_edit': '读取当前会话完整编辑内容、版本、来源、保存回执和实际 search_terms。规则 edit_id 就是已有 proposal_id。只读；不能把已保存旧版本误说成当前修改已保存。',
    'update_resource_edit': '按 edit_id/expected_version 局部修改，changes 是操作数组。词库用 upsert_entry（target_id 为已有词条 ID，新增省略 target_id）、remove_entry（删除主词连同其变体）；规则用 update_rule/remove_rule（target_id 为 rule_id）、add_rule（target_id 为 category_id）、set_exemptions；元数据用 set_metadata。values 只包含需要修改的字段。变体归属使用固定 ID，不因改名而丢失。不会保存、采用或启动。版本冲突先读并核对，不强行更新预期版本。',
    'save_resource': '用户明确要求保存时，把 edit_id/expected_version 的精确内容正式保存并发布，一次动作完成。mode=new 保存新生成资源，update 保存回可编辑原资源，copy 另存。operation_id 标识这一次逻辑保存，重试保持同一个值；结果未知先 get_resource_save。规则与词库分别调用，允许同轮保存多个资源。保存不等于采用、绑定 Draft 或启动；任务可以继续使用未保存内容。',
    'get_resource_save': '按原 operation_id 查询实际正式保存回执。用于响应丢失后的恢复，避免更换 ID 重复创建。只读，not_found 表示未查到已提交保存。',
}

RESOURCE_PROMPT = '''
资源管理扩展：保持原有自然语言任务入口、规则生成质量、展示后采用、确认后执行边界。
面向用户展示资源名称、正式版本、保存状态、规则条件和主词/变体/标签；不要展示内部 ID、
edit_id、哈希、接口字段名或机器配置。内部标识仅供工具调用，不能代替完整的业务内容。
用户要求生成可用的规则或词库时，必须通过业务工具建立会话资源，然后展示工具返回的实际内容。
“先展示”“不保存”“只临时用”表示不调用 save_resource、不发布到正式后台；不表示跳过
create_ruleset_proposal / create_lexicon_edit。会话编辑内容与正式资源是两件事。
纯文字列出规则或词表不能替代完成生成，否则后续无法修改、保存或可靠用于任务。
只有用户明确只要解释、构思、示例、讨论而非生成可用资源时，才可以仅给文字。
同轮要求生成规则和完整词库时，两者都创建后再展示，不能只完成其中一份就结束。
生成规则前做语义自查：hit_condition 与 adjudication_notes 的必要条件必须一致，尤其不能把
“任一条件即可”的风险写成多个条件同时满足。
以下民族相关检查仅适用于民族关系请求，其他领域不要套用这些类型或豁免：
明确区分个人生活选择与血统纯洁、强制干预等主张。
通婚类规则若合并，明确列为三个独立分支：普遍排斥跨民族通婚，或主张血统纯洁/血统污染，
或强制干预跨民族婚恋。前两项不要求强制干预；hit_condition 和 adjudication_notes 都不得
追加该门槛。例如“跨族婚姻污染血统”可独立命中，不能因未要求阻止婚姻而排除。
语言类线索也按可靠原文、译文、OCR、ASR等实际可承载证据选择阶段，不默认只落到评论。
单纯语言或民族身份不是风险；原有低优先级辱骂线索也不能被升级为民族仇恨结论。
通用民族关系方案还需逐项检查原有覆盖：群体负面泛化/先天优劣、非人化/集体犯罪污名、
剥夺权利/驱逐、明确暴力威胁、普遍通婚排斥/血统纯洁/强制干预、民族关联个体攻击，以及
维语恶意辱骂的低优先级线索。按用户要求限定条数时，合并相邻风险并用“任一”清楚列出
独立触发条件，不无声遗漏暴力威胁或跨民族情侣、红娘、支持融合者遭民族关联攻击等类型。
多民族关系场景中，对讨论涉及的各民族适用相同的攻击判定边界，不把示例中的某个民族误读为
唯一受保护对象。例如维汉关系任务不能只识别针对维吾尔族的攻击而漏掉针对汉族的同类攻击。
使用“讨论涉及的民族群体/相关个人”等清楚主体范围；不推断作者或被攻击者的真实民族身份。
用户明确只要其中某个子范围时按该范围生成，不强制扩展。避免整份规则多处重复同一条件。
保护正常文化表达、个人婚恋选择和具体行为批评，但豁免必须真的否定风险，不能让新闻、
文化讨论或个人身份成为同时发布攻击言论的通行证。生成内容条数应符合用户要求。
用户明确要求正式保存时，使用 save_resource，一次保存即发布；不要求另行发布。
原提示中的“生成不会正式保存”仍适用；“此处不支持正式保存”由本段替代。
生成规则仍调用 create_ruleset_proposal，沿用原有领域指导和结构；保存时直接引用 proposal_id
作为 edit_id，不重复生成规则。只要求查看或生成时不调用 save_resource。
用户请求完整词库、词库变体或独立保存词库时使用 create_lexicon_edit；其 search_terms 是
任务实际可用的启用主词，变体和标签只管理、不自动展开搜索。原有简单临时搜索词任务仍可
沿用原路径，不强制增加资源工具。新增词库也可以完全不正式保存，直接用于本次任务。
编辑后台已有资源先 read_resource/open_resource_edit；只说修改时留在会话副本，明确要求
修改并保存时可同轮完成，不机械多问。保存不是规则采用，更不是任务启动。
“修改后先展示、不保存”也必须实际 open_resource_edit 并 update_resource_edit，然后展示工具
返回的会话副本；不得仅描述修改方案，或把用户已经要求的副本修改再次推迟到确认之后。
只有用户明确要求“讨论修改方案、不要执行修改”时，才仅说明方案。不保存指不写回正式库，
不禁止建立与编辑会话副本。规则与词库都按此处理；修改现有规则应保留 open_resource_edit
建立的来源关联，不另行生成一个无来源 Proposal 冒充修改完成。
用户要任务使用资源时，保留原有 query/Proposal 展示采用/Draft 预览/明确启动流程。
读取 get_resource_edit 返回的 recall_plan 可直接用于 Draft；不要加入变体或 tag。
新建临时词库的 source_lexicon_ids 为 []；edit_id 不是正式词库 ID，绝不能放进该字段。
正式词库使用 read_resource 返回的 existing_lexicon recall_plan，或 query_investigation_options
返回的实际搜索指纹；不得把完整内容 content_hash 填入 expected_runtime_content_hash。
词库哈希或配置参数错误应修正词库配置，不修改、重新生成已被用户采用的规则来绕开失败。
一次用户回合可以分别处理多个资源；每个保存有独立 operation_id 和真实回执。
已确认 Draft 不可编辑；用户确认后要求修改时，读取原配置作为新 Draft 的参考，重新展示和确认，不修改原任务。
若当前会话已进入执行或报告阶段，沿用现有“新建调查”入口在新会话准备新 Draft，不替换原会话的已启动任务。
用户明确同时要求生成并保存时，可以完成生成与保存，不把生成回合的结束建议当成必须新增一次保存确认。
参数错误最多修正一次；版本冲突需核对内容，写入结果未知查询原回执。不能只凭回答宣称保存。
'''


def execute_resource(service, name, parsed, *, session_id, principal):
    args = parsed.model_dump(mode='json')
    ctx = dict(session_id=session_id, principal=principal)
    if name == 'read_resource':
        return service.read(**args, principal=principal)
    if name == 'create_lexicon_edit':
        return service.create_lexicon(**args, **ctx)
    if name == 'open_resource_edit':
        return service.open(**args, **ctx)
    if name == 'get_resource_edit':
        return service.get_edit(**args, **ctx)
    if name == 'update_resource_edit':
        return service.update(**args, **ctx)
    if name == 'save_resource':
        return service.save(**args, **ctx)
    return service.get_save(**args, **ctx)
