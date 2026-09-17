"""Read-only presentation view for published R3.1 structured reports."""

from __future__ import annotations

from typing import Any, Iterable

from backend.domain.identity import stable_hash
from backend.reporting.errors import ReportGenerationError
from backend.reporting.comment_statistics import snapshot_comment_coverage


PRESENTATION_SCHEMA_VERSION = "r31-report-presentation/v1"
# Editorial omissions apply to display only; archived evidence remains intact.
_OMITTED_BOUNDARY_NOTES = frozenset({
    "仅覆盖输入中明确标记为review且包含相关direct evidence的帖子。",
    "仅基于输入中已审核并标记为risk的评论证据。",
    "仅基于输入中已标记为high risk的审核结果。",
})
APPENDIX_VIEWS = {"posts", "standalone", "evidence"}
ACCOUNT_ROLES = {None, "post_author", "comment_author"}
ACCOUNT_FILTERS = {"post_author", "cross_investigation_commenter", "risk_commenter"}
ACCOUNT_FILTER_SORTS = {
    "post_author": {"published_post_count", "risk_published_post_count", "latest_activity"},
    "cross_investigation_commenter": {
        "investigation_count",
        "comment_count",
        "risk_comment_count",
        "latest_activity",
    },
    "risk_commenter": {
        "risk_comment_count",
        "comment_count",
        "commented_post_count",
        "latest_activity",
    },
}
ACCOUNT_FILTER_DEFAULT_SORT = {
    "post_author": "published_post_count",
    "cross_investigation_commenter": "investigation_count",
    "risk_commenter": "risk_comment_count",
}
ACCOUNT_SEARCH_MAX_LENGTH = 80
_ACCOUNT_METRIC_KEYS = (
    "comment_count",
    "risk_comment_count",
    "published_post_count",
    "risk_published_post_count",
    "commented_post_count",
    "commented_post_author_count",
)

_PLATFORM_PRESENTATION = {
    "dy": {"code": "dy", "label": "抖音"},
    "douyin": {"code": "dy", "label": "抖音"},
}

_DECISION_PRESENTATION = (
    ("pass", "通过"),
    ("review", "复审"),
    ("reject", "拒绝"),
)

_RISK_PRESENTATION = (
    ("high", "高风险"),
    ("medium", "中风险"),
    ("low", "低风险"),
    ("none", "无风险"),
)


def build_presentation_projection(
    document: dict[str, Any],
    *,
    snapshot: Any,
    account_projection: dict[str, Any] | None,
    account_repository: Any | None = None,
    current_task_id: str = "",
) -> dict[str, Any]:
    """Build a deterministic view without mutating its published inputs."""

    posts = _list(document, "posts")
    audit_findings = _list(document, "audit_findings")
    evidence = _list(document, "evidence")
    investigation_findings = _list(document, "investigation_findings")
    posts_by_ref = _index(posts, "post_ref")
    audits_by_ref = _index(audit_findings, "audit_finding_ref")
    evidence_by_ref = _index(evidence, "evidence_ref")
    standalone_items = _standalone_items(
        document, posts_by_ref=posts_by_ref, audits_by_ref=audits_by_ref
    )

    metadata = _mapping(document, "report_metadata")
    accounts = _account_groups(
        account_projection,
        account_repository=account_repository,
        current_task_id=current_task_id,
    )
    coverage = snapshot_comment_coverage(post.payload for post in snapshot.posts)
    comment_statistics = (coverage["completed"], coverage["risk"])
    sections = [
        _section_projection(
            section,
            findings=investigation_findings,
            posts_by_ref=posts_by_ref,
            audits_by_ref=audits_by_ref,
            evidence_by_ref=evidence_by_ref,
            standalone_items=standalone_items,
        )
        for section in _list(document, "ordered_sections")
    ]
    source_statistics = _mapping(document, "statistics")
    statistics = _statistics_projection(
        source_statistics,
        evidence=evidence,
        investigation_findings=investigation_findings,
        comment_statistics=comment_statistics,
    )
    summary = _investigation_summary(statistics)
    template_kind = str(document.get("template_kind") or "")
    if template_kind in {"all_pass", "single_risk_post"}:
        accounts["snapshot_summary"] = document.get("snapshot_account_summary")
    if template_kind in {"all_pass", "single_risk_post", "unified_audit"}:
        overview = next(
            (section for section in sections if section["section_type"] == "overview"), {}
        )
        summary = {"status": "available", "paragraphs": overview.get("paragraphs") or []}
    statistics["comment_audit_coverage"] = coverage
    statistics["stored_comments"] = coverage["total"]
    statistics["incomplete_comments"] = (
        coverage["total"] - coverage["completed"] if coverage["available"] else None
    )
    statistics["direct_comment_evidence_count"] = sum(
        item.get("support_type") == "direct" and item.get("evidence_type") == "comment"
        for item in evidence
    )
    return {
        "schema_version": PRESENTATION_SCHEMA_VERSION,
        "report_metadata": {
            "title": str(metadata.get("title") or "调查报告"),
            "source_name": str(metadata.get("source_name") or ""),
            "status": str(metadata.get("status") or "published"),
            "published_at": str(metadata.get("published_at") or ""),
            "template_kind": template_kind,
            "platform": _platform_projection(snapshot),
            "scope": _scope_projection(metadata),
        },
        "investigation_summary": summary,
        "statistics": statistics,
        "accounts": accounts,
        "ordered_sections": sections,
        "appendix": {
            "available": True,
            "post_count": len(posts),
            "direct_evidence_count": sum(
                str(item.get("support_type") or "") == "direct" for item in evidence
            ),
        },
    }


