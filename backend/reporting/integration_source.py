from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from backend.domain.contracts import SourceFormat, SupportType
from backend.domain.evidence_adapter import EvidenceAdapter, EvidenceContext
from backend.domain.identity import make_evidence_id, make_finding_id, stable_hash
from hermes_m0.source_projection import (
    normalize_source_id,
    normalize_source_text,
    read_verified_raw_item,
    unix_timestamp,
    utc_timestamp,
)


TASK_ID = "3ad102e072f6"
BASELINE_TASK_ID = "2272c3692807"

MANUAL_REVIEW_EXPECTED = {
    "7536935733123763497": ("low", 1),
    "7632593025227203327": ("none", 0),
    "7636196225720562367": ("low", 1),
    "7636783717638666858": ("high", 7),
    "7637069400713867846": ("high", 8),
    "7637134122841831507": ("none", 0),
    "7637340745296418486": ("medium", 5),
    "7656203227280728554": ("medium", 2),
    "7656655127848140137": ("medium", 1),
    "7656656385367487942": ("medium", 1),
    "7656658969469181318": ("medium", 1),
    "7660739864656707327": ("none", 0),
}


def _json(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class SnapshotPost:
    ref: str
    canonical_key: str
    revision: str
    payload: dict[str, Any]
    payload_hash: str


@dataclass(frozen=True)
class SnapshotFinding:
    ref: str
    audit_result_id: int
    post_ref: str
    payload: dict[str, Any]
    payload_hash: str


@dataclass(frozen=True)
class SnapshotEvidence:
    ref: str
    audit_result_id: int
    post_ref: str
    finding_ref: str
    support_type: str
    source_formats: tuple[str, ...]
    payload: dict[str, Any]
    payload_hash: str


@dataclass(frozen=True)
class ImmutableReportSnapshot:
    snapshot_ref: str
    task_id: str
    display_name: str
    source_db_sha256: str
    source_revision: str
    audit_config_revision_id: str
    posts: tuple[SnapshotPost, ...]
    findings: tuple[SnapshotFinding, ...]
    evidence: tuple[SnapshotEvidence, ...]
    relation_hash: str
    snapshot_hash: str
    statistics: dict[str, Any]

    @property
    def post_by_ref(self) -> dict[str, SnapshotPost]:
        return {item.ref: item for item in self.posts}

    @property
    def finding_by_ref(self) -> dict[str, SnapshotFinding]:
        return {item.ref: item for item in self.findings}

    @property
    def evidence_by_ref(self) -> dict[str, SnapshotEvidence]:
        return {item.ref: item for item in self.evidence}


class CanonicalReportSource:
    """Read the audit projection through SQLite immutable/query_only mode."""

    def __init__(self, db_path: Path, outputs_dir: Path):
        self.db_path = db_path.resolve()
        self.outputs_dir = outputs_dir.resolve()
        if not self.db_path.is_file():
            raise FileNotFoundError(self.db_path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.db_path}?immutable=1", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def task_name(self, task_id: str) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT display_name FROM jobs WHERE id = ? AND archived = 0", (task_id,)
            ).fetchone()
        if row is None:
            raise ValueError(f"unknown task: {task_id}")
        return str(row["display_name"] or task_id)

    def has_explicit_creator_target(self, task_id: str) -> bool:
        """Return whether the frozen task was configured around a creator target."""
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT crawl_mode, creator_url, creator_id
                FROM jobs WHERE id = ? AND archived = 0
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"unknown task: {task_id}")
        return str(row["crawl_mode"] or "") == "creator" and bool(
            str(row["creator_url"] or row["creator_id"] or "").strip()
        )

    def creator_account_identity(self, task_id: str) -> dict[str, str] | None:
        """Resolve a creator task's configured stable Account identity."""
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT platform, crawl_mode, creator_url, creator_id
                FROM jobs WHERE id = ? AND archived = 0
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"unknown task: {task_id}")
        if str(row["crawl_mode"] or "").strip().lower() != "creator":
            return None

        platform = str(row["platform"] or "").strip().lower()
        if platform in {"dy", "douyin"}:
            platform = "douyin"
            source_namespace = "douyin.sec_uid"
        else:
            raise ValueError(
                f"unsupported creator Account identity platform: {platform or 'empty'}"
            )

        configured = str(row["creator_url"] or row["creator_id"] or "").strip()
        if not configured:
            raise ValueError("creator task is missing its configured Account identity")
        parsed = urlparse(configured)
        if parsed.scheme and parsed.netloc:
            segments = [
                unquote(item).strip() for item in parsed.path.split("/") if item
            ]
            try:
                user_index = segments.index("user")
                source_account_key = segments[user_index + 1]
            except (ValueError, IndexError) as exc:
                raise ValueError(
                    "creator URL does not contain a stable Account identity"
                ) from exc
        else:
            source_account_key = configured
        if not source_account_key:
            raise ValueError("creator task has an empty stable Account identity")
        return {
            "platform": platform,
            "source_namespace": source_namespace,
            "source_account_key": source_account_key,
        }

    def config(self, task_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_audit_config_revisions WHERE job_id = ? ORDER BY version DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"missing audit config revision for {task_id}")
        return {
            "id": str(row["id"]),
            "version": row["version"],
            "config_hash": row["config_hash"],
            "audit_config": _json(row["audit_config_json"], {}),
            "rule_snapshot": _json(row["rule_snapshot_json"], {}),
            "prompt_profile_snapshot": _json(row["prompt_profile_snapshot_json"], {}),
        }

    def canonical_rows(self, task_id: str) -> list[sqlite3.Row]:
        query = """
        SELECT ar.*, tc.raw_item_path AS tc_raw_item_path,
               tc.created_at AS tc_captured_at
        FROM audit_results AS ar
        JOIN task_contents AS tc
          ON tc.task_id = ar.job_id
         AND tc.audit_result_id = ar.id
         AND tc.analyze_status = 'completed'
        WHERE ar.job_id = ?
        ORDER BY ar.id
        """
        with self.connect() as connection:
            rows = list(connection.execute(query, (task_id,)))
        keys = [str(row["content_key"] or row["content_id"] or row["id"]) for row in rows]
        if len(keys) != len(set(keys)):
            raise ValueError("canonical audit rows contain duplicate content keys")
        return rows

    def coverage(self, task_id: str) -> dict[str, Any]:
        """Return public, sanitized coverage for completed and failed posts."""
        with self.connect() as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT c.content_key, c.note_id, tc.analyze_status
                    FROM task_contents AS tc
                    JOIN contents AS c ON c.id = tc.content_id
                    WHERE tc.task_id = ?
                    ORDER BY tc.id
                    """,
                    (task_id,),
                )
            )
        failure_records: dict[str, dict[str, Any]] = {}
        failure_dir = self.outputs_dir / task_id / "post_failures"
        if failure_dir.is_dir():
            for path in sorted(failure_dir.glob("*.json")):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(value, dict):
                    continue
                for identity in (value.get("content_key"), value.get("note_id")):
                    cleaned = str(identity or "").strip()
                    if cleaned:
                        failure_records[cleaned] = value

        excluded: list[dict[str, str]] = []
        for row in rows:
            if str(row["analyze_status"] or "") != "failed":
                continue
            post_id = str(row["note_id"] or row["content_key"] or "").strip()
            record = failure_records.get(str(row["content_key"] or "")) or failure_records.get(post_id) or {}
            error_code = self._public_failure_code(record)
            excluded.append(
                {
                    "post_id": post_id,
                    "note_id": post_id,
                    "analyze_status": "failed",
                    "stage": self._public_failure_stage(record.get("stage")),
                    "reason": self._public_failure_reason(error_code),
                    "error_code": error_code,
                }
            )
        return {
            "candidate_posts": len(rows),
            "selected_posts": sum(
                str(row["analyze_status"] or "") == "completed" for row in rows
            ),
            "excluded_failed_posts": excluded,
        }

    @staticmethod
    def _public_failure_code(record: dict[str, Any]) -> str:
        stored = str(record.get("error_code") or "").strip().lower()
        if stored and all(character.isalnum() or character == "_" for character in stored):
            return stored[:80]
        statuses = [
            int(value)
            for value in record.get("http_statuses") or []
            if isinstance(value, int) or str(value).isdigit()
        ]
        if statuses:
            return f"audit_http_{statuses[-1]}"
        error_types = {
            str(value or "")
            for value in [record.get("error_type"), *(record.get("cause_types") or [])]
        }
        if "FusionAuditContractError" in error_types:
            return "fusion_contract_invalid"
        if error_types.intersection(
            {"QwenTimeoutError", "Timeout", "TimeoutError", "ReadTimeout"}
        ):
            return "audit_timeout"
        return "post_audit_failed"

    @staticmethod
    def _public_failure_stage(value: Any) -> str:
        stage = str(value or "post_audit").strip().lower()
        if not stage or not all(character.isalnum() or character == "_" for character in stage):
            return "post_audit"
        return stage[:80]

    @staticmethod
    def _public_failure_reason(error_code: str) -> str:
        if error_code == "fusion_contract_invalid":
            return "审核结果未通过证据或格式校验"
        if error_code == "audit_timeout":
            return "审核请求超时，补试结束后仍未完成"
        if error_code.startswith("audit_http_401") or error_code.startswith("audit_http_403"):
            return "审核服务认证失败"
        if error_code.startswith("audit_http_4"):
            return "审核服务未接受本次请求"
        if error_code.startswith("audit_http_5"):
            return "审核服务暂时不可用"
        return "帖子审核未完成"

    def adapt_evidence(self, row: sqlite3.Row):
        context = EvidenceContext(
            audit_result_id=int(row["id"]),
            task_id=str(row["job_id"]),
            content_id=row["content_id"],
            outputs_dir=self.outputs_dir,
            raw_item_path=str(row["tc_raw_item_path"] or ""),
        )
        return EvidenceAdapter().adapt(_json(row["result_json"], {}), context).evidence


def build_immutable_snapshot(
    source: CanonicalReportSource,
    task_id: str = TASK_ID,
) -> ImmutableReportSnapshot:
    rows = source.canonical_rows(task_id)
    if not rows:
        raise ValueError(f"no canonical rows for {task_id}")
    config = source.config(task_id)
    posts: list[SnapshotPost] = []
    findings: list[SnapshotFinding] = []
    evidences: list[SnapshotEvidence] = []
    direct_count = 0
    indirect_count = 0
    counter_count = 0
    legacy_image_count = 0
    manual_matches = 0

    for row in rows:
        result = _json(row["result_json"], {})
        author = _json(row["author_json"], {})
        audit_result_id = int(row["id"])
        post_ref = f"post:{row['content_key'] or row['content_id'] or audit_result_id}"
        finding_ref = make_finding_id(audit_result_id)
        canonical_key = str(row["content_key"] or row["content_id"] or audit_result_id)
        raw_item = read_verified_raw_item(
            row["tc_raw_item_path"], expected_aweme_id=canonical_key
        )
        raw_source_title = normalize_source_id(
            (raw_item or {}).get("title") or (raw_item or {}).get("desc")
        )
        stored_source_title = normalize_source_id(row["title"])
        if (
            raw_source_title
            and stored_source_title
            and raw_source_title != stored_source_title
        ):
            raise ValueError(
                "raw and stored source titles disagree for content "
                f"{canonical_key}"
            )
        source_title = raw_source_title or stored_source_title
        audit_title = normalize_source_id(result.get("content_title"))
        if audit_title:
            display_title = audit_title
            display_title_source = "audit_generated"
        elif source_title:
            display_title = source_title
            display_title_source = "source"
        else:
            display_title = ""
            display_title_source = "unavailable"
        _, published_at = unix_timestamp((raw_item or {}).get("create_time"))
        captured_at = utc_timestamp(row["tc_captured_at"])
        analyzed_at = utc_timestamp(row["analyzed_at"])
        raw_comments = result.get("comments") or []
        if not isinstance(raw_comments, list):
            raise TypeError(f"comments are not an array for content {canonical_key}")
        comments: list[dict[str, Any]] = []
        comment_ids: set[str] = set()
        for source_comment in raw_comments:
            if not isinstance(source_comment, dict):
                raise TypeError(
                    f"comment is not an object for content {canonical_key}"
                )
            comment_id = normalize_source_id(source_comment.get("comment_id"))
            if not comment_id or comment_id in comment_ids:
                raise ValueError(
                    f"duplicate or empty Comment identity for content {canonical_key}"
                )
            comment_ids.add(comment_id)
            parent_key = normalize_source_id(source_comment.get("aweme_id"))
            if parent_key and parent_key != canonical_key:
                raise ValueError(
                    f"Comment {comment_id} points to the wrong parent Post"
                )
            _, comment_time = unix_timestamp(source_comment.get("create_time"))
            comment_audit_status = normalize_source_id(
                source_comment.get("audit_status")
            ).lower()
            comment_risk_level = normalize_source_id(
                source_comment.get("risk_level")
            ).lower()
            if not comment_audit_status and not comment_risk_level:
                comment_audit_status = "unavailable"
                comment_risk_level = "unavailable"
            elif comment_audit_status not in {
                "completed",
                "failed",
                "pending",
                "queued",
            }:
                raise ValueError(
                    f"Comment {comment_id} has invalid audit status"
                )
            elif comment_audit_status in {"failed", "pending", "queued"} and comment_risk_level in {
                "", "unknown", "unavailable"
            }:
                # Missing results are not a safe verdict. Preserve the audit
                # status and include the comment in incomplete coverage.
                comment_risk_level = "unavailable"
            elif comment_risk_level not in {"none", "low", "medium", "high"}:
                raise ValueError(
                    f"Comment {comment_id} has invalid risk level"
                )
            comments.append(
                {
                    "comment_id": comment_id,
                    "sec_uid": normalize_source_id(source_comment.get("sec_uid")),
                    "nickname": normalize_source_id(source_comment.get("nickname")),
                    "audit_status": comment_audit_status,
                    "risk_level": comment_risk_level,
                    "risk_score": source_comment.get("risk_score"),
                    "risk_type": normalize_source_id(source_comment.get("risk_type")),
                    "content": normalize_source_text(source_comment.get("content")),
                    "create_time": comment_time,
                }
            )
        comments.sort(key=lambda item: item["comment_id"])
        caption = result.get("desc_zh") or result.get("desc") or ""
        post_payload = {
            "canonical_key": canonical_key,
            "revision": str(row["updated_at"] or row["analyzed_at"] or audit_result_id),
            "platform": str(row["platform"] or ""),
            "source_title": source_title,
            "display_title": display_title,
            "display_title_source": display_title_source,
            "title": display_title,
            "published_at": published_at,
            "captured_at": captured_at,
            "analyzed_at": analyzed_at,
            "caption": caption,
            "url": row["url"] or result.get("url") or "",
            "author": author.get("nickname") or result.get("author", {}).get("nickname", ""),
            "author_display": {"nickname": author.get("nickname", ""), "platform": row["platform"] or ""},
            "source_content": {
                "title": result.get("title") or "",
                "title_zh": result.get("title_zh") or "",
                "desc": result.get("desc") or "",
                "desc_zh": result.get("desc_zh") or "",
                "video_results": result.get("video_results") if isinstance(result.get("video_results"), list) else [],
            },
            "raw_content_payload": result,
            "comments": comments,
        }
        if hasattr(source, "provenance_for_row"):
            post_payload["source_provenance"] = source.provenance_for_row(row)
        posts.append(
            SnapshotPost(post_ref, canonical_key, post_payload["revision"], post_payload, stable_hash(post_payload))
        )
        categories = _json(row["categories_json"], None)
        if not isinstance(categories, list):
            categories = result.get("categories") if isinstance(result.get("categories"), list) else []
        finding_payload = {
            "audit_result_id": audit_result_id,
            "decision": row["decision"] or result.get("decision") or "",
            "risk_level": row["risk_level"] or result.get("risk_level") or "",
            "risk_score": result.get("risk_score"),
            "categories": [str(item) for item in categories],
            "summary": row["summary"] or result.get("summary") or "",
            "risk_basis": result.get("risk_basis") or "",
            "primary_risk": result.get("primary_risk") or "",
            "completed_at": row["analyzed_at"] or "",
            "audit_config_revision_id": row["audit_config_revision_id"] or config["id"],
            "review_status": row["review_status"] or "",
            "review_note": row["review_note"] or "",
            "content_key": canonical_key,
            "content_id": row["content_id"],
            "source_platform": row["platform"] or "",
            "content_url": row["url"] or result.get("url") or "",
            "author": author.get("nickname") or result.get("author", {}).get("nickname", ""),
            "raw_effective_result": result,
        }
        if hasattr(source, "provenance_for_row"):
            finding_payload["source_provenance"] = source.provenance_for_row(row)
        findings.append(
            SnapshotFinding(finding_ref, audit_result_id, post_ref, finding_payload, stable_hash(finding_payload))
        )
        expected = MANUAL_REVIEW_EXPECTED.get(canonical_key)
        if expected and finding_payload["risk_level"] == expected[0] and len(result.get("evidence_items") or []) == expected[1]:
            manual_matches += 1
        for evidence in source.adapt_evidence(row):
            support = evidence.support_type.value
            if support == SupportType.INDIRECT.value:
                indirect_count += 1
            elif support == SupportType.COUNTER_EVIDENCE.value:
                counter_count += 1
            if support != SupportType.DIRECT.value:
                continue
            direct_count += 1
            source_formats = tuple(sorted({item.value for item in evidence.source_formats} | {evidence.source_format.value}))
            if SourceFormat.LEGACY_RISK_IMAGE.value in source_formats:
                legacy_image_count += 1
            evidence_ref = make_evidence_id(audit_result_id, evidence.local_evidence_id)
            payload = {
                "evidence_id": evidence_ref,
                "local_evidence_id": evidence.local_evidence_id,
                "evidence_type": evidence.evidence_type.value,
                "original_text": evidence.original_text,
                "translated_text": evidence.translated_text,
                "summary": evidence.summary,
                "reason": evidence.summary,
                "source_json_path": evidence.source_json_path,
                "asset_path": evidence.asset_path,
                "source_formats": list(source_formats),
                "audit_result_id": audit_result_id,
                "content_id": row["content_id"],
                "support_type": support,
            }
            evidences.append(
                SnapshotEvidence(
                    evidence_ref,
                    audit_result_id,
                    post_ref,
                    finding_ref,
                    support,
                    source_formats,
                    payload,
                    stable_hash(payload),
                )
            )

    decisions: dict[str, int] = {}
    risk_levels: dict[str, int] = {}
    for finding in findings:
        decisions[finding.payload["decision"]] = decisions.get(finding.payload["decision"], 0) + 1
        risk_levels[finding.payload["risk_level"]] = risk_levels.get(finding.payload["risk_level"], 0) + 1
    statistics = {
        "canonical_posts": len(posts),
        "findings": len(findings),
        "direct_evidence": len(evidences),
        "direct_legacy_risk_image": legacy_image_count,
        "indirect_evidence": indirect_count,
        "counter_evidence": counter_count,
        "decision": dict(sorted(decisions.items())),
        "risk_level": dict(sorted(risk_levels.items())),
        "manual_corrections": manual_matches,
    }
    coverage_source = getattr(source, "coverage", None)
    coverage = coverage_source(task_id) if callable(coverage_source) else coverage_source
    if isinstance(coverage, dict):
        statistics["source_coverage"] = coverage
        statistics["source_configuration"] = config
    relations = [
        {"post_ref": item.post_ref, "finding_ref": item.finding_ref, "evidence_ref": item.ref}
        for item in evidences
    ]
    relation_hash = stable_hash(relations)
    snapshot_body = {
        "task_id": task_id,
        "display_name": source.task_name(task_id),
        "source_db_sha256": source_sha256(source.db_path),
        "source_revision": stable_hash([(item.ref, item.revision, item.payload_hash) for item in posts]),
        "audit_config_revision_id": config["id"],
        "posts": [item.payload_hash for item in posts],
        "findings": [item.payload_hash for item in findings],
        "evidence": [item.payload_hash for item in evidences],
        "relations": relation_hash,
    }
    if isinstance(coverage, dict):
        snapshot_body["source_configuration_hash"] = stable_hash(config)
    snapshot_hash = stable_hash(snapshot_body)
    return ImmutableReportSnapshot(
        snapshot_ref=f"snapshot-{snapshot_hash[:16]}",
        task_id=task_id,
        display_name=source.task_name(task_id),
        source_db_sha256=snapshot_body["source_db_sha256"],
        source_revision=snapshot_body["source_revision"],
        audit_config_revision_id=config["id"],
        posts=tuple(posts),
        findings=tuple(findings),
        evidence=tuple(evidences),
        relation_hash=relation_hash,
        snapshot_hash=snapshot_hash,
        statistics=statistics,
    )
