"""Trusted authoring instructions, loaded with the relevant tool schema.

These are not per-user policy or authorization. Keep lifecycle authority in the
creation system prompt; preserve authoring semantics when relocating guidance.
"""

RULESET_AUTHORING_GUIDANCE = """When authoring or revising any domain's 审核规则完整内容, choose each rule's application_stages by
asking which evidence modalities can independently show the risk behavior. The only legal stages
are image_evidence, video_frame_evidence, comment_audit and fusion_audit. image_evidence extracts
image text and visual evidence AND applies business 审核规则 relevant to images. video_frame_evidence
extracts and assesses video frames, OCR, ASR and contextual evidence AND applies business 审核规则
relevant to video. comment_audit audits the comment's own content. fusion_audit performs final rule
matching and combined judgment using existing cross-modal evidence; it does not reread all raw
media or recover every OCR/ASR detail. Do not default all 审核规则 to comment_audit + fusion_audit.
A rule intended to support a final finding must also include fusion_audit, even when one modality
alone can establish that risk: the compiler does not automatically copy 审核规则 into fusion. Reserve
discovery-only stages for deliberate evidence prerequisites covered by an explicit final rule;
do not require a cross-modal closed loop before recognizing every independently sufficient risk.
For example, an explicit requirement to pay before starting a job can independently appear in a
recruitment poster, video subtitles/frames, spoken ASR or comments, so normally consider all four
stages. A rule about participation organized by a comment can use comment_audit + fusion_audit;
a rule that only combines existing evidence across sources can use fusion_audit alone. These are
examples of modality-based selection, not a requirement that every rule use all four stages.

Both general_exemptions and rule_exemptions are strong business exemptions: matching them removes
the corresponding risk, rather than providing background, a confidence hint or a small downgrade.
Author an exemption only when its condition negates the applicable risk. A general
exemption must be valid across the 审核规则 it can exempt; use rule_exemptions for narrower conditions.
Enterprise certification, a blue verification badge, an official account, matching registered
business scope, a well-known institution or real-name verification alone must never be a general
exemption. Identity or reputation does not negate an explicit risky act. For example, even a
verified employer's explicit requirement to pay a training fee before employment is not exempt
because the employer is verified. Do not encode mere background information as an exemption.


生成质量参考（只在民族关系讨论任务中适用）：搜索以相关主体和婚恋、家庭、文化语言、身份认同、交往等议题寻找讨论场；重点议题可分别使用维族、维吾尔族及维汉的自然完整查询，例如维吾尔族民族认同、维汉恋爱。正常民族认同、文化保护和个人婚恋选择不是风险，不推断任何人的民族身份或认同倾向。
规则识别具体攻击：群体负面泛化与先天优劣通常 medium；非人化、严重集体犯罪污名、权利剥夺或驱逐、明确暴力威胁通常 high，避免同义重复。通婚普遍排斥与血统纯洁分别覆盖，血统污染主张无需同时要求强制阻止婚姻。民族关系关联攻击通常 medium：对红娘、情侣或支持者的侮辱诅咒须与其民族相关身份、行为或立场有明显关联；普通售假等具体行为批评不能仅因账号背景而命中。可靠维语原文或译文中的明确恶意辱骂可独立列为 low 专项敌意线索，不要求民族对象或民族动机，不据此认定民族仇恨；单纯使用维语不命中。模糊敌意仅作 low 待复核兜底，有明确规则可用时不用它。
保持正常身份文化表达、具体行为正常批评、个人生活选择，以及不支持风险表达的新闻学术和批判性引用豁免；豁免不得抵消明确攻击。关键条件写入 hit_condition，通用原则写入 audit_goal，不能只写在不进入编译的 adjudication_notes；按实际证据模态分配阶段。以上是生成指导，不自行扩展到政治宗教或思想倾向评分，也不改变任务流程。

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
结构化规则通用约束：正常、允许、无需处置的表达应写入 general_exemptions 或 rule_exemptions，不能创建启用的 low 风险规则来表示豁免。兜底规则仅在不能命中其他明确风险规则时使用；生成和修改后检查是否存在相反条件。民族主题中的模糊敌意另须具有明确民族关联。"""

LEXICON_DOMAIN_GUIDANCE = """生成质量参考（只在民族关系讨论任务中适用）：搜索以相关主体和婚恋、家庭、文化语言、身份认同、交往等议题寻找讨论场；重点议题可分别使用维族、维吾尔族及维汉的自然完整查询，例如维吾尔族民族认同、维汉恋爱。正常民族认同、文化保护和个人婚恋选择不是风险，不推断任何人的民族身份或认同倾向。
"""

RESOURCE_EDIT_GUIDANCE = """词库/黑话库的“说明、描述、用途”统一对应整库 description，修改时用 update_resource_edit：
changes=[{"operation":"set_metadata","values":{"description":"用户要求的说明"}}]。
词条 note 仅用于用户明确指定的词条备注；不得把整库说明写入所有词条 note、risk_label 或 title。
读取旧词库未返回 description 表示暂无说明，不代表不能修改。只改说明时保留其他字段与全部词条。
“不要这次修改，重新读取后台保存版本”应回到该词库正式版本；不要用未保存副本冒充正式内容。
编辑后台已有资源先 read_resource/open_resource_edit；只说修改时留在会话副本，明确要求
修改并保存时可同轮完成，不机械多问。保存不是规则采用，更不是任务启动。
“修改后先展示、不保存”也必须实际 open_resource_edit 并 update_resource_edit，然后展示工具
返回的会话副本；不得仅描述修改方案，或把用户已经要求的副本修改再次推迟到确认之后。
只有用户明确要求“讨论修改方案、不要执行修改”时，才仅说明方案。不保存指不写回正式库，
不禁止建立与编辑会话副本。规则与词库都按此处理；修改现有规则应保留 open_resource_edit
建立的来源关联，不另行生成一个无来源 Proposal 冒充修改完成。
修改要求与词库重名约束冲突时，先保留原内容，说明哪两项要求不能同时满足，并让用户选择。
同一平台、匹配方式下，主词与变体不能同名，停用也不解除冲突。若用户要求主词改名且保留
同名变体，可建议保留变体并给主词换名、给变体换名后保留它，或经用户同意去掉重复变体而
将该词保留为主词。按具体冲突给出相关选项，不要求固定句式。
用户没有给出替代名称时，改名方案只说明可以给主词或变体换名，并请用户指定名称，不自拟具体新词（也不举示例词）。用户给出名称后核对完整词库，包括停用词条；若仍重名则继续说明冲突，不自行再换一个词。
启用变体优先参与实际搜索，没有启用变体的主题才回退主词；说明变更的实际影响。不要把移除变体
记录说成主题主词也从词库消失，也不能因此视为已满足保留变体。
发现冲突或更新校验被拒绝，都不代表获得了额外修改权限；不能擅自删词、合并、改归属、改平台
或匹配方式来让操作通过。一次更新校验失败不会写入该次修改，但不代表本轮此前已成功的操作
也被撤销；按工具实际结果说明状态。用户已明确选定解决方案后直接执行选定修改，不重复询问，
保留其他词条的标识、归属、启停状态与备注；删除主词会连带删除其变体，不能扩大用户删除范围。
"""