def build_account_index_page(
    page: dict[str, Any], *, role: str | None
) -> dict[str, Any]:
    if role not in ACCOUNT_ROLES:
        raise ReportGenerationError("invalid ReportAccountEntry role filter")
    return {
        "role_filter": role,
        "action_label": (
            "查看全部评论账号" if role == "comment_author" else "查看全部账号"
        ),
        "entries": [_public_account_entry(item) for item in page.get("entries") or []],
        "matched_count": int(page.get("matched_count") or 0),
        "has_more": bool(page.get("has_more")),
        "cursor": page.get("cursor"),
    }


def build_filtered_account_index_page(
    account_projection: dict[str, Any] | None,
    *,
    account_repository: Any | None,
    current_task_id: str,
    account_filter: str,
    limit: int,
    cursor: str | None,
    search: str | None = None,
    sort_order: str | None = None,
) -> dict[str, Any]:
    if account_filter not in ACCOUNT_FILTERS:
        raise ReportGenerationError("invalid ReportAccountEntry presentation filter")
    if not 1 <= int(limit) <= 100:
        raise ReportGenerationError("report account limit must be between 1 and 100")
    normalized_search = str(search or "").strip()
    if len(normalized_search) > ACCOUNT_SEARCH_MAX_LENGTH:
        raise ReportGenerationError("report account search is too long")
    resolved_sort = sort_order or ACCOUNT_FILTER_DEFAULT_SORT[account_filter]
    if resolved_sort not in ACCOUNT_FILTER_SORTS[account_filter]:
        raise ReportGenerationError("invalid report account sort order")
    collections = _account_collections(
        account_projection,
        account_repository=account_repository,
        current_task_id=current_task_id,
    )
    group = collections[account_filter]
    filter_counts = _account_filter_counts(collections)
    if group["status"] != "available":
        return {
            "status": "unavailable",
            "filter": account_filter,
            "action_label": group["action_label"],
            "drawer_title": group["drawer_title"],
            "basis_label": group.get("basis_label"),
            "unavailable_message": group["unavailable_message"],
            "entries": [],
            "total_count": 0,
            "has_more": False,
            "next_cursor": None,
            "search": normalized_search,
            "sort": resolved_sort,
            "filter_counts": filter_counts,
        }
    candidates = list(group["all_entries"])
    if normalized_search:
        needle = normalized_search.casefold()
        candidates = [
            item
            for item in candidates
            if needle in str(item.get("display_name") or "").casefold()
        ]
    candidates = _sort_filtered_account_entries(
        candidates, account_filter=account_filter, sort_order=resolved_sort
    )
    items = [_public_account_entry(item) for item in candidates]
    start = 0
    if cursor:
        indexes = [
            index for index, item in enumerate(items) if item["entry_ref"] == cursor
        ]
        if len(indexes) != 1:
            raise ReportGenerationError("report account cursor is invalid")
        start = indexes[0] + 1
    page = items[start : start + int(limit)]
    has_more = start + len(page) < len(items)
    return {
        "status": "available",
        "filter": account_filter,
        "action_label": group["action_label"],
        "drawer_title": group["drawer_title"],
        "basis_label": group.get("basis_label"),
        "entries": page,
        "total_count": len(items),
        "has_more": has_more,
        "next_cursor": page[-1]["entry_ref"] if has_more and page else None,
        "search": normalized_search,
        "sort": resolved_sort,
        "filter_counts": filter_counts,
    }


