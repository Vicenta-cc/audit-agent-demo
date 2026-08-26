from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from backend.audit_agent.config import settings
from backend.domain.contracts import (
    DomainWarning,
    EvidenceView,
    FindingEvidenceLinkValidation,
    FindingFilters,
    FindingPage,
    FindingView,
    SourceEnvelope,
    SourceFormat,
    SourceRecord,
    SourceStatus,
)
from backend.domain.evidence_adapter import EvidenceAdaptationResult, EvidenceAdapter, EvidenceContext
from backend.domain.identity import (
    make_finding_id,
    parse_evidence_id,
    parse_finding_id,
    stable_hash,
)
from backend.domain.warnings import DataQuality, normalize_data_quality


class DomainRecordNotFoundError(KeyError):
    pass


class DomainRepository:
    """Read-only access to normalized domain views backed by audit_index.sqlite3."""

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        outputs_dir: Path | None = None,
        evidence_adapter: EvidenceAdapter | None = None,
    ):
        self.db_path = (db_path or (settings.data_dir / "audit_index.sqlite3")).resolve()
        if not self.db_path.is_file():
            raise FileNotFoundError(self.db_path)
        self.outputs_dir = (outputs_dir or settings.outputs_dir).resolve()
        self.evidence_adapter = evidence_adapter or EvidenceAdapter()

    def get_finding_view(self, audit_result_id: int) -> SourceEnvelope[FindingView]:
        row = self._get_audit_row(int(audit_result_id))
        if row is None:
            raise DomainRecordNotFoundError(f"audit result not found: {audit_result_id}")
        return self._build_finding_envelope(row)

    def list_finding_views(
        self,
        task_id: str,
        filters: FindingFilters | None = None,
        *,
        offset: int = 0,
        limit: int = 100,
        sort_by: str = "audit_result_id",
        sort_order: str = "asc",
    ) -> SourceEnvelope[FindingPage]:
        filters = filters or FindingFilters()
        offset = max(0, int(offset))
        limit = min(max(1, int(limit)), 1000)
        where = ["ar.job_id = ?", "j.archived = 0"]
        params: list[Any] = [task_id]
        if filters.decision:
            where.append("ar.decision = ?")
            params.append(filters.decision)
        if filters.risk_level:
            where.append("ar.risk_level = ?")
            params.append(filters.risk_level)
        if filters.primary_risk:
            where.append("json_valid(ar.result_json) AND json_extract(ar.result_json, '$.primary_risk') = ?")
            params.append(filters.primary_risk)
        if filters.category:
            where.append(
                "json_valid(ar.categories_json) "
                "AND EXISTS (SELECT 1 FROM json_each(ar.categories_json) WHERE value = ?)"
            )
            params.append(filters.category)
        if filters.min_risk_score is not None:
            minimum_score = filters.risk_score_min
            if minimum_score is None:
                minimum_score = filters.min_risk_score
            where.append(
                "json_valid(ar.result_json) "
                "AND CAST(json_extract(ar.result_json, '$.risk_score') AS REAL) >= ?"
            )
            params.append(float(minimum_score))
        elif filters.risk_score_min is not None:
            where.append(
                "json_valid(ar.result_json) "
                "AND CAST(json_extract(ar.result_json, '$.risk_score') AS REAL) >= ?"
            )
            params.append(float(filters.risk_score_min))
        if filters.risk_score_max is not None:
            where.append(
                "json_valid(ar.result_json) "
                "AND CAST(json_extract(ar.result_json, '$.risk_score') AS REAL) <= ?"
            )
            params.append(float(filters.risk_score_max))
        if filters.author:
            where.append(
                "(ar.author_key = ? OR (json_valid(ar.author_json) "
                "AND json_extract(ar.author_json, '$.nickname') = ?))"
            )
            params.extend((filters.author, filters.author))
        if filters.content_id is not None:
            where.append("ar.content_id = ?")
            params.append(int(filters.content_id))
        if filters.source_platform:
            where.append("ar.platform = ?")
            params.append(filters.source_platform)
        where_sql = " AND ".join(where)
        sort_columns = {
            "risk_score": "CAST(json_extract(ar.result_json, '$.risk_score') AS REAL)",
            "created_at": "ar.created_at",
            "audit_result_id": "ar.id",
        }
        if sort_by not in sort_columns:
            raise ValueError(f"unsupported finding sort: {sort_by}")
        normalized_order = str(sort_order or "").lower()
        if normalized_order not in {"asc", "desc"}:
            raise ValueError(f"unsupported finding sort order: {sort_order}")
        order_sql = f"{sort_columns[sort_by]} {normalized_order.upper()}, ar.id ASC"

        with self._connect() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS count FROM audit_results ar JOIN jobs j ON j.id = ar.job_id WHERE {where_sql}",
                params,
            ).fetchone()
            rows = conn.execute(
                f"""
                {self._audit_row_select()}
                WHERE {where_sql}
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        envelopes = [self._build_finding_envelope(row) for row in rows]
        warnings = self._merge_warnings(envelope.warnings for envelope in envelopes)
        sources = tuple(source for envelope in envelopes for source in envelope.sources)
        items = tuple(envelope.data for envelope in envelopes)
        return SourceEnvelope[FindingPage](
            data=FindingPage(
                items=items,
                total=int(total_row["count"] or 0) if total_row else 0,
                offset=offset,
                limit=limit,
            ),
            sources=sources,
            warnings=warnings,
            metadata={
                "candidate_evidence_count": sum(
                    int(envelope.metadata.get("candidate_evidence_count") or 0) for envelope in envelopes
                ),
                "normalized_evidence_count": sum(
                    int(envelope.metadata.get("normalized_evidence_count") or 0) for envelope in envelopes
                ),
                "duplicate_evidence_merged_count": sum(
                    int(envelope.metadata.get("duplicate_evidence_merged_count") or 0) for envelope in envelopes
                ),
            },
        )

    def get_task_record(self, task_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ? AND archived = 0",
                (str(task_id),),
            ).fetchone()
        if row is None:
            raise DomainRecordNotFoundError(f"task not found: {task_id}")
        return dict(row)

    def get_task_sql_statistics(self, task_id: str) -> dict[str, Any]:
        """Return deterministic counts from SQLite without materializing result_json."""
        self.get_task_record(task_id)
        with self._connect() as conn:
            content_row = conn.execute(
                "SELECT COUNT(*) AS count FROM task_contents WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            audit_row = conn.execute(
                "SELECT COUNT(*) AS count FROM audit_results WHERE job_id = ?",
                (task_id,),
            ).fetchone()
            decision_rows = conn.execute(
                """
                SELECT COALESCE(decision, '') AS value, COUNT(*) AS count
                FROM audit_results
                WHERE job_id = ?
                GROUP BY COALESCE(decision, '')
                ORDER BY value
                """,
                (task_id,),
            ).fetchall()
            risk_rows = conn.execute(
                """
                SELECT COALESCE(risk_level, '') AS value, COUNT(*) AS count
                FROM audit_results
                WHERE job_id = ?
                GROUP BY COALESCE(risk_level, '')
                ORDER BY value
                """,
                (task_id,),
            ).fetchall()
            category_rows = conn.execute(
                """
                SELECT CAST(category.value AS TEXT) AS value, COUNT(DISTINCT ar.id) AS count
                FROM audit_results ar, json_each(ar.categories_json) AS category
                WHERE ar.job_id = ? AND json_valid(ar.categories_json)
                GROUP BY CAST(category.value AS TEXT)
                ORDER BY value
                """,
                (task_id,),
            ).fetchall()
        return {
            "content_count": int(content_row["count"] or 0) if content_row else 0,
            "audit_result_count": int(audit_row["count"] or 0) if audit_row else 0,
            "decision_distribution": self._rows_to_distribution(decision_rows),
            "risk_level_distribution": self._rows_to_distribution(risk_rows),
            "category_distribution": self._rows_to_distribution(category_rows),
        }

    def get_audit_result_record(self, audit_result_id: int) -> dict[str, Any]:
        row = self._get_audit_row(int(audit_result_id))
        if row is None:
            raise DomainRecordNotFoundError(f"audit result not found: {audit_result_id}")
        record = dict(row)
        warnings: list[DomainWarning] = []
        record["result"] = self._load_result_json(
            str(row["result_json"] or ""),
            int(row["id"]),
            str(row["job_id"] or ""),
            warnings,
        )
        record["result_warnings"] = tuple(warnings)
        return record

    def get_task_path_warnings(self, task_id: str) -> tuple[DomainWarning, ...]:
        self.get_task_record(task_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    tc.content_id,
                    COALESCE(tc.raw_item_path, c.raw_item_path, '') AS raw_item_path,
                    ar.id AS audit_result_id
                FROM task_contents tc
                LEFT JOIN contents c ON c.id = tc.content_id
                LEFT JOIN audit_results ar
                    ON ar.job_id = tc.task_id AND ar.content_id = tc.content_id
                WHERE tc.task_id = ?
                ORDER BY tc.content_id
                """,
                (task_id,),
            ).fetchall()
        warnings = []
        for row in rows:
            raw_path = str(row["raw_item_path"] or "")
            if raw_path and Path(raw_path).is_file():
                continue
            warnings.append(
                DomainWarning(
                    code=DataQuality.DEGRADED_RAW_ITEM_MISSING,
                    message=f"raw item file is missing: {raw_path or '<unset>'}",
                    task_id=task_id,
                    audit_result_id=(
                        int(row["audit_result_id"])
                        if row["audit_result_id"] is not None
                        else None
                    ),
                    source_json_path="/raw_item_path",
                )
            )
        return self._merge_warnings((warnings,))

    def get_evidence_views(
        self,
        audit_result_id: int,
    ) -> SourceEnvelope[tuple[EvidenceView, ...]]:
        row = self._get_audit_row(int(audit_result_id))
        if row is None:
            raise DomainRecordNotFoundError(f"audit result not found: {audit_result_id}")
        finding_envelope, adaptation = self._build_finding(row)
        return SourceEnvelope[tuple[EvidenceView, ...]](
            data=adaptation.evidence,
            sources=finding_envelope.sources,
            warnings=finding_envelope.warnings,
            metadata=finding_envelope.metadata,
        )

    def get_evidence_view(self, evidence_id: str) -> SourceEnvelope[EvidenceView]:
        audit_result_id, local_id = parse_evidence_id(evidence_id)
        envelope = self.get_evidence_views(audit_result_id)
        for evidence in envelope.data:
            if local_id == evidence.local_evidence_id or local_id in evidence.local_evidence_aliases:
                return SourceEnvelope[EvidenceView](
                    data=evidence,
                    sources=envelope.sources,
                    warnings=envelope.warnings,
                    metadata=envelope.metadata,
                )
        raise DomainRecordNotFoundError(f"evidence not found: {evidence_id}")

    def validate_finding_evidence_link(
        self,
        finding_id: str,
        evidence_id: str,
    ) -> SourceEnvelope[FindingEvidenceLinkValidation]:
        finding_result_id = parse_finding_id(finding_id)
        evidence_result_id, _ = parse_evidence_id(evidence_id)
        finding_envelope = self.get_finding_view(finding_result_id)
        try:
            evidence_envelope = self.get_evidence_view(evidence_id)
        except DomainRecordNotFoundError:
            same_result = finding_result_id == evidence_result_id
            return SourceEnvelope(
                data=FindingEvidenceLinkValidation(
                    finding_id=finding_id,
                    evidence_id=evidence_id,
                    valid=False,
                    reason=(
                        "evidence is not present in the normalized finding"
                        if same_result
                        else "finding and evidence belong to different audit results"
                    ),
                    same_audit_result=same_result,
                    same_task=False,
                    finding_exists=True,
                    evidence_exists=False,
                    data_quality=(DataQuality.BROKEN_REFERENCE,),
                ),
                sources=finding_envelope.sources,
                warnings=finding_envelope.warnings,
            )

        evidence = evidence_envelope.data
        same_result = finding_result_id == evidence_result_id
        same_task = finding_envelope.data.task_id == evidence.task_id
        broken = any(
            warning.code == DataQuality.BROKEN_REFERENCE
            and warning.evidence_id == evidence.evidence_id
            for warning in evidence_envelope.warnings
        )
        valid = same_result and same_task and not broken
        if not same_result:
            reason = "finding and evidence belong to different audit results"
        elif not same_task:
            reason = "finding and evidence belong to different tasks"
        elif broken:
            reason = "evidence reference is degraded or broken"
        else:
            reason = "evidence belongs to finding"
        return SourceEnvelope(
            data=FindingEvidenceLinkValidation(
                finding_id=finding_id,
                evidence_id=evidence.evidence_id,
                valid=valid,
                reason=reason,
                same_audit_result=same_result,
                same_task=same_task,
                finding_exists=True,
                evidence_exists=True,
                support_type=evidence.support_type,
                data_quality=evidence.data_quality,
            ),
            sources=finding_envelope.sources,
            warnings=self._merge_warnings((finding_envelope.warnings, evidence_envelope.warnings)),
        )

    def active_task_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM jobs WHERE archived = 0").fetchone()
        return int(row["count"] or 0) if row else 0

    def audit_result_count(self, *, active_only: bool = True) -> int:
        with self._connect() as conn:
            if active_only:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM audit_results ar JOIN jobs j ON j.id = ar.job_id WHERE j.archived = 0"
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS count FROM audit_results").fetchone()
        return int(row["count"] or 0) if row else 0

    def list_active_audit_result_ids(self) -> tuple[int, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ar.id
                FROM audit_results ar
                JOIN jobs j ON j.id = ar.job_id
                WHERE j.archived = 0
                ORDER BY ar.id
                """
            ).fetchall()
        return tuple(int(row["id"]) for row in rows)

    def traceability_issues(self) -> tuple[dict[str, Any], ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    ar.id AS audit_result_id,
                    ar.job_id AS task_id,
                    ar.content_id,
                    ar.content_key,
                    CASE WHEN j.id IS NULL THEN 1 ELSE 0 END AS missing_task,
                    CASE WHEN c.id IS NULL THEN 1 ELSE 0 END AS missing_content,
                    CASE WHEN tc.id IS NULL THEN 1 ELSE 0 END AS missing_task_content
                FROM audit_results ar
                LEFT JOIN jobs j ON j.id = ar.job_id
                LEFT JOIN contents c ON c.id = ar.content_id
                LEFT JOIN task_contents tc ON tc.task_id = ar.job_id AND tc.content_id = ar.content_id
                WHERE j.id IS NULL OR c.id IS NULL OR tc.id IS NULL
                ORDER BY ar.id
                """
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        uri = f"{self.db_path.as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn

    def _get_audit_row(self, audit_result_id: int) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                f"""
                {self._audit_row_select()}
                WHERE ar.id = ? AND j.archived = 0
                """,
                (audit_result_id,),
            ).fetchone()

    def _audit_row_select(self) -> str:
        return """
            SELECT
                ar.*,
                c.id AS linked_content_id,
                c.content_key AS linked_content_key,
                COALESCE(tc.raw_item_path, c.raw_item_path, '') AS resolved_raw_item_path,
                CASE WHEN tc.id IS NULL THEN 0 ELSE 1 END AS has_task_content
            FROM audit_results ar
            JOIN jobs j ON j.id = ar.job_id
            LEFT JOIN contents c ON c.id = ar.content_id
            LEFT JOIN task_contents tc ON tc.task_id = ar.job_id AND tc.content_id = ar.content_id
        """

    def _build_finding_envelope(self, row: sqlite3.Row) -> SourceEnvelope[FindingView]:
        envelope, _ = self._build_finding(row)
        return envelope

    def _build_finding(
        self,
        row: sqlite3.Row,
    ) -> tuple[SourceEnvelope[FindingView], EvidenceAdaptationResult]:
        audit_result_id = int(row["id"])
        task_id = str(row["job_id"] or "")
        content_id = int(row["content_id"]) if row["content_id"] is not None else None
        warnings: list[DomainWarning] = []
        result = self._load_result_json(row["result_json"], audit_result_id, task_id, warnings)
        context = EvidenceContext(
            audit_result_id=audit_result_id,
            task_id=task_id,
            content_id=content_id,
            outputs_dir=self.outputs_dir,
            raw_item_path=str(row["resolved_raw_item_path"] or ""),
        )
        adaptation = self.evidence_adapter.adapt(result, context)
        warnings.extend(adaptation.warnings)

        if row["linked_content_id"] is None or not int(row["has_task_content"] or 0):
            warnings.append(
                DomainWarning(
                    code=DataQuality.BROKEN_REFERENCE,
                    message="audit result cannot be traced through contents/task_contents",
                    task_id=task_id,
                    audit_result_id=audit_result_id,
                    source_json_path="/content_id",
                )
            )

        categories = self._load_string_list(row["categories_json"])
        if not categories:
            categories = tuple(str(value) for value in result.get("categories") or [] if str(value or "").strip())
        matched_rule_ids = self._matched_rule_ids(result)
        risk_score = self._as_float(result.get("risk_score"))
        finding_id = make_finding_id(audit_result_id)
        source_hash = stable_hash(
            {
                "audit_result_id": audit_result_id,
                "task_id": task_id,
                "content_id": content_id,
                "content_key": row["content_key"],
                "platform": self._row_value(row, "platform"),
                "title": self._row_value(row, "title"),
                "url": self._row_value(row, "url"),
                "author_key": self._row_value(row, "author_key"),
                "analyzed_at": self._row_value(row, "analyzed_at"),
                "created_at": self._row_value(row, "created_at"),
                "audit_config_revision_id": self._row_value(row, "audit_config_revision_id"),
                "decision": row["decision"],
                "risk_level": row["risk_level"],
                "categories": categories,
                "result": result,
            }
        )
        quality = normalize_data_quality(warning.code for warning in warnings)
        finding = FindingView(
            finding_id=finding_id,
            audit_result_id=audit_result_id,
            task_id=task_id,
            content_id=content_id,
            content_key=str(row["content_key"] or ""),
            source_platform=str(self._row_value(row, "platform") or ""),
            content_title=str(
                self._row_value(row, "title")
                or result.get("content_title")
                or result.get("title")
                or ""
            ),
            content_url=str(self._row_value(row, "url") or result.get("url") or ""),
            author=str(self._row_value(row, "author_key") or ""),
            analyzed_at=str(self._row_value(row, "analyzed_at") or ""),
            created_at=str(self._row_value(row, "created_at") or ""),
            audit_config_revision_id=str(
                self._row_value(row, "audit_config_revision_id")
                or result.get("audit_config_revision_id")
                or ""
            ),
            decision=str(row["decision"] or result.get("decision") or "review"),
            risk_level=str(row["risk_level"] or result.get("risk_level") or "unknown"),
            risk_score=risk_score,
            primary_risk=str(result.get("primary_risk") or ""),
            categories=categories,
            matched_rule_ids=matched_rule_ids,
            summary=str(row["summary"] or result.get("summary") or ""),
            evidence_ids=tuple(evidence.evidence_id for evidence in adaptation.evidence),
            evidence_type_counts={
                evidence_type: sum(
                    1 for evidence in adaptation.evidence if evidence.evidence_type.value == evidence_type
                )
                for evidence_type in sorted({item.evidence_type.value for item in adaptation.evidence})
            },
            source_hash=source_hash,
            data_quality=quality,
        )
        formats = tuple(
            dict.fromkeys(
                source_format
                for evidence in adaptation.evidence
                for source_format in evidence.source_formats
            )
        )
        status = SourceStatus.LIVE if quality == (DataQuality.COMPLETE,) else SourceStatus.DEGRADED
        source = SourceRecord(
            task_id=task_id,
            audit_result_id=audit_result_id,
            finding_id=finding_id,
            content_id=content_id,
            evidence_ids=finding.evidence_ids,
            source_status=status,
            source_format=SourceFormat.AUDIT_RESULT,
            source_formats=formats,
            source_hash=source_hash,
        )
        warning_tuple = self._merge_warnings((warnings,))
        envelope = SourceEnvelope[FindingView](
            data=finding,
            sources=(source,),
            warnings=warning_tuple,
            metadata={
                "candidate_evidence_count": adaptation.candidate_count,
                "normalized_evidence_count": len(adaptation.evidence),
                "duplicate_evidence_merged_count": adaptation.duplicate_merged_count,
            },
        )
        return envelope, adaptation

    @staticmethod
    def _rows_to_distribution(rows: Iterable[sqlite3.Row]) -> dict[str, int]:
        return {
            str(row["value"] or "unknown"): int(row["count"] or 0)
            for row in rows
        }

    @staticmethod
    def _row_value(row: sqlite3.Row, key: str, default: Any = "") -> Any:
        return row[key] if key in row.keys() else default

    def _load_result_json(
        self,
        raw_value: str,
        audit_result_id: int,
        task_id: str,
        warnings: list[DomainWarning],
    ) -> dict[str, Any]:
        try:
            result = json.loads(raw_value or "{}")
        except json.JSONDecodeError as exc:
            warnings.append(
                DomainWarning(
                    code=DataQuality.UNSUPPORTED_FORMAT,
                    message=f"result_json is invalid: {exc}",
                    task_id=task_id,
                    audit_result_id=audit_result_id,
                    source_json_path="/result_json",
                )
            )
            return {}
        if not isinstance(result, dict):
            warnings.append(
                DomainWarning(
                    code=DataQuality.UNSUPPORTED_FORMAT,
                    message="result_json is not an object",
                    task_id=task_id,
                    audit_result_id=audit_result_id,
                    source_json_path="/result_json",
                )
            )
            return {}
        return result

    def _matched_rule_ids(self, result: dict[str, Any]) -> tuple[str, ...]:
        output: list[str] = []
        for collection_name in ("rule_matches", "score_breakdown"):
            for row in result.get(collection_name) or []:
                if not isinstance(row, dict):
                    continue
                rule_id = str(row.get("rule_id") or row.get("id") or "").strip()
                if rule_id and rule_id not in output:
                    output.append(rule_id)
        return tuple(output)

    def _load_string_list(self, raw_value: str) -> tuple[str, ...]:
        try:
            values = json.loads(raw_value or "[]")
        except json.JSONDecodeError:
            return ()
        if not isinstance(values, list):
            return ()
        return tuple(str(value) for value in values if str(value or "").strip())

    def _as_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _merge_warnings(
        self,
        warning_groups: Iterable[Iterable[DomainWarning]],
    ) -> tuple[DomainWarning, ...]:
        output = []
        seen = set()
        for warning in (item for group in warning_groups for item in group):
            key = (
                warning.code,
                warning.message,
                warning.task_id,
                warning.audit_result_id,
                warning.evidence_id,
                warning.source_json_path,
            )
            if key not in seen:
                seen.add(key)
                output.append(warning)
        return tuple(output)
