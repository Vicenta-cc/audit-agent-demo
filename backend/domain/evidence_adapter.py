from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from backend.domain.contracts import (
    DomainWarning,
    EvidenceType,
    EvidenceView,
    SourceFormat,
    SupportType,
)
from backend.domain.identity import generated_local_evidence_id, make_evidence_id, stable_hash
from backend.domain.warnings import DataQuality, normalize_data_quality


# Highest-precedence records own conflicting values. Lower-precedence records may
# fill missing fields and are retained in source_formats/source_json_paths.
SOURCE_PRIORITY = {
    SourceFormat.EVIDENCE_ITEMS: 0,
    SourceFormat.EVIDENCE_CATALOG: 1,
    SourceFormat.EXTERNAL_EVIDENCE_INDEX: 2,
    SourceFormat.LEGACY_RISK_EVIDENCE: 3,
    SourceFormat.LEGACY_RISK_FRAME: 4,
    SourceFormat.LEGACY_RISK_IMAGE: 5,
    SourceFormat.RULE_MATCH: 6,
    SourceFormat.SCORE_BREAKDOWN: 7,
    SourceFormat.UNKNOWN: 8,
}


@dataclass(frozen=True)
class EvidenceContext:
    audit_result_id: int
    task_id: str
    content_id: int | None
    outputs_dir: Path
    raw_item_path: str = ""

    @property
    def task_root(self) -> Path:
        return (self.outputs_dir / self.task_id).resolve()


@dataclass(frozen=True)
class EvidenceAdaptationResult:
    evidence: tuple[EvidenceView, ...]
    source_anchors: tuple[tuple[str, str], ...]
    warnings: tuple[DomainWarning, ...]
    candidate_count: int
    duplicate_merged_count: int


@dataclass
class _Candidate:
    local_id: str
    aliases: set[str]
    evidence_type: EvidenceType
    original_text: str
    translated_text: str
    summary: str
    timestamp_start: float | None
    timestamp_end: float | None
    asset_path: str
    source_json_path: str
    source_json_paths: list[str]
    support_type: SupportType
    source_format: SourceFormat
    source_formats: list[SourceFormat]
    source_anchor: str
    dedup_keys: set[str]
    content_key: str
    priority: int
    data_quality: set[DataQuality] = field(default_factory=set)


