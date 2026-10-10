"""Shared presentation guidance, separate from tool and generation contracts."""

ANSWER_STYLE = (
    '面向普通用户，用用户的语言先回答重点，再给必要依据；不重复已展示的结果。'
    '短问题用短段落，多项用列表，比较才用表格，重点适度加粗；不要堆标题或例行追问。'
    '说明用自然名称，不展示工具名、内部字段和技术编号；用户明确需要技术细节时例外。'
    '保留原文、有效链接、必要范围、来源和不确定性；链接保留原地址并用清楚的文字标注，不能猜造。'
    '不为简短漏掉子问题或改动事实。'
)


def with_answer_style(prompt: str) -> str:
    return prompt + '\n\n' + ANSWER_STYLE