def build_account_detail(
    account_projection: dict[str, Any] | None,
    *,
    entry_ref: str,
    account_repository: Any | None,
    current_task_id: str,
) -> dict[str, Any]:
    entries = (
        []
        if account_projection is None
        else account_projection.get("all_entries")
        or account_projection.get("entries")
        or []
    )
    matches = [item for item in entries if item.get("entry_ref") == entry_ref]
    if len(matches) != 1:
        raise ReportGenerationError("ReportAccountEntry was not found")
    entry = matches[0]
    internal_account_ref = str(entry.get("internal_account_ref") or "")
    if not internal_account_ref:
        raise ReportGenerationError("ReportAccountEntry has no internal Account binding")
    current_statistics = _entry_statistics(entry)
    if account_repository is None:
        return {
            "entry": _public_account_identity(entry),
            "current_report": {
                "status": "available",
                "statistics": current_statistics,
            },
            "authorized_investigations": {
                "status": "unavailable",
                "unavailable_message": "当前授权调查活动暂时不可用。",
            },
            "activity": {
                "status": "unavailable",
                "unavailable_message": "当前授权调查活动暂时不可用。",
            },
            "investigation_distribution": [],
            "primary_comment_targets": [],
        }
    overview = account_repository.overview(internal_account_ref)
    source_distribution = overview.get("activity_source_distribution") or []
    current_sources = [
        item
        for item in source_distribution
        if str(item.get("task_id") or "") == current_task_id
    ]
    if len(current_sources) != 1:
        raise ReportGenerationError(
            "ReportAccountEntry has no unique current Account corpus source"
        )
    current_corpus_statistics = _authorized_statistics(current_sources[0])
    for key in _ACCOUNT_METRIC_KEYS:
        value = current_corpus_statistics[key]
        if int(current_statistics.get(key) or 0) != value:
            raise ReportGenerationError(
                "ReportAccountEntry disagrees with the current Account corpus"
            )
    distribution = [
        {
            "investigation_name": str(item.get("source_task", {}).get("name") or "未命名调查"),
            "is_current_report": str(item.get("task_id") or "") == current_task_id,
            "statistics": _authorized_statistics(item),
        }
        for item in source_distribution
    ]
    investigation_count = len(distribution)
    aggregate = {
        key: sum(int(item["statistics"].get(key) or 0) for item in distribution)
        for key in _ACCOUNT_METRIC_KEYS
    }
    overview_statistics = overview.get("statistics") or {}
    aggregate["earliest_activity_at"] = overview_statistics.get("earliest_activity_at")
    aggregate["latest_activity_at"] = overview_statistics.get("latest_activity_at")
    return {
        "entry": _public_account_identity(entry),
        "current_report": {
            "status": "available",
            "statistics": current_statistics,
        },
        "authorized_investigations": {
            "status": "available",
            "investigation_count": investigation_count,
            "statistics": aggregate,
            "single_investigation_message": (
                "在当前授权范围内，该账号仅出现于本次调查。"
                if investigation_count == 1
                else None
            ),
        },
        "activity": {
            "status": "available",
            "earliest_activity_at": overview_statistics.get("earliest_activity_at"),
            "latest_activity_at": overview_statistics.get("latest_activity_at"),
            "latest_comment_at": overview_statistics.get("latest_comment_at"),
            "latest_published_at": overview_statistics.get("latest_published_at"),
        },
        "investigation_distribution": distribution,
        "primary_comment_targets": [
            {
                "target_ref": "account-target-" + stable_hash(
                    {
                        "schema_version": "r31-account-comment-target/v1",
                        "entry_ref": entry_ref,
                        "internal_target_ref": str(item.get("account_id") or ""),
                    }
                )[:16],
                "display_name": str(item.get("author_display_name") or "未记录昵称的账号"),
                "comment_count": int(item.get("comment_count") or 0),
                "commented_post_count": int(item.get("commented_post_count") or 0),
            }
            for item in (overview.get("comment_target_distribution") or [])[:5]
        ],
    }


def _authorized_statistics(value: dict[str, Any]) -> dict[str, Any]:
    output = {
        key: int(value.get(key) or 0)
        for key in _ACCOUNT_METRIC_KEYS
    }
    output["earliest_activity_at"] = value.get("earliest_activity_at")
    output["latest_activity_at"] = value.get("latest_activity_at")
    output["latest_comment_at"] = value.get("latest_comment_at")
    output["latest_published_at"] = value.get("latest_published_at")
    return output


def build_post_detail(document: dict[str, Any], *, post_ref: str) -> dict[str, Any]:
    post = _require_ref(_list(document, "posts"), "post_ref", post_ref, "Post")
    audit_ref = str(post.get("audit_finding_ref") or "")
    audit = _require_ref(
        _list(document, "audit_findings"),
        "audit_finding_ref",
        audit_ref,
        "AuditFinding",
    )
    evidence_by_ref = _index(_list(document, "evidence"), "evidence_ref")
    direct_evidence = [
        _public_evidence(evidence_by_ref[evidence_ref])
        for evidence_ref in audit.get("evidence_refs") or []
        if evidence_ref in evidence_by_ref
        and evidence_by_ref[evidence_ref].get("support_type") == "direct"
    ]
    finding_refs = sorted(
        str(finding.get("investigation_finding_ref"))
        for finding in _list(document, "investigation_findings")
        if any(
            membership.get("post_ref") == post_ref
            for membership in finding.get("post_memberships") or []
        )
    )
    return {
        **_public_post(post, audits_by_ref={audit_ref: audit}),
        "investigation_finding_refs": finding_refs,
        "direct_evidence": direct_evidence,
    }


def build_finding_evidence(
    document: dict[str, Any], *, investigation_finding_ref: str
) -> dict[str, Any]:
    finding = _require_ref(
        _list(document, "investigation_findings"),
        "investigation_finding_ref",
        investigation_finding_ref,
        "InvestigationFinding",
    )
    refs = _membership_evidence_refs(finding)
    evidence_by_ref = _index(_list(document, "evidence"), "evidence_ref")
    items = [
        _public_evidence(evidence_by_ref[ref])
        for ref in refs
        if ref in evidence_by_ref
        and evidence_by_ref[ref].get("support_type") == "direct"
    ]
    return {
        "investigation_finding_ref": investigation_finding_ref,
        "title": str(finding.get("title") or ""),
        "direct_evidence_count": len(items),
        "items": items,
    }


