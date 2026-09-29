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
