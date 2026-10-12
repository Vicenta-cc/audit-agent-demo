"""Experiment-only wording; no tools removed and no validation/mutation changes."""
ROUTING = '资源工具选择：新建独立规则/词库才用 create_ruleset_proposal/create_lexicon_edit。修改已有内容必须用 update_resource_edit；正式资源先 open_resource_edit 复制指定版本，已有会话稿先 get_resource_edit。禁止用生成或整稿替换代替局部编辑。'
WARNINGS = {
 'create_ruleset_proposal': '【仅新建独立规则】最佳用途：从零生成另一套独立规则，或原样导入用户已给定的完整新资源。【不允许用于修改已有规则】修改风险等级、条件、条款、豁免、临时修改版必须用 open_resource_edit + update_resource_edit；不得让生成器重写，也不得自己抄写整套 content 来冒充局部修改。',
 'create_lexicon_edit': '【仅新建独立词库】最佳用途：生成全新的词库或搜索词集合，或原样导入已给定的完整新词库。【不允许用于修改已有词库】给已有词库增删关键词、增加变体、改名、改风险等级或停用，必须用 open_resource_edit/get_resource_edit + update_resource_edit。',
 'open_resource_edit': '【正式资源局部修改的首选入口】按用户指定的真实资源及版本建立原样副本，然后必须用 update_resource_edit 只改所需字段。临时修改也走此入口，不调用生成工具。已有会话编辑稿无需再复制，直接 get_resource_edit。',
 'get_resource_edit': '【已有会话稿的读取入口】需要查看或继续修改已存在的编辑稿时用本工具读取真实内容和版本；后续局部修改必须用 update_resource_edit，不重新生成。',
 'update_resource_edit': '【局部修改必选】已有规则或词库的新增、删除、修改条目、条件、风险等级、豁免与元数据，必须使用本工具。只提交变化的字段，不抄整份内容。豁免使用 add_exemption/update_exemption/remove_exemption。不要改走生成工具或 update_ruleset_proposal。',
 'update_ruleset_proposal': '【仅用户已提供完整替换正文时使用】【不允许用于局部修改】本工具会提交整套规则；只改某一条、风险等级或豁免时，即使已经打开副本，也必须改用 update_resource_edit。不要自行重写或抄写整份 content。',
}
def install():
 from backend.investigation_creation import tools as t
 from backend.resource_management import tools as r
 from backend.hermes_runtime.creation_discovery import BRIDGE_GUIDANCE
 for name,warning in WARNINGS.items():
  old=t.M3_TOOL_DESCRIPTIONS[name]
  old=old.replace('Use this tool when the user asks to generate 审核规则 or another candidate.', 'Use this tool only for a new independent resource, never for changes to an existing resource.')
  updated=warning+'\n'+old
  t.M3_TOOL_DESCRIPTIONS[name]=updated
  if name in r.RESOURCE_DESCRIPTIONS:r.RESOURCE_DESCRIPTIONS[name]=updated
  for schema in t._ALL_CREATION_TOOL_SCHEMAS:
   if schema['name']==name:schema['description']=updated
 for name in ('tool_search','tool_describe','tool_call'):
  old=BRIDGE_GUIDANCE[name]
  old=old.replace('create_ruleset_proposal 使用 arguments.content，不是 canonical_content。', 'create_ruleset_proposal 新生成用 arguments.generation_request，原样导入才用 arguments.content；不传 canonical_content。')
  BRIDGE_GUIDANCE[name]=ROUTING+' '+old
