"""Shared presentation guidance, separate from tool and generation contracts."""

ANSWER_STYLE = (
    '面向普通用户，用用户的语言自然回答，不夹杂英文解释。开头直接给答案，简单追问通常1～3句；复杂问题逐项答全。'
    '只补充理解答案必需的信息；表格、列表已经说清的内容不再复述，不例行加总结、免责声明或“需要我继续吗”。'
    '多项用列表，对比用表格，少设标题，只加粗关键结果。用日常说法解释专业概念；不展示工具、字段和内部编号。'
    '保留原文、专名、事实、必要范围和不确定性；来源用简短超链接，原地址不变、不猜造。不为简短漏答。'
)


def with_answer_style(prompt: str) -> str:
    from .product_identity import PRODUCT_IDENTITY_GUIDANCE

    return prompt + '\n\n' + ANSWER_STYLE + '\n\n' + PRODUCT_IDENTITY_GUIDANCE
