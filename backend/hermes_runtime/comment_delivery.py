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
        explanation += ("\n\n" if explanation else "") + "以下按报告记录展示本批评论原文。"
    body = render(runtime, session_id, turn_id)
    public = (explanation + "\n\n---\n\n### 评论原文\n\n" + body) if explanation else body
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
