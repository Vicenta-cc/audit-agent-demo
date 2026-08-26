import tempfile
import unittest
from pathlib import Path

from backend.domain.contracts import EvidenceType, SourceFormat
from backend.domain.evidence_adapter import EvidenceAdapter, EvidenceContext
from backend.domain.warnings import DataQuality


class EvidenceAdapterTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.outputs_dir = self.root / "outputs"
        self.task_id = "task-1"
        self.task_root = self.outputs_dir / self.task_id
        self.task_root.mkdir(parents=True)
        self.raw_path = self.task_root / "raw_items" / "item.json"
        self.raw_path.parent.mkdir(parents=True)
        self.raw_path.write_text("{}", encoding="utf-8")
        self.adapter = EvidenceAdapter()

    def tearDown(self):
        self.temp_dir.cleanup()

    def context(self, audit_result_id: int = 1, *, raw_path: Path | None = None) -> EvidenceContext:
        return EvidenceContext(
            audit_result_id=audit_result_id,
            task_id=self.task_id,
            content_id=10,
            outputs_dir=self.outputs_dir,
            raw_item_path=str(raw_path if raw_path is not None else self.raw_path),
        )

    def asset(self, relative_path: str) -> str:
        path = self.task_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"asset")
        return relative_path

    def test_modern_evidence_item_wins_and_catalog_fills_missing_fields(self):
        asset_path = self.asset("assets/frame.jpg")
        result = {
            "evidence_items": [{
                "evidence_id": "ocr:1",
                "primary_modality": "ocr",
                "source": "video:1/frame:f0001",
                "ocr_text": "原始画面文字",
                "start": "1.25",
                "end": "2.5",
            }],
            "evidence_index": {"evidence_catalog": [{
                "evidence_id": "ocr:1",
                "primary_modality": "ocr",
                "source": "video:1/frame:f0001",
                "ocr_text_zh": "画面译文",
                "asset_rel": asset_path,
            }]},
        }

        adapted = self.adapter.adapt(result, self.context())

        self.assertEqual(len(adapted.evidence), 1)
        evidence = adapted.evidence[0]
        self.assertEqual(evidence.evidence_type, EvidenceType.OCR)
        self.assertEqual(evidence.original_text, "原始画面文字")
        self.assertEqual(evidence.translated_text, "画面译文")
        self.assertEqual(evidence.timestamp_start, 1.25)
        self.assertEqual(evidence.timestamp_end, 2.5)
        self.assertEqual(evidence.asset_path, asset_path)
        self.assertEqual(
            evidence.source_formats,
            (SourceFormat.EVIDENCE_ITEMS, SourceFormat.EVIDENCE_CATALOG),
        )
        self.assertEqual(adapted.duplicate_merged_count, 1)

    def test_legacy_frame_and_score_breakdown_same_source_are_merged(self):
        asset_path = self.asset("assets/frame-7.jpg")
        result = {
            "risk_evidence": [{
                "kind": "frame_ref",
                "source": "video_frame:7.056",
                "text": "联系方式",
                "reason": "画面命中",
                "start": "7.05",
                "end": "7.06",
            }],
            "score_breakdown": [{
                "rule_id": "ocr_redirect",
                "source": "video_frame:7.056",
                "evidence": "OCR识别出联系方式",
            }],
            "evidence_index": {"timeline_frames": [{
                "source": "video:1/frame:f0007",
                "video_frame_source": "video_frame:7.056",
                "timestamp": 7.056,
                "asset_rel": asset_path,
            }]},
        }

        adapted = self.adapter.adapt(result, self.context())

        self.assertEqual(len(adapted.evidence), 1)
        evidence = adapted.evidence[0]
        self.assertEqual(evidence.evidence_type, EvidenceType.KEYFRAME)
        self.assertEqual(evidence.asset_path, asset_path)
        self.assertIn(SourceFormat.LEGACY_RISK_EVIDENCE, evidence.source_formats)
        self.assertIn(SourceFormat.SCORE_BREAKDOWN, evidence.source_formats)

    def test_duplicate_local_id_is_merged(self):
        result = {"evidence_items": [
            {"evidence_id": "text:title", "primary_modality": "text", "source": "title", "text": "标题"},
            {"evidence_id": "text:title", "primary_modality": "text", "source": "title", "translation_zh": "标题译文"},
        ]}

        adapted = self.adapter.adapt(result, self.context())

        self.assertEqual(len(adapted.evidence), 1)
        self.assertEqual(adapted.evidence[0].translated_text, "标题译文")
        self.assertEqual(adapted.duplicate_merged_count, 1)

    def test_standalone_evidence_catalog_item_is_exposed_as_indirect_evidence(self):
        result = {"evidence_index": {"evidence_catalog": [{
            "evidence_id": "text:desc",
            "primary_modality": "text",
            "source": "desc",
            "text": "候选正文证据",
        }]}}

        adapted = self.adapter.adapt(result, self.context())

        self.assertEqual(len(adapted.evidence), 1)
        self.assertEqual(adapted.evidence[0].source_format, SourceFormat.EVIDENCE_CATALOG)
        self.assertEqual(adapted.evidence[0].original_text, "候选正文证据")

    def test_unsupported_evidence_item_emits_warning(self):
        adapted = self.adapter.adapt(
            {"evidence_items": [{"unrecognized": True}]},
            self.context(),
        )

        self.assertEqual(adapted.evidence, ())
        self.assertIn(DataQuality.UNSUPPORTED_FORMAT, {warning.code for warning in adapted.warnings})

    def test_same_local_id_in_different_results_has_different_global_id(self):
        result = {"evidence_items": [{
            "evidence_id": "text:title",
            "primary_modality": "text",
            "source": "title",
            "text": "标题",
        }]}

        first = self.adapter.adapt(result, self.context(1)).evidence[0]
        second = self.adapter.adapt(result, self.context(2)).evidence[0]

        self.assertNotEqual(first.evidence_id, second.evidence_id)
        self.assertEqual(first.local_evidence_id, second.local_evidence_id)

    def test_external_index_missing_uses_embedded_index_with_explicit_quality(self):
        result = {
            "evidence_index_path": str(self.task_root / "assets" / "missing-index.json"),
            "evidence_index": {"evidence_catalog": [{
                "evidence_id": "text:title",
                "source": "title",
                "primary_modality": "text",
                "text": "内嵌正文",
            }]},
        }

        adapted = self.adapter.adapt(result, self.context())

        self.assertEqual(len(adapted.evidence), 1)
        quality = adapted.evidence[0].data_quality
        self.assertIn(DataQuality.DEGRADED_EXTERNAL_FILE_MISSING, quality)
        self.assertIn(DataQuality.EMBEDDED_ONLY, quality)
        self.assertEqual(
            {warning.code for warning in adapted.warnings},
            {DataQuality.DEGRADED_EXTERNAL_FILE_MISSING, DataQuality.EMBEDDED_ONLY},
        )

    def test_raw_item_missing_is_not_silent(self):
        missing_raw = self.task_root / "raw_items" / "missing.json"
        result = {"evidence_items": [{
            "evidence_id": "text:desc",
            "primary_modality": "text",
            "source": "desc",
            "text": "正文",
        }]}

        adapted = self.adapter.adapt(result, self.context(raw_path=missing_raw))

        self.assertIn(DataQuality.DEGRADED_RAW_ITEM_MISSING, adapted.evidence[0].data_quality)
        self.assertIn(DataQuality.DEGRADED_RAW_ITEM_MISSING, {item.code for item in adapted.warnings})

    def test_pass_result_without_evidence_stays_empty(self):
        adapted = self.adapter.adapt(
            {"decision": "pass", "risk_level": "none"},
            self.context(),
        )

        self.assertEqual(adapted.evidence, ())
        self.assertEqual(adapted.warnings, ())

    def test_ocr_and_asr_time_ranges_are_preserved(self):
        result = {"evidence_items": [
            {
                "evidence_id": "ocr:window",
                "primary_modality": "ocr",
                "source": "video:1/frame:f1",
                "ocr_text": "二维码",
                "start": 3.2,
                "end": 4.4,
            },
            {
                "evidence_id": "asr:window",
                "primary_modality": "asr",
                "source": "video_audio:8.0-12.5",
                "source_text_dolphin": "加我联系",
                "translation_zh": "加我联系",
                "start": "8.0",
                "end": "12.5",
            },
        ]}

        adapted = self.adapter.adapt(result, self.context())
        by_type = {evidence.evidence_type: evidence for evidence in adapted.evidence}

        self.assertEqual((by_type[EvidenceType.OCR].timestamp_start, by_type[EvidenceType.OCR].timestamp_end), (3.2, 4.4))
        self.assertEqual((by_type[EvidenceType.ASR].timestamp_start, by_type[EvidenceType.ASR].timestamp_end), (8.0, 12.5))

    def test_comment_original_and_translation_are_enriched_from_comments(self):
        result = {
            "evidence_items": [{
                "evidence_id": "comment:88",
                "primary_modality": "comment",
                "source": "comment:88",
                "comment_id": "88",
                "reason": "评论命中",
            }],
            "comments": [{
                "comment_id": "88",
                "content": "ئەسسالامۇ ئەلەيكۇم",
                "translation_zh": "你好",
            }],
        }

        evidence = self.adapter.adapt(result, self.context()).evidence[0]

        self.assertEqual(evidence.evidence_type, EvidenceType.COMMENT)
        self.assertEqual(evidence.original_text, "ئەسسالامۇ ئەلەيكۇم")
        self.assertEqual(evidence.translated_text, "你好")

    def test_keyframe_asset_path_is_normalized(self):
        asset_path = self.asset("assets/keyframes/frame.jpg")
        result = {"risk_frames": [{
            "timestamp": 12.3,
            "frame_asset_rel": asset_path,
            "evidence": "画面出现联系方式",
        }]}

        evidence = self.adapter.adapt(result, self.context()).evidence[0]

        self.assertEqual(evidence.evidence_type, EvidenceType.KEYFRAME)
        self.assertEqual(evidence.asset_path, asset_path)

    def test_illegal_asset_path_is_marked_broken(self):
        result = {"evidence_items": [{
            "evidence_id": "ocr:illegal",
            "primary_modality": "ocr",
            "source": "image:0",
            "ocr_text": "文字",
            "asset_rel": "../outside.jpg",
        }]}

        adapted = self.adapter.adapt(result, self.context())

        self.assertEqual(adapted.evidence[0].asset_path, "")
        self.assertIn(DataQuality.BROKEN_REFERENCE, adapted.evidence[0].data_quality)
        self.assertIn(DataQuality.BROKEN_REFERENCE, {warning.code for warning in adapted.warnings})

    def test_broken_rule_reference_is_preserved_as_degraded_placeholder(self):
        result = {"rule_matches": [{
            "rule_id": "rule-1",
            "rule_name": "规则一",
            "evidence_ids": ["missing-local-id"],
        }]}

        adapted = self.adapter.adapt(result, self.context())

        self.assertEqual(len(adapted.evidence), 1)
        evidence = adapted.evidence[0]
        self.assertEqual(evidence.evidence_type, EvidenceType.RULE_REFERENCE)
        self.assertIn(DataQuality.BROKEN_REFERENCE, evidence.data_quality)
        self.assertIn(DataQuality.BROKEN_REFERENCE, {warning.code for warning in adapted.warnings})


if __name__ == "__main__":
    unittest.main()
