"""Read-only adapter for one authorized published ReportVersion SQLite store."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from types import MappingProxyType
from typing import Any, Mapping

from hermes_m0.domain import (
    AuthorDisplay,
    Evidence,
    EvidenceContent,
    EvidenceSource,
    Finding,
    FixtureProvenance,
    FrozenSnapshot,
    InvestigationFixture,
    Post,
    PostSource,
    ReportCase,
    ReportStatistics,
    ReportVersion,
)
from hermes_m0.repository import InvestigationRepository, RepositoryLookupError
from hermes_m0.source_projection import normalize_source_id, utc_timestamp


READ_ONLY_ACCESS_CONTRACT = "sqlite-mode=ro;immutable=1;query_only=ON"


class PublishedReportLoadError(ValueError):
    pass


@dataclass(frozen=True)
class FindingMembership:
    post_id: str
    audit_finding_id: str
    is_representative: bool
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class InvestigationFinding:
    id: str
    position: int
    title: str
    statement: str
    boundary_notes: tuple[str, ...]
    memberships: tuple[FindingMembership, ...]


@dataclass(frozen=True)
class StandaloneRiskPost:
    post_id: str
    audit_finding_id: str
    disposition_note: str


@dataclass(frozen=True)
class FrozenReportComment:
    id: str
    parent_post_id: str
    text: str
    audit_status: str
    risk_level: str
    risk_type: str
    published_at: str
    author_display_name: str
    author_source_key: str


@dataclass(frozen=True)
class PublishedReportAccountEntry:
    entry_ref: str
    account_id: str
    display_name: str
    roles: tuple[str, ...]
    current_statistics: Mapping[str, Any]
    target_display_order: int | None
    active_comment_display_order: int | None
    default_active_comment_visible: bool
    is_target_account: bool = False
    target_display_ordinal: int | None = None
    active_comment_rank: int | None = None
    active_comment_display_ordinal: int | None = None
    default_visible: bool = False


class PublishedReportRepository(InvestigationRepository):
    """Immutable in-memory projection loaded through a fenced SQLite connection."""

    is_published_report_store = True

    def __init__(
        self,
        fixture: InvestigationFixture,
        *,
        database_path: Path,
        database_sha256: str,
        content_hash: str,
        snapshot_hash: str,
        report_overview: str,
        post_content: Mapping[str, dict[str, Any]],
        finding_details: Mapping[str, dict[str, Any]],
        investigation_findings: tuple[InvestigationFinding, ...],
        standalone_risk_posts: tuple[StandaloneRiskPost, ...],
        report_account_entries: tuple[PublishedReportAccountEntry, ...] = (),
        report_account_projection_hash: str = "",
        report_comments: tuple[FrozenReportComment, ...] = (),
    ) -> None:
        super().__init__(fixture)
        self.database_path = database_path
        self.database_sha256 = database_sha256
        self.content_hash = content_hash
        self.snapshot_hash = snapshot_hash
        self.read_only_access_contract = READ_ONLY_ACCESS_CONTRACT
        self.report_overview = report_overview
        self._post_content = MappingProxyType(dict(post_content))
        self._finding_details = MappingProxyType(dict(finding_details))
        self._investigation_findings = MappingProxyType(
            {item.id: item for item in investigation_findings}
        )
        self._ordered_investigation_findings = investigation_findings
        self._standalone_risk_posts = standalone_risk_posts
        self._report_account_entries = report_account_entries
        self.report_account_projection_hash = report_account_projection_hash
        self._report_account_entries_by_ref = MappingProxyType(
            {item.entry_ref: item for item in report_account_entries}
        )
        self._report_comments = report_comments
        self._report_comments_by_post = MappingProxyType(
            {
                post_id: tuple(
                    item for item in report_comments if item.parent_post_id == post_id
                )
                for post_id in fixture.snapshot.post_ids
            }
        )

    @classmethod
    def load(
        cls,
        path: Path | str,
        *,
        report_version_id: str,
        expected_database_sha256: str,
        expected_content_hash: str,
        expected_snapshot_hash: str,
    ) -> "PublishedReportRepository":
        database_path = Path(path).expanduser().resolve(strict=True)
        if not database_path.is_file():
            raise PublishedReportLoadError("published report path is not a file")
        database_sha256 = _file_sha256(database_path)
        if database_sha256 != expected_database_sha256:
            raise PublishedReportLoadError("published report SQLite SHA-256 mismatch")

        uri = f"{database_path.as_uri()}?mode=ro&immutable=1"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=5.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
                raise PublishedReportLoadError("SQLite query_only could not be enabled")
            connection.set_authorizer(_sqlite_authorizer)
            loaded = _load_report_graph(
                connection,
                report_version_id=report_version_id,
                expected_content_hash=expected_content_hash,
                expected_snapshot_hash=expected_snapshot_hash,
            )
        except sqlite3.Error as exc:
            raise PublishedReportLoadError(
                "published report SQLite could not be loaded read-only"
            ) from exc
        finally:
            if "connection" in locals():
                connection.close()

        return cls(
            loaded["fixture"],
            database_path=database_path,
            database_sha256=database_sha256,
            content_hash=loaded["content_hash"],
            snapshot_hash=loaded["snapshot_hash"],
            report_overview=loaded["report_overview"],
            post_content=loaded["post_content"],
            finding_details=loaded["finding_details"],
            investigation_findings=loaded["investigation_findings"],
            standalone_risk_posts=loaded["standalone_risk_posts"],
            report_account_entries=loaded["report_account_entries"],
            report_account_projection_hash=loaded[
                "report_account_projection_hash"
            ],
            report_comments=loaded["report_comments"],
        )

    def post_content(self, post_id: str) -> dict[str, Any]:
        self.post(post_id)
        try:
            return self._post_content[post_id]
        except KeyError as exc:
            raise RepositoryLookupError("Unknown Post content") from exc

    def finding_detail_for_post(self, post_id: str) -> dict[str, Any]:
        finding = self.finding_for_post(post_id)
        try:
            return self._finding_details[finding.id]
        except KeyError as exc:
            raise RepositoryLookupError("Unknown Finding detail") from exc

    def ordered_investigation_findings(self) -> tuple[InvestigationFinding, ...]:
        return self._ordered_investigation_findings

    def investigation_finding(self, finding_id: str) -> InvestigationFinding:
        try:
            return self._investigation_findings[finding_id]
        except KeyError as exc:
            raise RepositoryLookupError("Unknown InvestigationFinding") from exc

    def finding_membership(
        self, finding_id: str, post_id: str
    ) -> FindingMembership:
        finding = self.investigation_finding(finding_id)
        for membership in finding.memberships:
            if membership.post_id == post_id:
                return membership
        raise RepositoryLookupError("Post is not a member of InvestigationFinding")

    def membership_evidence(
        self, finding_id: str, post_id: str
    ) -> tuple[Evidence, ...]:
        membership = self.finding_membership(finding_id, post_id)
        return tuple(self.evidence(item_id) for item_id in membership.evidence_ids)

    def standalone_risk_posts(self) -> tuple[StandaloneRiskPost, ...]:
        return self._standalone_risk_posts

    def is_standalone_risk_post(self, post_id: str) -> bool:
        return any(item.post_id == post_id for item in self._standalone_risk_posts)

    def report_account_entries(self) -> tuple[PublishedReportAccountEntry, ...]:
        return self._report_account_entries

    def report_account_entry(self, entry_ref: str) -> PublishedReportAccountEntry:
        try:
            return self._report_account_entries_by_ref[entry_ref]
        except KeyError as exc:
            raise RepositoryLookupError("Unknown ReportAccountEntry") from exc

    def risk_comments_for_post(
        self, post_id: str
    ) -> tuple[FrozenReportComment, ...]:
        self.post(post_id)
        return tuple(
            item
            for item in self._report_comments_by_post.get(post_id, ())
            if item.audit_status == "completed"
            and item.risk_level in {"low", "medium", "high"}
        )


_ALLOWED_TABLES = frozenset(
    {
        "reports",
        "report_versions",
        "report_source_snapshots",
        "report_snapshot_posts",
        "report_snapshot_findings",
        "report_snapshot_evidence",
        "report_investigation_findings",
        "report_categories",
        "report_category_displayed_members",
        "report_sections",
        "report_account_projections",
        "report_account_entries",
    }
)
_WRITE_ACTIONS = frozenset(
    action
    for action in (
        getattr(sqlite3, "SQLITE_INSERT", None),
        getattr(sqlite3, "SQLITE_UPDATE", None),
        getattr(sqlite3, "SQLITE_DELETE", None),
        getattr(sqlite3, "SQLITE_CREATE_INDEX", None),
        getattr(sqlite3, "SQLITE_CREATE_TABLE", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_INDEX", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_TABLE", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_VIEW", None),
        getattr(sqlite3, "SQLITE_CREATE_TRIGGER", None),
        getattr(sqlite3, "SQLITE_CREATE_VIEW", None),
        getattr(sqlite3, "SQLITE_DROP_INDEX", None),
        getattr(sqlite3, "SQLITE_DROP_TABLE", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_INDEX", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_TABLE", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_VIEW", None),
        getattr(sqlite3, "SQLITE_DROP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_DROP_VIEW", None),
        getattr(sqlite3, "SQLITE_ALTER_TABLE", None),
        getattr(sqlite3, "SQLITE_REINDEX", None),
        getattr(sqlite3, "SQLITE_ANALYZE", None),
        getattr(sqlite3, "SQLITE_ATTACH", None),
        getattr(sqlite3, "SQLITE_DETACH", None),
    )
    if action is not None
)


def _sqlite_authorizer(
    action: int,
    first: str | None,
    _second: str | None,
    _database: str | None,
    _trigger: str | None,
) -> int:
    if action in _WRITE_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ and first not in _ALLOWED_TABLES:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _load_report_graph(
    connection: sqlite3.Connection,
    *,
    report_version_id: str,
    expected_content_hash: str,
    expected_snapshot_hash: str,
) -> dict[str, Any]:
    version = _one(
        connection,
        "SELECT * FROM report_versions WHERE id = ?",
        (report_version_id,),
        "ReportVersion",
    )
    if str(version["status"]) != "published":
        raise PublishedReportLoadError("ReportVersion is not published")
    content_hash = str(version["content_hash"])
    if content_hash != expected_content_hash:
        raise PublishedReportLoadError("ReportVersion content hash mismatch")

    report = _one(
        connection,
        "SELECT * FROM reports WHERE id = ?",
        (version["report_id"],),
        "Report",
    )
    manifest = _one(
        connection,
        "SELECT * FROM report_source_snapshots WHERE report_version_id = ?",
        (report_version_id,),
        "Snapshot",
    )
    if str(version["source_snapshot_id"]) != str(manifest["id"]):
        raise PublishedReportLoadError("ReportVersion source Snapshot mismatch")
    if str(report["task_id"]) != str(manifest["task_id"]):
        raise PublishedReportLoadError("Report and Snapshot task mismatch")

    body_json = _json_object(version["body_json"], "ReportVersion body_json")
    audit_model = _require_object(body_json.get("audit_model"), "audit_model")
    human_report = _require_object(body_json.get("human_report"), "human_report")
    account_model_raw = body_json.get("account_model")
    account_model = (
        None
        if account_model_raw is None
        else _require_object(account_model_raw, "account_model")
    )
    sections = _require_list(audit_model.get("sections"), "audit_model sections")
    categories_json = _require_list(audit_model.get("categories"), "report categories")
    investigation_json = _require_list(
        audit_model.get("investigation_findings"), "InvestigationFindings"
    )
    standalone_json = _json_list(
        version["standalone_risk_posts_json"], "standalone risk posts"
    )
    if standalone_json != _require_list(
        audit_model.get("standalone_risk_posts"), "audit_model standalone risk posts"
    ):
        raise PublishedReportLoadError("standalone risk post projections disagree")
    recomputed_content_hash = _stable_hash(
        {
            "title": str(version["title"]),
            "body_markdown": str(version["body_markdown"]),
            "body_json": body_json,
            "sections": sections,
            "categories": categories_json,
            "investigation_findings": investigation_json,
            "standalone_risk_posts": standalone_json,
        }
    )
    if recomputed_content_hash != content_hash:
        raise PublishedReportLoadError("published ReportVersion content is hash-invalid")

    post_rows = list(
        connection.execute(
            "SELECT * FROM report_snapshot_posts WHERE report_version_id = ? ORDER BY rowid",
            (report_version_id,),
        )
    )
    finding_rows = list(
        connection.execute(
            "SELECT * FROM report_snapshot_findings WHERE report_version_id = ? ORDER BY rowid",
            (report_version_id,),
        )
    )
    evidence_rows = list(
        connection.execute(
            "SELECT * FROM report_snapshot_evidence WHERE report_version_id = ? ORDER BY rowid",
            (report_version_id,),
        )
    )
    post_payloads = _validated_payloads(post_rows, "post_ref", "Post")
    finding_payloads = _validated_payloads(finding_rows, "finding_ref", "Finding")
    evidence_payloads = _validated_payloads(
        evidence_rows, "evidence_ref", "Evidence"
    )
    if not post_rows or len(post_rows) != len(finding_rows):
        raise PublishedReportLoadError("Snapshot Post/Finding projection is incomplete")

    manifest_finding_ids = tuple(
        str(item)
        for item in _json_list(manifest["finding_ids_json"], "Snapshot Finding IDs")
    )
    manifest_evidence_ids = tuple(
        str(item)
        for item in _json_list(manifest["evidence_ids_json"], "Snapshot Evidence IDs")
    )
    if manifest_finding_ids != tuple(str(row["finding_ref"]) for row in finding_rows):
        raise PublishedReportLoadError("Snapshot Finding order mismatch")
    if manifest_evidence_ids != tuple(str(row["evidence_ref"]) for row in evidence_rows):
        raise PublishedReportLoadError("Snapshot Evidence order mismatch")

    posts_by_ref = {str(row["post_ref"]): row for row in post_rows}
    findings_by_ref = {str(row["finding_ref"]): row for row in finding_rows}
    finding_by_post: dict[str, str] = {}
    for row in finding_rows:
        post_ref = str(row["post_ref"])
        finding_ref = str(row["finding_ref"])
        if post_ref not in posts_by_ref or post_ref in finding_by_post:
            raise PublishedReportLoadError("Snapshot Finding/Post relation is invalid")
        finding_by_post[post_ref] = finding_ref
    if set(finding_by_post) != set(posts_by_ref):
        raise PublishedReportLoadError("Snapshot Finding/Post coverage is incomplete")

    evidence_by_post: dict[str, list[sqlite3.Row]] = defaultdict(list)
    relations = []
    for row in evidence_rows:
        evidence_ref = str(row["evidence_ref"])
        post_ref = str(row["post_ref"])
        finding_ref = str(row["finding_ref"])
        if (
            post_ref not in posts_by_ref
            or finding_ref not in findings_by_ref
            or finding_by_post[post_ref] != finding_ref
        ):
            raise PublishedReportLoadError("Snapshot Evidence parent is invalid")
        evidence_by_post[post_ref].append(row)
        relations.append(
            {
                "post_ref": post_ref,
                "finding_ref": finding_ref,
                "evidence_ref": evidence_ref,
            }
        )
    relation_hash = _stable_hash(relations)
    if str(manifest["relation_hash"]) != relation_hash:
        raise PublishedReportLoadError("Snapshot relation hash mismatch")
    snapshot_body = {
        "task_id": str(manifest["task_id"]),
        "display_name": str(manifest["display_name"]),
        "source_db_sha256": str(manifest["source_hash"]),
        "source_revision": str(manifest["source_revision"]),
        "audit_config_revision_id": str(manifest["configuration_revision_id"]),
        "posts": [str(row["payload_hash"]) for row in post_rows],
        "findings": [str(row["payload_hash"]) for row in finding_rows],
        "evidence": [str(row["payload_hash"]) for row in evidence_rows],
        "relations": relation_hash,
    }
    snapshot_hash = _stable_hash(snapshot_body)
    if snapshot_hash != str(manifest["snapshot_hash"]):
        raise PublishedReportLoadError("Snapshot hash does not match frozen payloads")
    if snapshot_hash != expected_snapshot_hash:
        raise PublishedReportLoadError("authorized Snapshot hash mismatch")

    investigations = _load_investigation_findings(
        connection,
        report_version_id=report_version_id,
        body_items=investigation_json,
        posts_by_ref=posts_by_ref,
        findings_by_ref=findings_by_ref,
        evidence_rows=evidence_rows,
    )
    standalone = _load_standalone(
        standalone_json,
        posts_by_ref=posts_by_ref,
        findings_by_ref=findings_by_ref,
        finding_by_post=finding_by_post,
    )
    categories = _load_categories(
        connection,
        report_version_id=report_version_id,
        body_items=categories_json,
        investigations=investigations,
    )
    _validate_risk_coverage(
        investigations,
        standalone,
        finding_rows=finding_rows,
        finding_payloads=finding_payloads,
        audit_model=audit_model,
    )
    report_overview = _load_sections(
        connection,
        report_version_id=report_version_id,
        body_sections=sections,
    )
    report_account_entries, report_account_projection_hash = _load_report_accounts(
        connection,
        report_version_id=report_version_id,
        account_model=account_model,
        snapshot_hash=snapshot_hash,
    )

    captured_at = _parse_datetime(str(manifest["generated_at"]), "Snapshot generated_at")
    revision_by_post = {
        str(row["post_ref"]): str(row["payload_hash"]) for row in post_rows
    }
    posts: list[Post] = []
    post_content: dict[str, dict[str, Any]] = {}
    report_comments: list[FrozenReportComment] = []
    report_comment_ids: set[str] = set()
    for row in post_rows:
        post_ref = str(row["post_ref"])
        payload = post_payloads[post_ref]
        projected_content = _project_post_content(payload)
        post_content[post_ref] = projected_content
        for comment in _project_frozen_comments(post_ref, payload):
            if comment.id in report_comment_ids:
                raise PublishedReportLoadError(
                    "Report Snapshot contains a duplicate Comment identity"
                )
            report_comment_ids.add(comment.id)
            report_comments.append(comment)
        posts.append(
            Post(
                id=post_ref,
                revision_id=revision_by_post[post_ref],
                title=str(payload.get("display_title") or "标题不可用"),
                body=_post_preview_text(projected_content),
                author=AuthorDisplay(
                    display_name=str(payload.get("author") or "未知作者"),
                    avatar_url="",
                    platform=str(payload.get("platform") or ""),
                    account_id="",
                ),
                source=PostSource(
                    platform=str(payload.get("platform") or ""),
                    url=str(payload.get("url") or ""),
                    published_at=str(payload.get("published_at") or ""),
                    captured_at=_parse_optional_datetime(
                        payload.get("captured_at"), "Post captured_at"
                    ),
                    analyzed_at=_parse_optional_datetime(
                        payload.get("analyzed_at"), "Post analyzed_at"
                    ),
                ),
                finding_id=finding_by_post[post_ref],
            )
        )

    findings: list[Finding] = []
    finding_details: dict[str, dict[str, Any]] = {}
    for row in finding_rows:
        finding_ref = str(row["finding_ref"])
        post_ref = str(row["post_ref"])
        payload = finding_payloads[finding_ref]
        completed_at = _parse_datetime(
            str(payload.get("completed_at") or manifest["generated_at"]),
            "Finding completed_at",
        )
        finding = Finding(
            id=finding_ref,
            post_id=post_ref,
            post_revision_id=revision_by_post[post_ref],
            decision=str(payload.get("decision") or "pass"),
            risk_level=str(payload.get("risk_level") or "none"),
            categories=tuple(str(item) for item in payload.get("categories") or []),
            summary=str(payload.get("summary") or ""),
            completed_at=completed_at,
        )
        findings.append(finding)
        finding_details[finding_ref] = {
            "type": "audit_finding",
            "decision": finding.decision,
            "risk_level": finding.risk_level,
            "categories": list(finding.categories),
            "summary": finding.summary,
            "risk_basis": str(payload.get("risk_basis") or ""),
            "completed_at": completed_at.isoformat(),
        }

    evidence: list[Evidence] = []
    evidence_type_counts: Counter[str] = Counter()
    for post_ref, rows in evidence_by_post.items():
        for ordinal, row in enumerate(rows, 1):
            evidence_ref = str(row["evidence_ref"])
            payload = evidence_payloads[evidence_ref]
            evidence_type = _evidence_type(str(payload.get("evidence_type") or ""))
            evidence_type_counts[evidence_type] += 1
            evidence.append(
                Evidence(
                    id=evidence_ref,
                    finding_id=str(row["finding_ref"]),
                    parent_post_id=post_ref,
                    parent_revision_id=revision_by_post[post_ref],
                    type=evidence_type,
                    ordinal=ordinal,
                    content=EvidenceContent(
                        original_text=str(payload.get("original_text") or ""),
                        translated_text=str(payload.get("translated_text") or ""),
                        summary=str(payload.get("reason") or payload.get("summary") or ""),
                        timestamp_start=None,
                        timestamp_end=None,
                        asset_path="",
                    ),
                    source=EvidenceSource(
                        locator="",
                        availability="available",
                        captured_at=captured_at,
                    ),
                )
            )

    statistics = _json_object(manifest["statistics_json"], "Snapshot statistics")
    report_statistics = ReportStatistics(
        case_count=len(categories),
        post_count=len(posts),
        finding_count=len(findings),
        evidence_count=len(evidence),
        decision_counts={
            str(key): int(value)
            for key, value in _require_object(
                statistics.get("decision"), "decision statistics"
            ).items()
        },
        risk_counts={
            str(key): int(value)
            for key, value in _require_object(
                statistics.get("risk_level"), "risk statistics"
            ).items()
        },
        evidence_type_counts=dict(sorted(evidence_type_counts.items())),
    )
    snapshot = FrozenSnapshot(
        id=str(manifest["id"]),
        captured_through=captured_at,
        post_ids=tuple(str(row["post_ref"]) for row in post_rows),
        post_revision_ids=tuple(
            revision_by_post[str(row["post_ref"])] for row in post_rows
        ),
        finding_ids=tuple(str(row["finding_ref"]) for row in finding_rows),
        evidence_ids=tuple(str(row["evidence_ref"]) for row in evidence_rows),
    )
    report_version = ReportVersion(
        id=report_version_id,
        report_id=str(version["report_id"]),
        snapshot_id=snapshot.id,
        revision=content_hash,
        public_revision="published",
        title=str(version["title"]),
        overview=report_overview,
        published_at=_parse_datetime(str(version["published_at"]), "published_at"),
        case_ids=tuple(item.id for item in categories),
        statistics=report_statistics,
    )
    fixture = InvestigationFixture(
        schema_version="hermes-investigation-fixture/v1",
        provenance=FixtureProvenance(
            source_workspace_head="",
            source_task_id=str(manifest["task_id"]),
            source_scope_ref=f"task:{manifest['task_id']}",
            projection_contract=READ_ONLY_ACCESS_CONTRACT,
            projection_snapshot_id=snapshot.id,
            projection_manifest_hash=snapshot_hash,
            projection_counts={
                "posts": len(posts),
                "findings": len(findings),
                "evidence": len(evidence),
            },
        ),
        snapshot=snapshot,
        report_version=report_version,
        cases=categories,
        posts=tuple(posts),
        findings=tuple(findings),
        evidence=tuple(evidence),
    )
    return {
        "fixture": fixture,
        "content_hash": content_hash,
        "snapshot_hash": snapshot_hash,
        "report_overview": report_overview,
        "post_content": post_content,
        "finding_details": finding_details,
        "investigation_findings": investigations,
        "standalone_risk_posts": standalone,
        "report_account_entries": report_account_entries,
        "report_account_projection_hash": report_account_projection_hash,
        "report_comments": tuple(
            sorted(
                report_comments,
                key=lambda item: (
                    item.parent_post_id,
                    item.published_at,
                    item.id,
                ),
            )
        ),
    }


def _load_report_accounts(
    connection: sqlite3.Connection,
    *,
    report_version_id: str,
    account_model: Mapping[str, Any] | None,
    snapshot_hash: str,
) -> tuple[tuple[PublishedReportAccountEntry, ...], str]:
    if account_model is None:
        return (), ""
    projection_row = _one(
        connection,
        "SELECT * FROM report_account_projections WHERE report_version_id = ?",
        (report_version_id,),
        "Report Account projection",
    )
    entry_rows = list(
        connection.execute(
            """
            SELECT * FROM report_account_entries
            WHERE report_version_id = ? ORDER BY full_index_order
            """,
            (report_version_id,),
        )
    )
    if not entry_rows:
        raise PublishedReportLoadError("Report Account projection has no entries")

    entries: list[dict[str, Any]] = []
    output: list[PublishedReportAccountEntry] = []
    schema_version = str(projection_row["schema_version"])
    is_account_overview = schema_version == "report-account-entry-r3.1/v1"
    for expected_order, row in enumerate(entry_rows, 1):
        if int(row["full_index_order"]) != expected_order:
            raise PublishedReportLoadError("Report Account full index order is invalid")
        roles = [
            str(item)
            for item in _json_list(row["roles_json"], "Report Account roles")
        ]
        if not roles or set(roles) - {"post_author", "comment_author"}:
            raise PublishedReportLoadError("Report Account role is invalid")
        statistics = _json_object(
            row["current_statistics_json"], "Report Account statistics"
        )
        required_statistics = (
            {
                "comment_count",
                "risk_comment_count",
                "commented_post_count",
                "commented_post_author_count",
                "earliest_activity_at",
                "latest_activity_at",
                "published_post_count",
                "risk_published_post_count",
            }
            if is_account_overview
            else {
                "comment_count",
                "commented_post_count",
                "commented_post_author_count",
                "earliest_activity_at",
                "latest_activity_at",
                "published_post_count",
            }
        )
        if set(statistics) != required_statistics:
            raise PublishedReportLoadError(
                "Report Account current-investigation statistics are incomplete"
            )
        if is_account_overview:
            item = {
                "entry_ref": str(row["entry_ref"]),
                "internal_account_ref": str(row["internal_account_ref"]),
                "display_name": str(row["display_name"]),
                "roles": roles,
                "is_target_account": bool(row["is_target_account"]),
                "current_investigation_statistics": statistics,
                "target_display_ordinal": (
                    int(row["target_display_ordinal"])
                    if row["target_display_ordinal"] is not None
                    else None
                ),
                "active_comment_rank": (
                    int(row["active_comment_rank"])
                    if row["active_comment_rank"] is not None
                    else None
                ),
                "active_comment_display_ordinal": (
                    int(row["active_comment_display_ordinal"])
                    if row["active_comment_display_ordinal"] is not None
                    else None
                ),
                "default_visible": bool(row["default_visible"]),
            }
        else:
            item = {
                "entry_ref": str(row["entry_ref"]),
                "internal_account_ref": str(row["internal_account_ref"]),
                "display_name": str(row["display_name"]),
                "roles": roles,
                "current_investigation_statistics": statistics,
                "target_display_order": (
                    int(row["target_display_order"])
                    if row["target_display_order"] is not None
                    else None
                ),
                "active_comment_display_order": (
                    int(row["active_comment_display_order"])
                    if row["active_comment_display_order"] is not None
                    else None
                ),
                "default_active_comment_visible": bool(
                    row["default_active_comment_visible"]
                ),
            }
        if _stable_hash(item) != str(row["content_hash"]):
            raise PublishedReportLoadError("ReportAccountEntry hash mismatch")
        entries.append(item)
        output.append(
            PublishedReportAccountEntry(
                entry_ref=item["entry_ref"],
                account_id=item["internal_account_ref"],
                display_name=item["display_name"],
                roles=tuple(roles),
                current_statistics=MappingProxyType(dict(statistics)),
                target_display_order=(
                    item.get("target_display_ordinal")
                    if is_account_overview
                    else item["target_display_order"]
                ),
                active_comment_display_order=(
                    item.get("active_comment_display_ordinal")
                    if is_account_overview
                    else item["active_comment_display_order"]
                ),
                default_active_comment_visible=(
                    item.get("default_visible", False)
                    if is_account_overview
                    else item["default_active_comment_visible"]
                ),
                is_target_account=bool(item.get("is_target_account", False)),
                target_display_ordinal=item.get("target_display_ordinal"),
                active_comment_rank=item.get("active_comment_rank"),
                active_comment_display_ordinal=item.get(
                    "active_comment_display_ordinal"
                ),
                default_visible=bool(item.get("default_visible", False)),
            )
        )

    statistics = _json_object(
        projection_row["statistics_json"], "Report Account projection statistics"
    )
    identity = {
        "schema_version": schema_version,
        "report_snapshot_hash": str(projection_row["report_snapshot_hash"]),
        "account_corpus_schema_version": str(
            projection_row["account_corpus_schema_version"]
        ),
        "account_corpus_revision": str(projection_row["account_corpus_revision"]),
        "account_fixture_sha256": str(projection_row["account_fixture_sha256"]),
        "account_task_snapshot_ref": str(
            projection_row["account_task_snapshot_ref"]
        ),
        "account_task_source_hash": str(
            projection_row["account_task_source_hash"]
        ),
        "occurrence_set_hash": str(projection_row["occurrence_set_hash"]),
        "default_active_comment_limit": int(
            projection_row["default_active_comment_limit"]
        ),
        "statistics": statistics,
        "entries": entries,
    }
    projection_hash = str(projection_row["projection_hash"])
    if _stable_hash(identity) != projection_hash:
        raise PublishedReportLoadError("Report Account projection hash mismatch")
    if identity["report_snapshot_hash"] != snapshot_hash:
        raise PublishedReportLoadError(
            "Report Account projection is bound to the wrong Snapshot"
        )
    if _public_report_account_projection(identity, projection_hash) != account_model:
        raise PublishedReportLoadError(
            "Report Account public and stored projections disagree"
        )
    return tuple(output), projection_hash


def _public_report_account_projection(
    identity: Mapping[str, Any], projection_hash: str
) -> dict[str, Any]:
    entries = identity["entries"]

    if identity["schema_version"] == "report-account-entry-r3.1/v1":
        return _public_report_account_overview_projection(identity, projection_hash)

    def public_entry(item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "entry_ref": item["entry_ref"],
            "display_name": item["display_name"],
            "roles": list(item["roles"]),
            "current_investigation_statistics": dict(
                item["current_investigation_statistics"]
            ),
            "target_display_order": item["target_display_order"],
            "active_comment_display_order": item[
                "active_comment_display_order"
            ],
            "default_active_comment_visible": item[
                "default_active_comment_visible"
            ],
        }

    targets = sorted(
        (item for item in entries if item["target_display_order"] is not None),
        key=lambda item: int(item["target_display_order"]),
    )
    active_comments = sorted(
        (
            item
            for item in entries
            if item["default_active_comment_visible"]
            and item["active_comment_display_order"] is not None
        ),
        key=lambda item: int(item["active_comment_display_order"]),
    )
    return {
        "schema_version": identity["schema_version"],
        "projection_hash": projection_hash,
        "statistics": dict(identity["statistics"]),
        "default_active_comment_limit": identity[
            "default_active_comment_limit"
        ],
        "groups": {
            "target_accounts": [public_entry(item) for item in targets],
            "active_comment_accounts": [
                public_entry(item) for item in active_comments
            ],
        },
        "full_account_index_available": True,
        "scope_boundary": (
            "报告账号统计仅覆盖当前调查；进入 Account Overview 后，"
            "默认查询当前用户已授权的全部调查数据。"
        ),
    }


def _public_report_account_overview_projection(
    identity: Mapping[str, Any], projection_hash: str
) -> dict[str, Any]:
    from backend.reporting.account_overview import public_account_overview_projection

    return public_account_overview_projection(
        {**dict(identity), "projection_hash": projection_hash}
    )


def _load_investigation_findings(
    connection: sqlite3.Connection,
    *,
    report_version_id: str,
    body_items: list[Any],
    posts_by_ref: Mapping[str, sqlite3.Row],
    findings_by_ref: Mapping[str, sqlite3.Row],
    evidence_rows: list[sqlite3.Row],
) -> tuple[InvestigationFinding, ...]:
    rows = list(
        connection.execute(
            "SELECT * FROM report_investigation_findings "
            "WHERE report_version_id = ? ORDER BY display_ordinal",
            (report_version_id,),
        )
    )
    evidence_by_ref = {str(row["evidence_ref"]): row for row in evidence_rows}
    output: list[InvestigationFinding] = []
    normalized_rows = []
    for expected_position, row in enumerate(rows, 1):
        finding_ref = str(row["finding_ref"])
        alias = finding_ref.rsplit(":", 1)[-1]
        if int(row["display_ordinal"]) != expected_position or alias != f"IF{expected_position}":
            raise PublishedReportLoadError("InvestigationFinding order is invalid")
        memberships_json = _json_list(
            row["post_memberships_json"], "InvestigationFinding memberships"
        )
        item = {
            "finding_ref": finding_ref,
            "alias": alias,
            "display_ordinal": expected_position,
            "title": str(row["title"]),
            "statement": str(row["statement"]),
            "post_memberships": memberships_json,
            "metric_refs": _json_list(row["metric_refs_json"], "metric refs"),
            "boundary_notes": _json_list(row["boundary_notes_json"], "boundary notes"),
        }
        if _stable_hash(item) != str(row["content_hash"]):
            raise PublishedReportLoadError("InvestigationFinding content hash mismatch")
        normalized_rows.append(item)
        memberships: list[FindingMembership] = []
        seen_posts: set[str] = set()
        for raw_membership in memberships_json:
            membership = _require_object(raw_membership, "post membership")
            post_ref = str(membership.get("post_ref") or "")
            audit_finding_ref = str(membership.get("audit_finding_ref") or "")
            evidence_ids = tuple(
                str(value)
                for value in _require_list(
                    membership.get("membership_evidence_refs"),
                    "membership Evidence refs",
                )
            )
            if post_ref in seen_posts or post_ref not in posts_by_ref:
                raise PublishedReportLoadError("InvestigationFinding Post membership is invalid")
            seen_posts.add(post_ref)
            finding_row = findings_by_ref.get(audit_finding_ref)
            if finding_row is None or str(finding_row["post_ref"]) != post_ref:
                raise PublishedReportLoadError("membership AuditFinding parent mismatch")
            if len(evidence_ids) != len(set(evidence_ids)):
                raise PublishedReportLoadError("membership Evidence contains duplicates")
            for evidence_ref in evidence_ids:
                evidence_row = evidence_by_ref.get(evidence_ref)
                if (
                    evidence_row is None
                    or str(evidence_row["post_ref"]) != post_ref
                    or str(evidence_row["finding_ref"]) != audit_finding_ref
                    or str(evidence_row["support_type"]) != "direct"
                ):
                    raise PublishedReportLoadError("membership Evidence parent mismatch")
            memberships.append(
                FindingMembership(
                    post_id=post_ref,
                    audit_finding_id=audit_finding_ref,
                    is_representative=bool(membership.get("is_representative")),
                    evidence_ids=evidence_ids,
                )
            )
        if not memberships or sum(item.is_representative for item in memberships) != 1:
            raise PublishedReportLoadError(
                "InvestigationFinding must have exactly one representative Post"
            )
        output.append(
            InvestigationFinding(
                id=finding_ref,
                position=expected_position,
                title=str(row["title"]),
                statement=str(row["statement"]),
                boundary_notes=tuple(str(item) for item in item["boundary_notes"]),
                memberships=tuple(memberships),
            )
        )
    if normalized_rows != body_items:
        raise PublishedReportLoadError("InvestigationFinding projections disagree")
    return tuple(output)


def _load_standalone(
    body_items: list[Any],
    *,
    posts_by_ref: Mapping[str, sqlite3.Row],
    findings_by_ref: Mapping[str, sqlite3.Row],
    finding_by_post: Mapping[str, str],
) -> tuple[StandaloneRiskPost, ...]:
    output = []
    seen: set[str] = set()
    for raw_item in body_items:
        item = _require_object(raw_item, "standalone risk post")
        post_ref = str(item.get("post_ref") or "")
        finding_ref = str(item.get("audit_finding_ref") or "")
        if (
            post_ref in seen
            or post_ref not in posts_by_ref
            or finding_ref not in findings_by_ref
            or finding_by_post.get(post_ref) != finding_ref
        ):
            raise PublishedReportLoadError("standalone risk Post relation is invalid")
        seen.add(post_ref)
        output.append(
            StandaloneRiskPost(
                post_id=post_ref,
                audit_finding_id=finding_ref,
                disposition_note=str(item.get("disposition_note") or ""),
            )
        )
    return tuple(output)


def _load_categories(
    connection: sqlite3.Connection,
    *,
    report_version_id: str,
    body_items: list[Any],
    investigations: tuple[InvestigationFinding, ...],
) -> tuple[ReportCase, ...]:
    rows = list(
        connection.execute(
            "SELECT * FROM report_categories WHERE report_version_id = ? "
            "ORDER BY display_ordinal",
            (report_version_id,),
        )
    )
    investigation_by_position = {item.position: item for item in investigations}
    normalized = []
    output = []
    for expected_position, row in enumerate(rows, 1):
        if int(row["display_ordinal"]) != expected_position:
            raise PublishedReportLoadError("ReportCategory order is invalid")
        members = list(
            connection.execute(
                "SELECT * FROM report_category_displayed_members "
                "WHERE category_id = ? ORDER BY display_ordinal",
                (row["id"],),
            )
        )
        member_payload = [
            {
                "display_ordinal": int(member["display_ordinal"]),
                "post_ref": str(member["post_ref"]),
                "finding_ref": str(member["finding_ref"]),
            }
            for member in members
        ]
        normalized.append(
            {
                "category_ref": str(row["category_ref"]),
                "display_ordinal": expected_position,
                "title": str(row["title"]),
                "membership_scope": str(row["membership_scope"]),
                "membership_complete": bool(row["membership_complete"]),
                "source_section_id": str(row["source_section_id"]),
                "members": member_payload,
            }
        )
        investigation = investigation_by_position.get(expected_position)
        representative = tuple(
            item.post_id for item in investigation.memberships if item.is_representative
        ) if investigation else ()
        displayed = tuple(item["post_ref"] for item in member_payload)
        if (
            investigation is None
            or str(row["title"]) != investigation.title
            or str(row["membership_scope"]) != "report_displayed_posts"
            or bool(row["membership_complete"])
            or displayed != representative
        ):
            raise PublishedReportLoadError(
                "ReportCategory displayed members do not match representatives"
            )
        output.append(
            ReportCase(
                id=str(row["id"]),
                position=expected_position,
                key="review",
                title=str(row["title"]),
                summary=investigation.statement,
                member_post_ids=displayed,
            )
        )
    if normalized != body_items:
        raise PublishedReportLoadError("ReportCategory projections disagree")
    return tuple(output)


def _load_sections(
    connection: sqlite3.Connection,
    *,
    report_version_id: str,
    body_sections: list[Any],
) -> str:
    rows = list(
        connection.execute(
            "SELECT * FROM report_sections WHERE report_version_id = ? ORDER BY sort_order",
            (report_version_id,),
        )
    )
    if len(rows) != len(body_sections):
        raise PublishedReportLoadError("report section projection is incomplete")
    overview = ""
    for row, raw_section in zip(rows, body_sections):
        section = _require_object(raw_section, "report section")
        if (
            str(row["section_id"]) != str(section.get("section_id") or "")
            or int(row["sort_order"]) != int(section.get("sort_order", row["sort_order"]))
            or str(row["title"]) != str(section.get("title") or "")
            or str(row["purpose"]) != str(section.get("purpose") or "")
            or str(row["body"]) != str(section.get("body") or "")
            or str(row["content_hash"]) != _stable_hash(section)
        ):
            raise PublishedReportLoadError("report section projection or hash mismatch")
        if str(row["section_id"]) == "overview":
            overview = str(row["body"])
    if not overview:
        raise PublishedReportLoadError("published report overview section is missing")
    return overview


def _validate_risk_coverage(
    investigations: tuple[InvestigationFinding, ...],
    standalone: tuple[StandaloneRiskPost, ...],
    *,
    finding_rows: list[sqlite3.Row],
    finding_payloads: Mapping[str, dict[str, Any]],
    audit_model: Mapping[str, Any],
) -> None:
    membership_posts = {
        membership.post_id
        for finding in investigations
        for membership in finding.memberships
    }
    standalone_posts = {item.post_id for item in standalone}
    if membership_posts & standalone_posts:
        raise PublishedReportLoadError("standalone Post is also a Finding member")
    risk_posts = {
        str(row["post_ref"])
        for row in finding_rows
        if finding_payloads[str(row["finding_ref"])].get("decision")
        in {"review", "reject"}
        and finding_payloads[str(row["finding_ref"])].get("risk_level")
        in {"low", "medium", "high"}
    }
    if membership_posts | standalone_posts != risk_posts:
        raise PublishedReportLoadError("published risk Post coverage is incomplete")
    if audit_model.get("risk_post_coverage_complete") is not True:
        raise PublishedReportLoadError("published risk coverage flag is not complete")


def _project_post_content(payload: Mapping[str, Any]) -> dict[str, Any]:
    source_content = _require_object(payload.get("source_content"), "Post source content")
    videos = []
    for position, raw_video in enumerate(source_content.get("video_results") or [], 1):
        video = _require_object(raw_video, "Post video")
        transcript = video.get("transcript")
        transcript = transcript if isinstance(transcript, dict) else {}
        translation = transcript.get("translation")
        translation = translation if isinstance(translation, dict) else {}
        translated_text = str(translation.get("text") or transcript.get("text_zh") or "")
        original_text = str(transcript.get("text") or "")
        if bool(translation.get("translated")) and translated_text:
            label = "已有中文语音转写"
            text = translated_text
        elif translated_text:
            label = "已有中文语音转写"
            text = translated_text
        elif original_text:
            label = "原语言语音转写"
            text = original_text
        else:
            label = "当前没有可用语音转写"
            text = ""
        videos.append(
            {
                "position": position,
                "speech_transcript": {"label": label, "text": text},
            }
        )
    return {
        "author_caption": str(payload.get("caption") or ""),
        "videos": videos,
    }


def _project_frozen_comments(
    post_ref: str, payload: Mapping[str, Any]
) -> tuple[FrozenReportComment, ...]:
    comments = payload.get("comments")
    if comments is None:
        raw_payload = _require_object(
            payload.get("raw_content_payload"), "Post raw content payload"
        )
        comments = raw_payload.get("comments") or []
    if not isinstance(comments, list):
        raise PublishedReportLoadError("Snapshot Comment projection is not an array")
    output: list[FrozenReportComment] = []
    seen: set[str] = set()
    for value in comments:
        comment = _require_object(value, "Snapshot Comment")
        comment_id = normalize_source_id(comment.get("comment_id"))
        if not comment_id or comment_id in seen:
            raise PublishedReportLoadError(
                "Snapshot Comment identities must be non-empty and unique per Post"
            )
        seen.add(comment_id)
        audit_status = normalize_source_id(comment.get("audit_status")).lower()
        risk_level = normalize_source_id(comment.get("risk_level")).lower()
        if not audit_status and not risk_level:
            audit_status = "unavailable"
            risk_level = "unavailable"
        elif audit_status not in {"completed", "failed", "pending", "queued"}:
            raise PublishedReportLoadError("Snapshot Comment audit status is invalid")
        elif risk_level not in {"none", "low", "medium", "high"}:
            raise PublishedReportLoadError("Snapshot Comment risk level is invalid")
        try:
            published_at = utc_timestamp(comment.get("create_time"))
        except ValueError as exc:
            raise PublishedReportLoadError(
                "Snapshot Comment published_at is invalid"
            ) from exc
        output.append(
            FrozenReportComment(
                id=comment_id,
                parent_post_id=post_ref,
                text=str(comment.get("content") or ""),
                audit_status=audit_status,
                risk_level=risk_level,
                risk_type=normalize_source_id(comment.get("risk_type")),
                published_at=published_at,
                author_display_name=normalize_source_id(comment.get("nickname")),
                author_source_key=normalize_source_id(comment.get("sec_uid")),
            )
        )
    return tuple(sorted(output, key=lambda item: (item.published_at, item.id)))


def _post_preview_text(content: Mapping[str, Any]) -> str:
    caption = str(content.get("author_caption") or "")
    if caption:
        return caption
    for video in content.get("videos") or []:
        text = str((video.get("speech_transcript") or {}).get("text") or "")
        if text:
            return text
    return ""


def _validated_payloads(
    rows: list[sqlite3.Row], ref_column: str, label: str
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        ref = str(row[ref_column])
        if ref in output:
            raise PublishedReportLoadError(f"duplicate Snapshot {label}")
        payload = _json_object(row["payload_json"], f"Snapshot {label} payload")
        if _stable_hash(payload) != str(row["payload_hash"]):
            raise PublishedReportLoadError(f"Snapshot {label} payload hash mismatch")
        output[ref] = payload
    return output


def _evidence_type(value: str) -> str:
    return {
        "comment": "comment",
        "visual": "keyframe",
        "image": "keyframe",
        "text": "text",
        "asr": "asr",
        "ocr": "ocr",
    }.get(value, "text")


def _one(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...],
    label: str,
) -> sqlite3.Row:
    rows = list(connection.execute(query, parameters))
    if len(rows) != 1:
        raise PublishedReportLoadError(f"expected exactly one {label}")
    return rows[0]


def _json_object(value: Any, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise PublishedReportLoadError(f"{label} is invalid JSON") from exc
    return _require_object(parsed, label)


def _json_list(value: Any, label: str) -> list[Any]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise PublishedReportLoadError(f"{label} is invalid JSON") from exc
    return _require_list(parsed, label)


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PublishedReportLoadError(f"{label} is not an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise PublishedReportLoadError(f"{label} is not an array")
    return value


def _parse_datetime(value: str, label: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise PublishedReportLoadError(f"{label} is invalid") from exc


def _parse_optional_datetime(value: Any, label: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return _parse_datetime(raw, label)


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