def build_appendix_page(
    document: dict[str, Any],
    *,
    view: str,
    finding_ref: str | None,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    if view not in APPENDIX_VIEWS:
        raise ReportGenerationError("invalid report appendix view")
    if not 1 <= int(limit) <= 100:
        raise ReportGenerationError("report appendix limit must be between 1 and 100")

    finding = None
    if finding_ref:
        finding = _require_ref(
            _list(document, "investigation_findings"),
            "investigation_finding_ref",
            finding_ref,
            "InvestigationFinding",
        )
    if view == "standalone" and finding_ref:
        raise ReportGenerationError("standalone appendix does not accept a Finding ref")

    if view == "evidence":
        evidence_by_ref = _index(_list(document, "evidence"), "evidence_ref")
        allowed = (
            set(_membership_evidence_refs(finding)) if finding is not None else None
        )
        items = [
            _public_evidence(item)
            for item in evidence_by_ref.values()
            if item.get("support_type") == "direct"
            and (allowed is None or item.get("evidence_ref") in allowed)
        ]
        items.sort(key=lambda item: item["evidence_ref"])
        return _paginate(
            items,
            ref_key="evidence_ref",
            cursor=cursor,
            limit=limit,
            view=view,
            finding_ref=finding_ref,
        )

    post_refs: set[str] | None = None
    if finding is not None:
        post_refs = {
            str(item.get("post_ref")) for item in finding.get("post_memberships") or []
        }
    elif view == "standalone":
        post_refs = {
            str(item.get("post_ref"))
            for item in _list(document, "standalone_risk_posts")
        }
    posts = _list(document, "posts")
    audits_by_ref = _index(_list(document, "audit_findings"), "audit_finding_ref")
    items = [
        _public_post(item, audits_by_ref=audits_by_ref)
        for item in posts
        if post_refs is None or item.get("post_ref") in post_refs
    ]
    items.sort(key=lambda item: item["post_ref"])
    return _paginate(
        items,
        ref_key="post_ref",
        cursor=cursor,
        limit=limit,
        view=view,
        finding_ref=finding_ref,
    )


def _section_projection(
    section: dict[str, Any],
    *,
    findings: list[dict[str, Any]],
    posts_by_ref: dict[str, dict[str, Any]],
    audits_by_ref: dict[str, dict[str, Any]],
    evidence_by_ref: dict[str, dict[str, Any]],
    standalone_items: list[dict[str, Any]],
) -> dict[str, Any]:
    section_ref = str(section.get("section_ref") or "")
    section_type = str(section.get("section_type") or "")
    presentation_kind = _presentation_kind(section_ref, section_type)
    output = {
        "section_ref": section_ref,
        "section_number": str(section.get("section_number") or ""),
        "parent_section_ref": section.get("parent_section_ref"),
        "section_type": section_type,
        "title": str(section.get("title") or ""),
        "paragraphs": [
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in section.get("paragraphs") or []
        ],
        "presentation_kind": presentation_kind,
    }
    if presentation_kind == "data_overview":
        output["presentation_paragraphs"] = [
            "以下数据均来自本次调查的冻结资料快照。"
        ]
    if section_type == "standalone_risk_posts":
        output["standalone_items"] = standalone_items
        output["standalone_count"] = len(standalone_items)
        return output
    if section_type == "audit_samples":
        refs = section.get("sample_post_refs") or []
        if not refs or any(ref not in posts_by_ref for ref in refs):
            raise ReportGenerationError("audit sample references are missing or invalid")
        output["sample_posts"] = [_public_post(posts_by_ref[ref], audits_by_ref=audits_by_ref) for ref in refs]
        output["sample_total"] = len(posts_by_ref)
        return output
    if section_type in {
        "risk_post_analysis",
        "safe_post_analysis",
        "pending_post_analysis",
    }:
        audit_refs = [
            str(ref)
            for claim in section.get("claims") or []
            for ref in claim.get("audit_finding_refs") or []
            if str(ref)
        ]
        post_refs = [
            str(audits_by_ref[ref].get("post_ref") or "")
            for ref in audit_refs
            if ref in audits_by_ref
        ]
        if (
            len(post_refs) != len(audit_refs)
            or any(ref not in posts_by_ref for ref in post_refs)
            or len(set(post_refs)) != len(post_refs)
        ):
            raise ReportGenerationError(
                "unified report group references are missing or invalid"
            )
        output["group_posts"] = [
            _public_post(posts_by_ref[ref], audits_by_ref=audits_by_ref)
            for ref in post_refs
        ]
        output["group_total"] = len(post_refs)
        output["group_summary"] = (
            output["paragraphs"][0] if output["paragraphs"] else ""
        )
        return output
    if section_type != "investigation_finding":
        return output

    audit_refs = _claim_refs(section, "audit_finding_refs")
    evidence_refs = _claim_refs(section, "evidence_refs")
    matches = []
    if audit_refs and evidence_refs:
        matches = [
            item
            for item in findings
            if _membership_audit_refs(item) == audit_refs
            and set(_membership_evidence_refs(item)) == evidence_refs
        ]
    if not matches and audit_refs and evidence_refs:
        # A saved narrative may cite representative evidence rather than every
        # member. Bind only a unique containing set; never infer from title/order.
        matches = [
            item for item in findings
            if audit_refs.issubset(_membership_audit_refs(item))
            and evidence_refs.issubset(set(_membership_evidence_refs(item)))
        ]
    if len(matches) != 1:
        output["finding_binding"] = {"status": "unavailable"}
        return output

    finding = matches[0]
    representative_refs = [
        str(item) for item in finding.get("representative_post_refs") or []
    ][:3]
    representative_posts = [
        _public_post(posts_by_ref[ref], audits_by_ref=audits_by_ref)
        for ref in representative_refs
        if ref in posts_by_ref
    ]
    direct_refs = [
        ref
        for ref in _membership_evidence_refs(finding)
        if ref in evidence_by_ref
        and evidence_by_ref[ref].get("support_type") == "direct"
    ]
    output["finding_binding"] = {
        "status": "available",
        "investigation_finding_ref": str(finding["investigation_finding_ref"]),
        "title": str(finding.get("title") or ""),
        "statement": str(finding.get("statement") or ""),
        "boundary_notes": [
            str(item) for item in finding.get("boundary_notes") or []
            if str(item).strip() not in _OMITTED_BOUNDARY_NOTES
        ],
        "related_post_count": len(finding.get("post_memberships") or []),
        "representative_post_count": len(
            finding.get("representative_post_refs") or []
        ),
        "direct_evidence_count": len(direct_refs),
        "representative_posts": representative_posts,
    }
    return output


def _account_groups(
    account_projection: dict[str, Any] | None,
    *,
    account_repository: Any | None,
    current_task_id: str,
) -> dict[str, Any]:
    if account_projection is None:
        return {"status": "unavailable"}
    collections = _account_collections(
        account_projection,
        account_repository=account_repository,
        current_task_id=current_task_id,
    )
    entries = collections["entries"]
    targets = collections["targets"]
    other_post_authors = collections["other_post_authors"]
    coverage = dict(
        account_projection.get("account_coverage_statistics")
        or account_projection.get("statistics")
        or {}
    )
    return {
        "status": "available",
        "coverage": coverage,
        "target_entries": [_public_account_entry(item) for item in targets],
        "post_author_entries": [
            _public_account_entry(item) for item in other_post_authors
        ],
        "comment_author_entries": [
            _public_account_entry(item)
            for item in collections["comment_authors"]
        ],
        "cross_investigation_commenters": _public_account_group(
            collections["cross_investigation_commenter"]
        ),
        "risk_commenters": _public_account_group(collections["risk_commenter"]),
        "index": {
            "available": True,
            "total_count": len(entries),
            "default_page_size": 20,
            "maximum_page_size": 100,
        },
    }


def _account_collections(
    account_projection: dict[str, Any] | None,
    *,
    account_repository: Any | None,
    current_task_id: str,
) -> dict[str, Any]:
    entries = (
        []
        if account_projection is None
        else list(
            account_projection.get("entries")
            or account_projection.get("all_entries")
            or []
        )
    )
    targets = sorted(
        (item for item in entries if item.get("is_target_account")),
        key=lambda item: (
            item.get("target_display_ordinal") is None,
            int(item.get("target_display_ordinal") or 0),
            str(item.get("entry_ref") or ""),
        ),
    )
    target_refs = {str(item.get("entry_ref")) for item in targets}
    other_post_authors = sorted(
        (
            item
            for item in entries
            if "post_author" in (item.get("roles") or [])
            and item.get("entry_ref") not in target_refs
        ),
        key=lambda item: (
            -int(_entry_statistics(item).get("published_post_count") or 0),
            str(item.get("entry_ref") or ""),
        ),
    )
    occupied = target_refs | {
        str(item.get("entry_ref")) for item in other_post_authors
    }
    comment_authors = sorted(
        (
            item
            for item in entries
            if "comment_author" in (item.get("roles") or [])
        ),
        key=lambda item: (
            -int(_entry_statistics(item).get("risk_comment_count") or 0),
            -int(_entry_statistics(item).get("comment_count") or 0),
            str(item.get("entry_ref") or ""),
        ),
    )
    risk_candidates = _sort_risk_commenters(
        [
            item
            for item in entries
            if "comment_author" in (item.get("roles") or [])
            and int(_entry_statistics(item).get("risk_comment_count") or 0) > 0
        ]
    )
    body_risk = [
        item
        for item in risk_candidates
        if str(item.get("entry_ref") or "") not in occupied
    ][:5]
    body_risk_refs = {str(item.get("entry_ref") or "") for item in body_risk}

    cross_candidates: list[dict[str, Any]] = []
    cross_status = "unavailable"
    if account_repository is not None and current_task_id:
        bound_entries = {
            str(item.get("internal_account_ref") or ""): item
            for item in entries
            if str(item.get("internal_account_ref") or "")
            and "comment_author" in (item.get("roles") or [])
        }
        counts = account_repository.comment_investigation_counts(
            bound_entries, required_task_id=current_task_id
        )
        cross_candidates = _sort_cross_investigation_commenters(
            [
                {**item, "comment_investigation_count": int(counts.get(ref) or 0)}
                for ref, item in bound_entries.items()
                if int(counts.get(ref) or 0) >= 2
            ]
        )
        cross_status = "available"
    body_cross = [
        item
        for item in cross_candidates
        if str(item.get("entry_ref") or "") not in occupied | body_risk_refs
    ][:5]
    return {
        "entries": entries,
        "targets": targets,
        "other_post_authors": other_post_authors,
        "comment_authors": comment_authors,
        "post_author": {
            "status": "available",
            "all_entries": [item for item in entries if "post_author" in (item.get("roles") or [])],
            "total_count": sum("post_author" in (item.get("roles") or []) for item in entries),
            "action_label": "查看全部发布账号",
            "drawer_title": "发布账号索引",
            "basis_label": "基于本次发布报告",
        },
        "cross_investigation_commenter": {
            "status": cross_status,
            "entries": body_cross,
            "all_entries": cross_candidates,
            "total_count": len(cross_candidates),
            "action_label": "查看全部跨调查评论账号",
            "drawer_title": "跨调查评论账号",
            "basis_label": "基于当前可访问调查",
            "unavailable_message": "当前可访问调查的账号活动暂时不可用。",
        },
        "risk_commenter": {
            "status": "available",
            "entries": body_risk,
            "all_entries": risk_candidates,
            "total_count": len(risk_candidates),
            "action_label": "查看全部风险评论账号",
            "drawer_title": "风险评论账号",
            "basis_label": "基于本次发布报告",
        },
    }


def _sort_cross_investigation_commenters(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output = sorted(items, key=lambda item: str(item.get("entry_ref") or ""))
    output.sort(key=lambda item: str(_entry_statistics(item).get("latest_activity_at") or ""), reverse=True)
    output.sort(key=lambda item: int(_entry_statistics(item).get("comment_count") or 0), reverse=True)
    output.sort(key=lambda item: int(_entry_statistics(item).get("risk_comment_count") or 0), reverse=True)
    output.sort(key=lambda item: int(item.get("comment_investigation_count") or 0), reverse=True)
    return output


def _sort_risk_commenters(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = sorted(items, key=lambda item: str(item.get("entry_ref") or ""))
    output.sort(key=lambda item: str(_entry_statistics(item).get("latest_activity_at") or ""), reverse=True)
    output.sort(key=lambda item: int(_entry_statistics(item).get("commented_post_count") or 0), reverse=True)
    output.sort(key=lambda item: int(_entry_statistics(item).get("comment_count") or 0), reverse=True)
    output.sort(key=lambda item: int(_entry_statistics(item).get("risk_comment_count") or 0), reverse=True)
    return output


def _sort_filtered_account_entries(
    items: list[dict[str, Any]], *, account_filter: str, sort_order: str
) -> list[dict[str, Any]]:
    cross_priorities = {
        "investigation_count": (
            "investigation_count", "risk_comment_count", "comment_count", "latest_activity"
        ),
        "comment_count": (
            "comment_count", "investigation_count", "risk_comment_count", "latest_activity"
        ),
        "risk_comment_count": (
            "risk_comment_count", "investigation_count", "comment_count", "latest_activity"
        ),
        "latest_activity": (
            "latest_activity", "investigation_count", "risk_comment_count", "comment_count"
        ),
    }
    risk_priorities = {
        "risk_comment_count": (
            "risk_comment_count", "comment_count", "commented_post_count", "latest_activity"
        ),
        "comment_count": (
            "comment_count", "risk_comment_count", "commented_post_count", "latest_activity"
        ),
        "commented_post_count": (
            "commented_post_count", "risk_comment_count", "comment_count", "latest_activity"
        ),
        "latest_activity": (
            "latest_activity", "risk_comment_count", "comment_count", "commented_post_count"
        ),
    }
    priorities = (
        (sort_order,) if account_filter == "post_author" else
        cross_priorities[sort_order]
        if account_filter == "cross_investigation_commenter"
        else risk_priorities[sort_order]
    )

    def value(item: dict[str, Any], field: str) -> int | str:
        if field == "investigation_count":
            return int(item.get("comment_investigation_count") or 0)
        if field == "latest_activity":
            return str(_entry_statistics(item).get("latest_activity_at") or "")
        return int(_entry_statistics(item).get(field) or 0)

    output = sorted(items, key=lambda item: str(item.get("entry_ref") or ""))
    for field in reversed(priorities):
        output.sort(key=lambda item, field=field: value(item, field), reverse=True)
    return output


def _account_filter_counts(collections: dict[str, Any]) -> dict[str, Any]:
    return {
        account_filter: {
            "status": collections[account_filter]["status"],
            "total_count": int(collections[account_filter].get("total_count") or 0),
        }
        for account_filter in sorted(ACCOUNT_FILTERS)
    }


def _public_account_group(group: dict[str, Any]) -> dict[str, Any]:
    output = {
        key: group[key]
        for key in (
            "status",
            "total_count",
            "action_label",
            "drawer_title",
        )
        if key in group
    }
    if "basis_label" in group:
        output["basis_label"] = group["basis_label"]
    if group.get("status") == "available":
        output["entries"] = [
            _public_account_entry(item) for item in group.get("entries") or []
        ]
    else:
        output["entries"] = []
        output["unavailable_message"] = group["unavailable_message"]
    return output


def _public_account_entry(item: dict[str, Any]) -> dict[str, Any]:
    output = {
        "entry_ref": str(item.get("entry_ref") or ""),
        "display_name": str(item.get("display_name") or "未记录昵称的账号"),
        "roles": [str(role) for role in item.get("roles") or []],
        "is_target_account": bool(item.get("is_target_account")),
        "statistics": _entry_statistics(item),
    }
    if "comment_investigation_count" in item:
        output["comment_investigation_count"] = int(
            item.get("comment_investigation_count") or 0
        )
    return output


def _public_account_identity(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_ref": str(item.get("entry_ref") or ""),
        "display_name": str(item.get("display_name") or "未记录昵称的账号"),
        "is_target_account": bool(item.get("is_target_account")),
    }


def _entry_statistics(item: dict[str, Any]) -> dict[str, Any]:
    source = item.get("current_investigation_statistics") or item.get("statistics") or {}
    keys = (
        "published_post_count",
        "risk_published_post_count",
        "comment_count",
        "risk_comment_count",
        "commented_post_count",
        "commented_post_author_count",
        "earliest_activity_at",
        "latest_activity_at",
    )
    return {key: source.get(key) for key in keys}


def _platform_projection(snapshot: Any) -> dict[str, Any]:
    values = {
        str(item.payload.get("platform") or "").strip().lower()
        for item in snapshot.posts
        if str(item.payload.get("platform") or "").strip()
    }
    presentations = {
        tuple(_PLATFORM_PRESENTATION[value].items())
        for value in values
        if value in _PLATFORM_PRESENTATION
    }
    if len(values) != 1 or len(presentations) != 1:
        return {"status": "unavailable"}
    return {"status": "available", **dict(next(iter(presentations)))}


def _scope_projection(metadata: dict[str, Any]) -> dict[str, Any]:
    value = metadata.get("investigation_scope")
    if isinstance(value, str) and value.strip():
        return {"status": "available", "text": value.strip()}
    return {"status": "unavailable"}


def _statistics_projection(
    statistics: dict[str, Any],
    *,
    evidence: list[dict[str, Any]],
    investigation_findings: list[dict[str, Any]],
    comment_statistics: tuple[int | None, int | None],
) -> dict[str, Any]:
    decisions = _closed_counts(
        statistics.get("decision"), ("pass", "review", "reject")
    )
    risks = _closed_counts(
        statistics.get("risk_level"), ("high", "medium", "low", "none")
    )
    risk_total = sum(risks.values())
    risk_post_total = sum(risks[key] for key in ("high", "medium", "low"))
    maximum_risk_count = max(
        (risks[key] for key in ("high", "medium", "low")), default=0
    )
    direct_evidence = [
        item for item in evidence if str(item.get("support_type") or "") == "direct"
    ]
    direct_finding_count = len(
        {
            str(item.get("audit_finding_ref") or "")
            for item in direct_evidence
            if str(item.get("audit_finding_ref") or "")
        }
    )
    return {
        "canonical_posts": int(statistics.get("canonical_posts") or 0),
        "audit_finding_count": int(statistics.get("findings") or 0),
        "investigation_finding_count": len(investigation_findings),
        "independently_reviewed_comments": comment_statistics[0],
        "comment_own_risk": comment_statistics[1],
        "decision": decisions,
        "risk_level": risks,
        "risk_distribution": {
            "total": risk_total,
            "no_risk_summary": {
                "label": "无风险内容占比",
                "count": risks["none"],
                "percentage_label": _percentage_label(risks["none"], risk_total),
                "description": (
                    f"{risks['none']} 篇内容未发现风险，"
                    f"风险内容占全部样本的 "
                    f"{_percentage_label(risk_post_total, risk_total)}。"
                ),
            },
            "risk_bars": [
                {
                    "key": key,
                    "label": label,
                    "count": risks[key],
                    "width_label": (
                        _percentage_label(risks[key], maximum_risk_count)
                        if maximum_risk_count
                        else "0.0%"
                    ),
                }
                for key, label in _RISK_PRESENTATION
                if key != "none"
            ],
        },
        "evidence": {
            "direct": int(statistics.get("direct_evidence") or 0),
            "direct_finding_count": direct_finding_count,
            "indirect": int(statistics.get("indirect_evidence") or 0),
            "counter": int(statistics.get("counter_evidence") or 0),
        },
    }


def _investigation_summary(statistics: dict[str, Any]) -> dict[str, Any]:
    decision_parts = [
        f"{label} {statistics['decision'][key]} 篇"
        for key, label in _DECISION_PRESENTATION
        if statistics["decision"][key] > 0
    ]
    risk_parts = [
        f"{label} {statistics['risk_level'][key]} 篇"
        for key in ("none", "low", "medium", "high")
        for presentation_key, label in _RISK_PRESENTATION
        if presentation_key == key
    ]
    text = (
        f"当前调查任务纳入 {statistics['canonical_posts']} 篇已完成审核内容，"
        f"形成 {statistics['audit_finding_count']} 条有效审核结果；"
        f"审核决定为{'、'.join(decision_parts)}；"
        f"风险等级为{'、'.join(risk_parts)}；"
        f"冻结资料包含 {statistics['evidence']['direct']} 条直接研判依据，"
        f"覆盖 {statistics['evidence']['direct_finding_count']} 条帖子级审核发现。"
    )
    return {"status": "available", "paragraphs": [text]}


def _percentage_label(value: int, total: int) -> str:
    return f"{(value / total * 100) if total else 0:.1f}%"


def _public_post(
    post: dict[str, Any], *, audits_by_ref: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    audit_ref = str(post.get("audit_finding_ref") or "")
    audit = audits_by_ref.get(audit_ref) or {}
    return {
        "post_ref": str(post.get("post_ref") or ""),
        "title": str(post.get("title") or "未命名内容"),
        "content_summary": str(post.get("caption") or ""),
        "author_display_name": str(post.get("author_display_name") or ""),
        "audit_finding_ref": audit_ref,
        "decision": str(audit.get("decision") or ""),
        "risk_level": str(audit.get("risk_level") or ""),
        "audit_summary": str(audit.get("summary") or ""),
        "published_at": str(post.get("published_at") or ""),
        "source_url": str(post.get("source_url") or ""),
        "original_text": str(post.get("original_text") or ""),
        "transcripts": list(post.get("transcripts") or []),
    }


def _public_evidence(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_ref": str(item.get("evidence_ref") or ""),
        "post_ref": str(item.get("post_ref") or ""),
        "audit_finding_ref": str(item.get("audit_finding_ref") or ""),
        "support_type": str(item.get("support_type") or ""),
        "evidence_type": str(item.get("evidence_type") or ""),
        "original_text": str(item.get("original_text") or ""),
        "translated_text": str(item.get("translated_text") or ""),
        "summary": str(item.get("summary") or ""),
    }


def _standalone_items(
    document: dict[str, Any],
    *,
    posts_by_ref: dict[str, dict[str, Any]],
    audits_by_ref: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    output = []
    for item in _list(document, "standalone_risk_posts"):
        post_ref = str(item.get("post_ref") or "")
        audit_ref = str(item.get("audit_finding_ref") or "")
        post = posts_by_ref.get(post_ref)
        if post is None or post.get("audit_finding_ref") != audit_ref:
            raise ReportGenerationError("standalone risk Post relation is invalid")
        output.append(
            {
                **_public_post(post, audits_by_ref=audits_by_ref),
                "disposition_note": str(item.get("disposition_note") or ""),
            }
        )
    return output


def _paginate(
    items: list[dict[str, Any]],
    *,
    ref_key: str,
    cursor: str | None,
    limit: int,
    view: str,
    finding_ref: str | None,
) -> dict[str, Any]:
    start = 0
    if cursor:
        indexes = [index for index, item in enumerate(items) if item[ref_key] == cursor]
        if len(indexes) != 1:
            raise ReportGenerationError("report appendix cursor is invalid")
        start = indexes[0] + 1
    page = items[start : start + int(limit)]
    has_more = start + len(page) < len(items)
    return {
        "view": view,
        "finding_ref": finding_ref,
        "item_kind": "evidence" if view == "evidence" else "post",
        "items": page,
        "matched_count": len(items),
        "has_more": has_more,
        "cursor": page[-1][ref_key] if has_more and page else None,
    }


def _presentation_kind(section_ref: str, section_type: str) -> str:
    if section_type == "deterministic_statistics":
        return {
            "section-2-1": "content_scale",
            "section-2-2": "audit_risk_statistics",
        }.get(section_ref, "formal_paragraphs")
    return section_type


def _claim_refs(section: dict[str, Any], key: str) -> set[str]:
    return {
        str(ref)
        for claim in section.get("claims") or []
        for ref in claim.get(key) or []
        if str(ref)
    }


def _membership_audit_refs(finding: dict[str, Any]) -> set[str]:
    return {
        str(item.get("audit_finding_ref"))
        for item in finding.get("post_memberships") or []
        if item.get("audit_finding_ref")
    }


def _membership_evidence_refs(finding: dict[str, Any] | None) -> list[str]:
    if finding is None:
        return []
    return sorted(
        {
            str(ref)
            for item in finding.get("post_memberships") or []
            for ref in item.get("membership_evidence_refs") or []
            if str(ref)
        }
    )


def _closed_counts(value: Any, keys: Iterable[str]) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    return {key: int(source.get(key) or 0) for key in keys}


def _mapping(document: dict[str, Any], key: str) -> dict[str, Any]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise ReportGenerationError(f"structured Report {key} is missing")
    return value


def _list(document: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = document.get(key)
    if not isinstance(value, list):
        raise ReportGenerationError(f"structured Report {key} is missing")
    return [item for item in value if isinstance(item, dict)]


def _index(items: Iterable[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for item in items:
        ref = str(item.get(key) or "")
        if not ref or ref in output:
            raise ReportGenerationError(f"structured Report has invalid {key}")
        output[ref] = item
    return output


def _require_ref(
    items: Iterable[dict[str, Any]], key: str, ref: str, label: str
) -> dict[str, Any]:
    matches = [item for item in items if item.get(key) == ref]
    if len(matches) != 1:
        raise ReportGenerationError(f"{label} ref was not found")
    return matches[0]
