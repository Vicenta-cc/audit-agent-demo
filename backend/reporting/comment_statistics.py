"""Deterministic comment coverage shared by report pages and conversation tools.

Counts are scoped to a frozen report, independent of evidence selection,
clustering and account identity availability.
"""
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


COUNT_FIELDS = ('total', 'completed', 'failed', 'pending', 'unknown', 'risk', 'no_risk', 'risk_unknown')


def snapshot_comment_coverage(payloads: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    statuses: Counter[str] = Counter()
    risks: Counter[str] = Counter()
    missing_posts = 0
    for payload in payloads:
        raw = payload.get('raw_content_payload') or {}
        # Older A/B archives retain comments only in raw_content_payload;
        # newer snapshots may have both raw and compact copies of each comment.
        sources = [source['comments'] for source in (payload, raw)
                   if isinstance(source, Mapping) and source.get('comments') is not None]
        if not sources:
            missing_posts += 1
            continue
        comments: dict[str, dict[str, Any]] = {}
        for source in sources:
            if not isinstance(source, (list, tuple)):
                raise ValueError('Frozen comments must be an array')
            for comment in source:
                if not isinstance(comment, Mapping):
                    raise ValueError('Frozen comment must be an object')
                identifier = str(comment.get('comment_id') or '').strip()
                if not identifier:
                    raise ValueError('Frozen comment identity is missing')
                # Raw non-empty status fields take precedence, as in the frozen
                # comment detail projection. Identity is local to the parent post.
                merged = comments.setdefault(identifier, {})
                for field in ('audit_status', 'risk_level'):
                    value = str(comment.get(field) or '').strip().lower()
                    if value:
                        merged[field] = value
        for comment in comments.values():
            status = comment.get('audit_status', '')
            status = 'pending' if status == 'queued' else status
            statuses[status if status in {'completed', 'failed', 'pending'} else 'unknown'] += 1
            if status == 'completed':
                risk = comment.get('risk_level', '')
                risks[risk if risk in {'none', 'low', 'medium', 'high'} else 'unknown'] += 1
    result = {
        'available': missing_posts == 0,
        'scope': 'stored_snapshot_comments_not_platform_total',
        'missing_post_count': missing_posts,
    }
    if missing_posts:
        # Absence of a frozen comment list is not evidence of zero comments.
        return {**result, **dict.fromkeys(COUNT_FIELDS)}
    return {
        **result,
        'total': sum(statuses.values()),
        'completed': statuses['completed'],
        'failed': statuses['failed'],
        'pending': statuses['pending'],
        'unknown': statuses['unknown'],
        'risk': sum(risks[level] for level in ('low', 'medium', 'high')),
        'no_risk': risks['none'],
        'risk_unknown': risks['unknown'],
    }
