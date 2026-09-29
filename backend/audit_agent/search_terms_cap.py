"""One per-task cap on actual search terms, shared by every task source."""
from .config import settings


def search_terms_max() -> int:
    return settings.search_terms_max


def cap_search_terms(terms, limit: int | None = None) -> tuple[list[str], list[str]]:
    """Dedupe in order, keep the first N; return (kept, dropped)."""
    limit = search_terms_max() if limit is None else max(1, int(limit))
    unique = list(dict.fromkeys(terms))
    return unique[:limit], unique[limit:]


def unused_terms_notice(dropped: list[str], limit: int | None = None) -> str:
    if not dropped:
        return ""
    limit = search_terms_max() if limit is None else limit
    return f"已截取为前 {limit} 个搜索词，未使用：{'、'.join(dropped)}"


def lexicon_cap_note(terms) -> dict | None:
    """For a kept-whole lexicon over the cap: what a task built from it would not search."""
    kept, dropped = cap_search_terms(terms)
    if not dropped:
        return None
    limit = len(kept)
    return {
        "limit": limit, "search_term_count": limit + len(dropped), "unsearched_terms": dropped,
        "message": (f"本词库共 {limit + len(dropped)} 个搜索词，已全部保留；每个任务只搜索前 {limit} 个，"
                    f"不搜索：{'、'.join(dropped)}；全部词仍用于研判。请如实告知用户。"),
    }
