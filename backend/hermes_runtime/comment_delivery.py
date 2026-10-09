"""Projection of a prepared server batch, shared by both conversation entries.

Ordinary explanations remain model-written, including progress lists and tables.
Detect copied bodies and recognizable comment rows before appending a prepared
batch. This is a presentation guard, not a general hallucination detector.
Tool results and business receipts survive a rejected explanatory draft.
"""
from copy import deepcopy
import re


_COMMENT_ROW = re.compile(
    r"(?m)^\s*(?:\d+[.、)）]\s*|[（(]\d+[)）]\s*)"
    r"(?:\*\*)?([^\n:：]{1,60}?)(?:\*\*)?[:：]\s*\S+"
)
_COMMENT_TABLE = re.compile(r"(?m)^\s*\|[^\n]*(?:作者|用户|昵称)[^\n]*\|[^\n]*(?:原文|内容|评论)[^\n]*\|")
_METADATA_LABELS = {"已展示", "剩余", "总计", "当前进度", "资源名称", "资源版本", "保存状态"}


def _compact_batch_intro(answer):
    """Remove only standalone list logistics; keep analysis and business receipts.

    This deliberately is not a general answer rewriter. Mixed paragraphs and
    unknown phrasing are retained rather than risking loss of substantive text.
    """
    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", answer.strip()):
        # Remove a standalone navigation sentence even when appended to useful
        # analysis. No substring editing of the analysis itself.
        paragraph = re.sub(r"(?:需要接着看就说[‘“\"]继续[’”\"]。|发送[‘“\"]继续[’”\"]查看下一批。)\s*$", "", paragraph).strip()
        if not paragraph:
            continue
        plain = paragraph.replace("**", "").strip()
        if re.search(r"保存|词库|规则|原因|因为|依据|证据|判定理由|不能|无法|没有|未提供", plain):
            paragraphs.append(paragraph)
            continue
        introductory = re.fullmatch(r"以下[^。\n]*展示[^。\n]*评论原文[。！]?", plain)
        progress = re.fullmatch(r"(?:已展示|本批展示|本次展示|该帖[^\n]*评论共)[^\n]*\d[^\n]*条[。；\s]*", plain)
        navigation = re.match(r"^(?:需要接着看|需要继续|如需继续|想继续看|发送[‘“\"]继续|可以继续[：:]|如需进一步操作[：:])", plain)
        progress_lines = re.match(r"^(?:已准备好第[^。]*批评论。|已展示全部\s*\d+\s*条。|本批进度[：:])", plain)
        # A tool receipt must not be interpreted as a visible author link.
        invented_link = (re.search(r"(?:每条|原文|列表|本批).*(?:自带|附带|包含|含有|含|给出).*(?:作者入口|作者链接)", plain)
                         and not re.search(r"(?:不|未|无).{0,5}(?:入口|链接)", plain))
        if not (introductory or progress or navigation or progress_lines or invented_link):
            paragraphs.append(paragraph)
    return "\n\n".join(paragraphs)


def prepared_runtime(session_id, turn_id):
    from hermes_m0.runtime import report_task_runtime_for_session
    runtime = report_task_runtime_for_session(session_id)
    states = getattr(runtime, "comment_batch_states", {})
    return runtime if turn_id in states.get(session_id, {}).get("pages", {}) else None


def live_model_text_allowed(session_id, turn_id):
    # Once a batch is prepared, buffer explanation until it can be checked.
    return prepared_runtime(session_id, turn_id) is None


def _copies_comments(text, page):
    # Lists alone are not evidence of a second comment list: Qwen legitimately
    # uses bullets for progress, save receipts and suggested follow-up actions.
    if _COMMENT_TABLE.search(text) or any(
        label.strip().strip("*") not in _METADATA_LABELS
        for label in _COMMENT_ROW.findall(text)
    ):
        return True
    # Also reject unnumbered copies of actual bodies. The model was not given
    # these bodies by the batch tool and is not their delivery authority.
    return any(str(c.get("content") or "").strip() in text
               for c in page["comments"] if len(str(c.get("content") or "").strip()) >= 4)


def compose(runtime, session_id, turn_id, answer, transcript, history_count, *, verified_receipts=""):
    from hermes_m0.comment_batches import render, _summary
    page = runtime.comment_batch_states[session_id]["pages"][turn_id]
    rejected = _copies_comments(answer, page)
    explanation = answer.strip() if not rejected else ""
    if rejected:
        explanation = verified_receipts.strip()
    explanation = _compact_batch_intro(explanation)
    body = render(runtime, session_id, turn_id)
    public = (explanation + "\n\n---\n\n" + body) if explanation else body
    # Keep raw batches out of the model history, but replace its final draft so
    # invented comment text cannot be reused on the next turn or after restart.
    summary = _summary(session_id, turn_id, page)
    history_text = (explanation + "\n\n" if explanation else "") + (
        f"已展示第 {summary['start']}–{summary['end']} 条评论原文，"
        f"共 {summary['returned_count']} 条，匹配总数 {summary['matched_count']} 条。"
        f"还有 {summary['remaining_count']} 条。"
    )
    projected = deepcopy(transcript)
    for item in projected[history_count:]:
        if item.get("role") == "assistant" and _copies_comments(str(item.get("content") or ""), page):
            item["content"] = ""
    if projected and projected[-1].get("role") == "assistant" and not projected[-1].get("tool_calls"):
        projected[-1]["content"] = history_text
    return public, projected, rejected