class EvidenceAdapter:
    """Normalize persisted audit evidence without changing its source records.

    Read order is selected ``evidence_items``, embedded evidence catalog, external
    evidence index fallback, legacy risk arrays, then inline rule references. Raw
    comments/OCR/ASR/timeline entries enrich selected records; they are not bulk
    promoted into EvidenceViews.
    """

    _INDEX_LIST_KEYS = ("evidence_catalog", "comments", "ocr_items", "asr_segments", "timeline_frames")

    def adapt(self, result: dict[str, Any], context: EvidenceContext) -> EvidenceAdaptationResult:
        warnings: list[DomainWarning] = []
        common_quality: set[DataQuality] = set()
        self._check_raw_item(context, warnings, common_quality)

        embedded_index = result.get("evidence_index")
        if not isinstance(embedded_index, dict):
            embedded_index = {}
        external_warning_start = len(warnings)
        external_index, external_path_missing = self._load_external_index(result, context, warnings)
        common_quality.update(warning.code for warning in warnings[external_warning_start:])
        if external_path_missing:
            common_quality.add(DataQuality.DEGRADED_EXTERNAL_FILE_MISSING)
            if embedded_index:
                common_quality.add(DataQuality.EMBEDDED_ONLY)
                warnings.append(
                    self._warning(
                        DataQuality.EMBEDDED_ONLY,
                        "embedded evidence index is used because the external file is unavailable",
                        context,
                        "/evidence_index",
                    )
                )

        index, index_sources = self._merge_indexes(embedded_index, external_index)
        enrichment = self._build_enrichment_maps(result, index)
        candidates: list[_Candidate] = []

        candidates.extend(
            self._candidates_from_collection(
                result.get("evidence_items"),
                SourceFormat.EVIDENCE_ITEMS,
                "/evidence_items",
                context,
                enrichment,
                common_quality,
                warnings,
            )
        )

        catalog = index.get("evidence_catalog")
        catalog_format = index_sources.get("evidence_catalog", SourceFormat.EVIDENCE_CATALOG)
        candidates.extend(
            self._candidates_from_collection(
                catalog,
                catalog_format,
                "/evidence_index/evidence_catalog",
                context,
                enrichment,
                common_quality,
                warnings,
            )
        )

        for value, source_format, path in (
            (result.get("risk_evidence"), SourceFormat.LEGACY_RISK_EVIDENCE, "/risk_evidence"),
            (result.get("risk_frames"), SourceFormat.LEGACY_RISK_FRAME, "/risk_frames"),
            (result.get("risk_images"), SourceFormat.LEGACY_RISK_IMAGE, "/risk_images"),
        ):
            candidates.extend(
                self._candidates_from_collection(
                    value,
                    source_format,
                    path,
                    context,
                    enrichment,
                    common_quality,
                    warnings,
                    allow_scalar=True,
                )
            )

        rule_references: list[tuple[str, SourceFormat, str, dict[str, Any]]] = []
        for collection_name, source_format in (
            ("rule_matches", SourceFormat.RULE_MATCH),
            ("score_breakdown", SourceFormat.SCORE_BREAKDOWN),
        ):
            rows = result.get(collection_name)
            if rows is None:
                continue
            if not isinstance(rows, list):
                warnings.append(
                    self._warning(
                        DataQuality.UNSUPPORTED_FORMAT,
                        f"{collection_name} is not a list",
                        context,
                        f"/{collection_name}",
                    )
                )
                common_quality.add(DataQuality.UNSUPPORTED_FORMAT)
                continue
            for index_value, row in enumerate(rows):
                path = f"/{collection_name}/{index_value}"
                if not isinstance(row, dict):
                    warnings.append(
                        self._warning(
                            DataQuality.UNSUPPORTED_FORMAT,
                            f"{collection_name} item is not an object",
                            context,
                            path,
                        )
                    )
                    common_quality.add(DataQuality.UNSUPPORTED_FORMAT)
                    continue
                evidence_ids = row.get("evidence_ids")
                if isinstance(evidence_ids, list) and evidence_ids:
                    for local_id in evidence_ids:
                        if str(local_id or "").strip():
                            rule_references.append((str(local_id), source_format, path, row))
                elif self._has_inline_rule_evidence(row):
                    candidates.append(
                        self._candidate_from_item(
                            row,
                            source_format,
                            path,
                            context,
                            enrichment,
                            common_quality,
                            warnings,
                        )
                    )

        merged = self._merge_candidates(candidates)
        aliases = {alias for candidate in merged for alias in candidate.aliases}
        placeholders: list[_Candidate] = []
        for local_id, source_format, path, rule_row in rule_references:
            if local_id in aliases:
                continue
            quality = set(common_quality)
            quality.add(DataQuality.BROKEN_REFERENCE)
            placeholder = self._candidate_from_item(
                {
                    "evidence_id": local_id,
                    "primary_modality": "rule_reference",
                    "source": rule_row.get("source") or "rule_reference",
                    "summary": rule_row.get("rule_name") or rule_row.get("rule") or "unresolved rule evidence reference",
                },
                source_format,
                path,
                context,
                enrichment,
                quality,
                warnings,
            )
            placeholders.append(placeholder)
            warnings.append(
                self._warning(
                    DataQuality.BROKEN_REFERENCE,
                    f"rule reference points to unknown local evidence id: {local_id}",
                    context,
                    path,
                    make_evidence_id(context.audit_result_id, local_id),
                )
            )
            aliases.add(local_id)

        raw_candidate_count = len(candidates) + len(placeholders)
        if placeholders:
            merged = self._merge_candidates([*merged, *placeholders])

        views = tuple(self._to_view(candidate, context) for candidate in merged)
        return EvidenceAdaptationResult(
            evidence=views,
            source_anchors=tuple(
                (view.evidence_id, candidate.source_anchor)
                for view, candidate in zip(views, merged, strict=True)
            ),
            warnings=self._deduplicate_warnings(warnings),
            candidate_count=raw_candidate_count,
            duplicate_merged_count=max(0, raw_candidate_count - len(views)),
        )

    def _check_raw_item(
        self,
        context: EvidenceContext,
        warnings: list[DomainWarning],
        quality: set[DataQuality],
    ) -> None:
        raw_path = str(context.raw_item_path or "").strip()
        if raw_path and Path(raw_path).is_file():
            return
        quality.add(DataQuality.DEGRADED_RAW_ITEM_MISSING)
        warnings.append(
            self._warning(
                DataQuality.DEGRADED_RAW_ITEM_MISSING,
                f"raw item file is missing: {raw_path or '<unset>'}",
                context,
                "/raw_item_path",
            )
        )

    def _load_external_index(
        self,
        result: dict[str, Any],
        context: EvidenceContext,
        warnings: list[DomainWarning],
    ) -> tuple[dict[str, Any], bool]:
        raw_path = str(result.get("evidence_index_path") or "").strip()
        if not raw_path:
            relative = str(result.get("evidence_index_rel") or "").strip()
            raw_path = str(context.task_root / relative) if relative else ""
        if not raw_path:
            return {}, False

        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = context.task_root / path
        try:
            path = path.resolve()
            path.relative_to(context.task_root)
        except (OSError, ValueError):
            warnings.append(
                self._warning(
                    DataQuality.BROKEN_REFERENCE,
                    f"external evidence index escapes task output root: {raw_path}",
                    context,
                    "/evidence_index_path",
                )
            )
            return {}, False
        if not path.is_file():
            warnings.append(
                self._warning(
                    DataQuality.DEGRADED_EXTERNAL_FILE_MISSING,
                    f"external evidence index is missing: {path}",
                    context,
                    "/evidence_index_path",
                )
            )
            return {}, True
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(
                self._warning(
                    DataQuality.UNSUPPORTED_FORMAT,
                    f"external evidence index cannot be read: {exc}",
                    context,
                    "/evidence_index_path",
                )
            )
            return {}, False
        if not isinstance(payload, dict):
            warnings.append(
                self._warning(
                    DataQuality.UNSUPPORTED_FORMAT,
                    "external evidence index is not an object",
                    context,
                    "/evidence_index_path",
                )
            )
            return {}, False
        return payload, False

    def _merge_indexes(
        self,
        embedded: dict[str, Any],
        external: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, SourceFormat]]:
        merged = dict(external)
        merged.update(embedded)
        sources: dict[str, SourceFormat] = {}
        for key in self._INDEX_LIST_KEYS:
            embedded_value = embedded.get(key)
            external_value = external.get(key)
            if isinstance(embedded_value, list) and embedded_value:
                merged[key] = embedded_value
                sources[key] = SourceFormat.EVIDENCE_CATALOG
            elif isinstance(external_value, list) and external_value:
                merged[key] = external_value
                sources[key] = SourceFormat.EXTERNAL_EVIDENCE_INDEX
            elif isinstance(embedded_value, list):
                merged[key] = embedded_value
                sources[key] = SourceFormat.EVIDENCE_CATALOG
            elif isinstance(external_value, list):
                merged[key] = external_value
                sources[key] = SourceFormat.EXTERNAL_EVIDENCE_INDEX
        return merged, sources

    def _build_enrichment_maps(
        self,
        result: dict[str, Any],
        evidence_index: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        maps: dict[str, dict[str, Any]] = {
            "comment": {},
            "ocr": {},
            "asr": {},
            "frame": {},
            "catalog": {},
        }
        for item in evidence_index.get("evidence_catalog") or []:
            if not isinstance(item, dict):
                continue
            local_id = str(item.get("evidence_id") or item.get("id") or "")
            if local_id:
                maps["catalog"][local_id] = item
        for item in result.get("comments") or evidence_index.get("comments") or []:
            if not isinstance(item, dict):
                continue
            comment_id = str(item.get("comment_id") or item.get("id") or "")
            if comment_id:
                maps["comment"][comment_id] = item
                maps["comment"][f"comment:{comment_id}"] = item
        for item in evidence_index.get("ocr_items") or []:
            if isinstance(item, dict):
                self._index_by_sources(maps["ocr"], item)
        for item in evidence_index.get("asr_segments") or []:
            if isinstance(item, dict):
                self._index_by_sources(maps["asr"], item)
        for item in evidence_index.get("timeline_frames") or []:
            if isinstance(item, dict):
                self._index_by_sources(maps["frame"], item)
        return maps

    def _index_by_sources(self, target: dict[str, dict[str, Any]], item: dict[str, Any]) -> None:
        for key in ("source", "video_frame_source", "audio_source", "frame_id", "ocr_chunk_id", "asr_chunk_id"):
            value = str(item.get(key) or "").strip()
            if value:
                target[value] = item
                target[self._normalize_source(value)] = item

    def _candidates_from_collection(
        self,
        value: Any,
        source_format: SourceFormat,
        path: str,
        context: EvidenceContext,
        enrichment: dict[str, dict[str, Any]],
        common_quality: set[DataQuality],
        warnings: list[DomainWarning],
        *,
        allow_scalar: bool = False,
    ) -> list[_Candidate]:
        if value is None:
            return []
        if not isinstance(value, list):
            warnings.append(
                self._warning(
                    DataQuality.UNSUPPORTED_FORMAT,
                    f"{path} is not a list",
                    context,
                    path,
                )
            )
            common_quality.add(DataQuality.UNSUPPORTED_FORMAT)
            return []
        candidates: list[_Candidate] = []
        for index_value, raw_item in enumerate(value):
            item_path = f"{path}/{index_value}"
            if isinstance(raw_item, dict):
                item = raw_item
            elif allow_scalar and isinstance(raw_item, (str, int, float)):
                item = {"text": str(raw_item)}
            else:
                warnings.append(
                    self._warning(
                        DataQuality.UNSUPPORTED_FORMAT,
                        f"evidence item at {item_path} is not an object",
                        context,
                        item_path,
                    )
                )
                common_quality.add(DataQuality.UNSUPPORTED_FORMAT)
                continue
            if not self._has_evidence_content(item):
                warnings.append(
                    self._warning(
                        DataQuality.UNSUPPORTED_FORMAT,
                        f"evidence item at {item_path} has no usable identity or content",
                        context,
                        item_path,
                    )
                )
                common_quality.add(DataQuality.UNSUPPORTED_FORMAT)
                continue
            candidates.append(
                self._candidate_from_item(
                    item,
                    source_format,
                    item_path,
                    context,
                    enrichment,
                    common_quality,
                    warnings,
                )
            )
        return candidates

    def _candidate_from_item(
        self,
        raw_item: dict[str, Any],
        source_format: SourceFormat,
        source_json_path: str,
        context: EvidenceContext,
        enrichment: dict[str, dict[str, Any]],
        inherited_quality: Iterable[DataQuality],
        warnings: list[DomainWarning],
    ) -> _Candidate:
        item = self._enrich_item(raw_item, enrichment)
        evidence_type = self._classify_type(item, source_format)
        original_text = self._original_text(item, evidence_type)
        translated_text = self._translated_text(item, evidence_type)
        summary = self._first_text(
            item,
            "reason",
            "summary",
            "visual_summary",
            "analysis_result",
            "hit_explanation",
            "evidence",
        )
        timestamp_start = self._as_float(
            item.get("start") if item.get("start") is not None else item.get("timestamp")
        )
        timestamp_end = self._as_float(
            item.get("end") if item.get("end") is not None else item.get("timestamp")
        )
        raw_asset_path = self._first_text(item, "frame_asset_rel", "asset_rel", "asset_path", "path")
        asset_path, asset_broken = self._safe_asset_path(raw_asset_path, context)
        quality = set(inherited_quality)
        if asset_broken:
            quality.add(DataQuality.BROKEN_REFERENCE)
            warnings.append(
                self._warning(
                    DataQuality.BROKEN_REFERENCE,
                    f"evidence asset is missing or outside task output root: {raw_asset_path}",
                    context,
                    source_json_path,
                )
            )

        explicit_local_id = self._first_text(item, "evidence_id", "id")
        source_anchor = self._source_anchor(
            item,
            evidence_type,
            original_text,
            translated_text,
            asset_path,
            timestamp_start,
            timestamp_end,
        )
        dedup_keys = self._dedup_keys(
            item,
            evidence_type,
            asset_path,
            timestamp_start,
            timestamp_end,
        )
        content_key = ""
        if original_text or translated_text:
            content_key = f"content:{evidence_type.value}:{stable_hash([original_text, translated_text, timestamp_start, timestamp_end])}"
        local_id = explicit_local_id or generated_local_evidence_id(
            source_format.value,
            {
                "type": evidence_type.value,
                "source_anchor": source_anchor,
                "original_text": original_text,
                "translated_text": translated_text,
                "summary": summary,
            },
        )
        support_type = self._support_type(item, source_format)
        return _Candidate(
            local_id=local_id,
            aliases={local_id},
            evidence_type=evidence_type,
            original_text=original_text,
            translated_text=translated_text,
            summary=summary,
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end,
            asset_path=asset_path,
            source_json_path=source_json_path,
            source_json_paths=[source_json_path],
            support_type=support_type,
            source_format=source_format,
            source_formats=[source_format],
            source_anchor=source_anchor,
            dedup_keys=dedup_keys,
            content_key=content_key,
            priority=SOURCE_PRIORITY[source_format],
            data_quality=quality,
        )

    def _enrich_item(
        self,
        raw_item: dict[str, Any],
        enrichment: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        item = dict(raw_item)
        local_id = self._first_text(item, "evidence_id", "id")
        source = self._first_text(item, "source", "video_frame_source", "audio_source")
        comment_id = self._first_text(item, "comment_id")
        candidates = []
        if local_id:
            candidates.append(enrichment["catalog"].get(local_id))
        for key in (comment_id, source, self._normalize_source(source)):
            if not key:
                continue
            candidates.extend(
                (
                    enrichment["comment"].get(key),
                    enrichment["ocr"].get(key),
                    enrichment["asr"].get(key),
                    enrichment["frame"].get(key),
                )
            )
        enriched: dict[str, Any] = {}
        for candidate in candidates:
            if isinstance(candidate, dict):
                for key, value in candidate.items():
                    if value not in (None, "", [], {}):
                        enriched.setdefault(key, value)
        enriched.update(item)
        return enriched

    def _merge_candidates(self, candidates: list[_Candidate]) -> list[_Candidate]:
        merged: list[_Candidate] = []
        alias_to_index: dict[str, int] = {}
        dedup_to_index: dict[str, int] = {}
        content_to_index: dict[str, int] = {}
        for candidate in sorted(candidates, key=lambda item: item.priority):
            target_index = next(
                (alias_to_index[alias] for alias in sorted(candidate.aliases) if alias in alias_to_index),
                None,
            )
            if target_index is None:
                target_index = next(
                    (dedup_to_index[key] for key in sorted(candidate.dedup_keys) if key in dedup_to_index),
                    None,
                )
            if (
                target_index is None
                and candidate.content_key
                and candidate.source_format
                in (
                    SourceFormat.LEGACY_RISK_EVIDENCE,
                    SourceFormat.LEGACY_RISK_FRAME,
                    SourceFormat.LEGACY_RISK_IMAGE,
                    SourceFormat.RULE_MATCH,
                    SourceFormat.SCORE_BREAKDOWN,
                )
            ):
                target_index = content_to_index.get(candidate.content_key)
            if target_index is None:
                target_index = len(merged)
                merged.append(candidate)
            else:
                self._merge_into(merged[target_index], candidate)
            target = merged[target_index]
            for alias in target.aliases:
                alias_to_index[alias] = target_index
            for key in target.dedup_keys:
                dedup_to_index[key] = target_index
            if target.content_key:
                content_to_index.setdefault(target.content_key, target_index)
        return merged

    def _merge_into(self, target: _Candidate, incoming: _Candidate) -> None:
        target.aliases.update(incoming.aliases)
        target.dedup_keys.update(incoming.dedup_keys)
        target.data_quality.update(incoming.data_quality)
        for value in incoming.source_formats:
            if value not in target.source_formats:
                target.source_formats.append(value)
        for value in incoming.source_json_paths:
            if value not in target.source_json_paths:
                target.source_json_paths.append(value)
        for field_name in (
            "original_text",
            "translated_text",
            "summary",
            "asset_path",
            "source_anchor",
        ):
            if not getattr(target, field_name) and getattr(incoming, field_name):
                setattr(target, field_name, getattr(incoming, field_name))
        if target.timestamp_start is None:
            target.timestamp_start = incoming.timestamp_start
        if target.timestamp_end is None:
            target.timestamp_end = incoming.timestamp_end
        if target.evidence_type == EvidenceType.OTHER and incoming.evidence_type != EvidenceType.OTHER:
            target.evidence_type = incoming.evidence_type
        if target.support_type == SupportType.INDIRECT and incoming.support_type == SupportType.DIRECT:
            target.support_type = SupportType.DIRECT

    def _to_view(self, candidate: _Candidate, context: EvidenceContext) -> EvidenceView:
        evidence_id = make_evidence_id(context.audit_result_id, candidate.local_id)
        aliases = tuple(sorted(alias for alias in candidate.aliases if alias != candidate.local_id))
        source_formats = tuple(candidate.source_formats)
        source_json_paths = tuple(candidate.source_json_paths)
        quality = normalize_data_quality(candidate.data_quality)
        source_hash = stable_hash(
            {
                "audit_result_id": context.audit_result_id,
                "local_evidence_id": candidate.local_id,
                "aliases": aliases,
                "evidence_type": candidate.evidence_type.value,
                "original_text": candidate.original_text,
                "translated_text": candidate.translated_text,
                "summary": candidate.summary,
                "timestamp_start": candidate.timestamp_start,
                "timestamp_end": candidate.timestamp_end,
                "asset_path": candidate.asset_path,
                "source_formats": [item.value for item in source_formats],
                "source_json_paths": source_json_paths,
            }
        )
        return EvidenceView(
            evidence_id=evidence_id,
            local_evidence_id=candidate.local_id,
            local_evidence_aliases=aliases,
            audit_result_id=context.audit_result_id,
            task_id=context.task_id,
            content_id=context.content_id,
            evidence_type=candidate.evidence_type,
            original_text=candidate.original_text,
            translated_text=candidate.translated_text,
            summary=candidate.summary,
            timestamp_start=candidate.timestamp_start,
            timestamp_end=candidate.timestamp_end,
            asset_path=candidate.asset_path,
            source_json_path=candidate.source_json_path,
            source_json_paths=source_json_paths,
            support_type=candidate.support_type,
            source_format=candidate.source_format,
            source_formats=source_formats,
            source_hash=source_hash,
            data_quality=quality,
        )

    def _safe_asset_path(self, value: str, context: EvidenceContext) -> tuple[str, bool]:
        raw = str(value or "").strip()
        if not raw or raw.startswith(("http://", "https://")):
            return "", False
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = context.task_root / path
        try:
            resolved = path.resolve()
            relative = resolved.relative_to(context.task_root)
        except (OSError, ValueError):
            return "", True
        return (relative.as_posix(), False) if resolved.is_file() else (relative.as_posix(), True)

    def _classify_type(self, item: dict[str, Any], source_format: SourceFormat) -> EvidenceType:
        modality = self._first_text(item, "primary_modality", "modality", "kind").lower()
        source = self._first_text(item, "source").lower()
        if source_format in (SourceFormat.RULE_MATCH, SourceFormat.SCORE_BREAKDOWN) or modality == "rule_reference":
            return EvidenceType.RULE_REFERENCE
        if source_format == SourceFormat.LEGACY_RISK_FRAME:
            return EvidenceType.KEYFRAME
        if source_format == SourceFormat.LEGACY_RISK_IMAGE:
            return EvidenceType.VISUAL
        if "comment" in modality or source.startswith("comment:") or item.get("comment_id"):
            return EvidenceType.COMMENT
        if modality in ("asr", "audio", "video_audio") or source.startswith("video_audio"):
            return EvidenceType.ASR
        if modality in ("frame", "frame_ref", "keyframe"):
            return EvidenceType.KEYFRAME
        if modality == "ocr" or item.get("ocr_chunk_id"):
            return EvidenceType.OCR
        if modality in ("vision", "visual", "image") or source.startswith("image:"):
            return EvidenceType.VISUAL
        if modality in ("profile", "author", "account") or source.startswith(("profile:", "author:")):
            return EvidenceType.PROFILE
        if modality in ("text", "keyword", "title", "desc") or source in ("title", "desc", "text"):
            return EvidenceType.TEXT
        return EvidenceType.OTHER

    def _original_text(self, item: dict[str, Any], evidence_type: EvidenceType) -> str:
        if evidence_type == EvidenceType.OCR:
            return self._first_text(item, "ocr_text", "text", "evidence", "content")
        if evidence_type == EvidenceType.ASR:
            return self._first_text(item, "source_text_dolphin", "text", "source_text_mms", "evidence")
        if evidence_type == EvidenceType.COMMENT:
            return self._first_text(item, "text", "content", "evidence_quote", "evidence")
        if evidence_type in (EvidenceType.KEYFRAME, EvidenceType.VISUAL):
            return self._first_text(item, "ocr_text", "evidence", "text", "content")
        return self._first_text(item, "text", "evidence", "content", "evidence_quote")

    def _translated_text(self, item: dict[str, Any], evidence_type: EvidenceType) -> str:
        if evidence_type == EvidenceType.OCR:
            return self._first_text(item, "ocr_text_zh", "translation_zh", "text_zh")
        return self._first_text(item, "translation_zh", "text_zh", "ocr_text_zh")

    def _source_anchor(
        self,
        item: dict[str, Any],
        evidence_type: EvidenceType,
        original_text: str,
        translated_text: str,
        asset_path: str,
        timestamp_start: float | None,
        timestamp_end: float | None,
    ) -> str:
        comment_id = self._first_text(item, "comment_id")
        if evidence_type == EvidenceType.COMMENT and comment_id:
            return f"comment:{comment_id}"
        source = self._normalize_source(self._first_text(item, "source", "video_frame_source", "audio_source"))
        if evidence_type == EvidenceType.TEXT and source in ("title", "desc", "text"):
            return f"text:{source}"
        if asset_path and evidence_type in (EvidenceType.OCR, EvidenceType.KEYFRAME, EvidenceType.VISUAL):
            time_key = self._time_key(timestamp_start, timestamp_end)
            return f"media:asset:{asset_path}:{time_key}"
        if source:
            return f"{evidence_type.value}:source:{source}"
        if asset_path:
            time_key = self._time_key(timestamp_start, timestamp_end)
            return f"{evidence_type.value}:asset:{asset_path}:{time_key}"
        if original_text or translated_text:
            return f"{evidence_type.value}:content:{stable_hash([original_text, translated_text, timestamp_start, timestamp_end])}"
        return ""

    def _dedup_keys(
        self,
        item: dict[str, Any],
        evidence_type: EvidenceType,
        asset_path: str,
        timestamp_start: float | None,
        timestamp_end: float | None,
    ) -> set[str]:
        keys: set[str] = set()
        comment_id = self._first_text(item, "comment_id")
        if comment_id:
            keys.add(f"comment:{comment_id}")
        if asset_path:
            keys.add(f"asset:{asset_path}")
        source = self._normalize_source(
            self._first_text(item, "source", "video_frame_source", "audio_source")
        )
        generic_sources = {"", "text", "comment", "ocr", "asr", "audio", "vision", "image", "video_audio"}
        if source not in generic_sources:
            keys.add(f"source:{source}")
        if evidence_type == EvidenceType.TEXT and source in ("title", "desc"):
            keys.add(f"text:{source}")
        if not keys and timestamp_start is not None:
            keys.add(f"time:{evidence_type.value}:{self._time_key(timestamp_start, timestamp_end)}")
        return keys

    def _support_type(self, item: dict[str, Any], source_format: SourceFormat) -> SupportType:
        if item.get("exemption_basis") or item.get("counter_evidence"):
            return SupportType.COUNTER_EVIDENCE
        if source_format in (
            SourceFormat.EVIDENCE_CATALOG,
            SourceFormat.EXTERNAL_EVIDENCE_INDEX,
            SourceFormat.RULE_MATCH,
            SourceFormat.SCORE_BREAKDOWN,
        ):
            return SupportType.INDIRECT
        return SupportType.DIRECT

    def _has_evidence_content(self, item: dict[str, Any]) -> bool:
        return any(
            item.get(key) not in (None, "", [], {})
            for key in (
                "evidence_id",
                "id",
                "source",
                "text",
                "content",
                "evidence",
                "ocr_text",
                "source_text_dolphin",
                "source_text_mms",
                "translation_zh",
                "asset_rel",
                "frame_asset_rel",
                "reason",
                "summary",
            )
        )

    def _has_inline_rule_evidence(self, item: dict[str, Any]) -> bool:
        return any(item.get(key) not in (None, "", [], {}) for key in ("evidence", "summary", "context", "source"))

    def _normalize_source(self, value: str) -> str:
        raw = str(value or "").strip()
        for prefix in ("video_frame:",):
            if raw.startswith(prefix):
                number = self._as_float(raw[len(prefix) :])
                if number is not None:
                    return f"{prefix}{number:.2f}"
        return raw

    def _time_key(self, start: float | None, end: float | None) -> str:
        start_value = "" if start is None else f"{start:.3f}"
        end_value = "" if end is None else f"{end:.3f}"
        return f"{start_value}-{end_value}"

    def _as_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _first_text(self, item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    def _warning(
        self,
        code: DataQuality,
        message: str,
        context: EvidenceContext,
        source_json_path: str,
        evidence_id: str = "",
    ) -> DomainWarning:
        return DomainWarning(
            code=code,
            message=message,
            task_id=context.task_id,
            audit_result_id=context.audit_result_id,
            evidence_id=evidence_id,
            source_json_path=source_json_path,
        )

    def _deduplicate_warnings(self, warnings: Iterable[DomainWarning]) -> tuple[DomainWarning, ...]:
        output = []
        seen = set()
        for warning in warnings:
            key = (
                warning.code,
                warning.message,
                warning.audit_result_id,
                warning.evidence_id,
                warning.source_json_path,
            )
            if key not in seen:
                seen.add(key)
                output.append(warning)
        return tuple(output)
