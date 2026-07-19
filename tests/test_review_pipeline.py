from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.audit_agent.config import settings
from backend.audit_agent.evidence_groups import build_evidence_groups
from backend.audit_agent.models import AuditSubject
from backend.audit_agent.ocr_processor import VLMOCRTranslateProcessor
from backend.audit_agent.pipeline import AuditPipeline, FusionAuditTimeoutError
from backend.audit_agent.qwen_client import ChatCompletionText, QwenClient
from backend.audit_agent.video_processor import DemoAudioProcessor


def bare_pipeline() -> AuditPipeline:
    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.job_id = "test-job"
    pipeline.rule_snapshot = {}
    return pipeline


class ReviewChunkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = bare_pipeline()

    @staticmethod
    def frames(count: int) -> list[dict]:
        return [
            {
                "frame_id": f"f{index + 1:04d}",
                "frame_number": index * 30,
                "timestamp": float(index),
                "ocr_text": f"text {index // 2}",
                "ocr_text_zh": f"译文 {index // 2}",
            }
            for index in range(count)
        ]

    def test_ocr_chunks_are_balanced_between_three_and_five_frames(self) -> None:
        for count, expected_sizes in ((16, [4, 4, 4, 4]), (11, [4, 4, 3]), (6, [3, 3])):
            chunks = self.pipeline._build_ocr_context_chunks(self.frames(count), "video:1/segment:1")
            self.assertEqual([len(chunk["frame_ids"]) for chunk in chunks], expected_sizes)
            flattened = [frame_id for chunk in chunks for frame_id in chunk["frame_ids"]]
            self.assertEqual(flattened, [f"f{index + 1:04d}" for index in range(count)])

    def test_asr_chunks_keep_raw_ids_and_never_exceed_four(self) -> None:
        transcript = {
            "segments": [
                {
                    "start": index * 2.0,
                    "end": index * 2.0 + 1.5,
                    "text": "dolphin " * 12,
                    "translation_zh": "中文" * 20,
                }
                for index in range(12)
            ],
            "translation": {"asr_consistency": "整体一致"},
        }
        chunks = self.pipeline._build_asr_context_chunks(
            transcript,
            0.0,
            24.0,
            "video:1/segment:1",
        )
        self.assertEqual(len(chunks), 4)
        self.assertTrue(all(chunk["source_segment_ids"] for chunk in chunks))
        self.assertEqual(chunks[0]["asr_chunk_id"], "video:1/segment:1/asr:1")
        self.assertEqual(chunks[-1]["end"], 23.5)

    def test_segment_review_filters_invalid_frame_and_chunk_ids(self) -> None:
        frames = self.frames(4)
        sheet = {
            "ocr_chunks": self.pipeline._build_ocr_context_chunks(frames, "video:1/segment:1"),
            "asr_chunks": [{"asr_chunk_id": "video:1/segment:1/asr:1"}],
        }
        analysis = {
            "segment_score": 150,
            "visual_risks": [
                {"frame_ids": ["f0001", "bad"], "score": 61, "risk_type": "视觉", "reason": "命中"},
                {"frame_ids": ["bad"], "score": 99},
            ],
            "ocr_risks": [
                {
                    "ocr_chunk_id": sheet["ocr_chunks"][0]["ocr_chunk_id"],
                    "frame_ids": ["f0001", "bad"],
                    "score": -5,
                },
                {"ocr_chunk_id": "bad", "frame_ids": ["f0001"], "score": 80},
            ],
            "asr_risks": [
                {"asr_chunk_id": "video:1/segment:1/asr:1", "score": 80},
                {"asr_chunk_id": "bad", "score": 80},
            ],
        }
        result = self.pipeline._normalize_segment_review(analysis, sheet, frames)
        self.assertEqual(result["segment_score"], 100)
        self.assertEqual(result["visual_risks"][0]["frame_ids"], ["f0001"])
        self.assertEqual(len(result["visual_risks"]), 1)
        self.assertEqual(len(result["ocr_risks"]), 1)
        self.assertEqual(result["ocr_risks"][0]["score"], 0)
        self.assertEqual(len(result["asr_risks"]), 1)


class VideoReviewFlowTests(unittest.TestCase):
    def test_video_is_extracted_once_and_split_at_sixteenth_selected_frame(self) -> None:
        pipeline = bare_pipeline()

        class FakeAudio:
            last_extract_error = ""

            def extract_audio(self, video_path, output_dir):
                output_dir.mkdir(parents=True, exist_ok=True)
                target = output_dir / "audio.wav"
                target.write_bytes(b"audio")
                return target

            def transcribe(self, audio_path):
                return {
                    "text": "full transcript",
                    "segments": [
                        {"start": index * 5.0, "end": index * 5.0 + 4.0, "text": f"segment {index}"}
                        for index in range(20)
                    ],
                }

        class FakeFrames:
            def __init__(self):
                self.calls = 0
                self.max_frames = None

            def extract_timeline_frames(self, video_path, output_dir, max_frames):
                self.calls += 1
                self.max_frames = max_frames
                output_dir.mkdir(parents=True, exist_ok=True)
                output = []
                for index in range(32):
                    target = output_dir / f"f{index + 1:04d}.jpg"
                    target.write_bytes(b"frame")
                    output.append({
                        "frame_id": f"f{index + 1:04d}",
                        "frame_number": index * 30,
                        "timestamp": float(index),
                        "path": str(target),
                    })
                return output

            def video_meta(self, video_path):
                return 30.0, 3000, 100.0

            def create_contact_sheet(self, frames, output_path, columns, rows):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"sheet")

            def extract_precise_frames(self, *args, **kwargs):
                raise AssertionError("precise review must not be called")

        class FakeQwen:
            enabled = True

            def __init__(self):
                self.calls = 0
                self.active = 0
                self.max_active = 0
                self.lock = threading.Lock()

            def analyze_image(self, image_path, prompt, max_tokens=None, model=None):
                with self.lock:
                    self.calls += 1
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                time.sleep(0.03)
                with self.lock:
                    self.active -= 1
                return {
                    "segment_summary": "正常画面",
                    "segment_score": 0,
                    "visual_risks": [],
                    "ocr_risks": [],
                    "asr_risks": [],
                }

        pipeline.audio = FakeAudio()
        pipeline.frames = FakeFrames()
        pipeline.qwen = FakeQwen()
        pipeline.prompt_set = type("PromptSet", (), {"frame_prompt": "测试审核边界"})()
        pipeline._scan_timeline_ocr_batch = lambda *args: {}
        pipeline._translate_transcript_if_needed = lambda transcript, *args, **kwargs: transcript

        with tempfile.TemporaryDirectory() as tmp, patch("backend.audit_agent.pipeline.job_store.log"):
            root = Path(tmp)
            video_path = root / "video.mp4"
            video_path.write_bytes(b"video")
            with (
                patch.object(settings, "video_review_max_frames", 32),
                patch.object(settings, "video_review_sheet_frames", 16),
                patch.object(settings, "video_review_concurrency", 2),
                patch.object(settings, "video_review_asr_overlap_seconds", 4.0),
            ):
                result = pipeline._analyze_video_file(0, video_path, root / "out", "", "local")

        self.assertEqual(pipeline.frames.calls, 1)
        self.assertEqual(pipeline.frames.max_frames, 32)
        self.assertEqual(pipeline.qwen.calls, 2)
        self.assertEqual(pipeline.qwen.max_active, 2)
        self.assertEqual(len(result["review_sheets"]), 2)
        self.assertEqual(result["review_sheets"][0]["end"], 15.0)
        self.assertEqual(result["review_sheets"][1]["start"], 15.0)
        self.assertEqual(result["review_sheets"][0]["asr_chunks"][-1]["end"], 19.0)
        self.assertEqual(result["moment_sheets"], [])
        self.assertEqual(result["precise_sheets"], [])

    def test_contact_sheet_reviews_run_once_per_library_and_merge_results(self) -> None:
        pipeline = bare_pipeline()

        class FakeAudio:
            last_extract_error = ""

            def extract_audio(self, video_path, output_dir):
                output_dir.mkdir(parents=True, exist_ok=True)
                target = output_dir / "audio.wav"
                target.write_bytes(b"audio")
                return target

            def transcribe(self, audio_path):
                return {"text": "", "segments": []}

        class FakeFrames:
            def extract_timeline_frames(self, video_path, output_dir, max_frames):
                output_dir.mkdir(parents=True, exist_ok=True)
                output = []
                for index in range(16):
                    target = output_dir / f"f{index + 1:04d}.jpg"
                    target.write_bytes(b"frame")
                    output.append({
                        "frame_id": f"f{index + 1:04d}",
                        "frame_number": index * 30,
                        "timestamp": float(index),
                        "path": str(target),
                    })
                return output

            def video_meta(self, video_path):
                return 15.0, 450, 30.0

            def create_contact_sheet(self, frames, output_path, columns, rows):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"sheet")

        class FakeQwen:
            enabled = True

            def __init__(self):
                self.library_ids = []

            def analyze_image(self, image_path, prompt, max_tokens=None, model=None):
                payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
                library_id = payload["risk_library"]["id"]
                self.library_ids.append(library_id)
                return {
                    "segment_summary": f"{library_id}摘要",
                    "segment_score": 70 if library_id == "hate" else 0,
                    "visual_risks": [
                        {"frame_ids": ["f0001"], "score": 70, "risk_type": "歧视贬损", "reason": "命中"}
                    ] if library_id == "hate" else [],
                    "ocr_risks": [],
                    "asr_risks": [],
                }

        pipeline.audio = FakeAudio()
        pipeline.frames = FakeFrames()
        pipeline.qwen = FakeQwen()
        pipeline.prompt_profile_snapshot = {
            "libraries": [
                {"id": "hate", "title": "民族意识形态风险", "audit_goal": "识别仇恨", "output_labels": ["歧视贬损"]},
                {"id": "fraud", "title": "诈骗风险", "audit_goal": "识别诈骗", "output_labels": ["诈骗"]},
            ]
        }
        pipeline.rule_snapshot = {
            "thresholds": {"review": 40, "medium": 60, "high": 80},
            "scoring_rules": [],
        }
        pipeline._scan_timeline_ocr_batch = lambda *args: {}
        pipeline._translate_transcript_if_needed = lambda transcript, *args, **kwargs: transcript

        with tempfile.TemporaryDirectory() as tmp, patch("backend.audit_agent.pipeline.job_store.log"):
            root = Path(tmp)
            video_path = root / "video.mp4"
            video_path.write_bytes(b"video")
            with (
                patch.object(settings, "video_review_max_frames", 16),
                patch.object(settings, "video_review_sheet_frames", 16),
                patch.object(settings, "video_review_concurrency", 4),
            ):
                result = pipeline._analyze_video_file(0, video_path, root / "out", "", "local")

        self.assertEqual(pipeline.qwen.library_ids, ["hate", "fraud"])
        self.assertEqual(len(result["review_sheets"]), 1)
        self.assertEqual(len(result["segment_reviews"]), 1)
        analysis = result["segment_reviews"][0]["analysis"]
        self.assertEqual(analysis["segment_score"], 70)
        self.assertEqual(analysis["visual_risks"][0]["risk_library_id"], "hate")
        self.assertEqual(len(result["segment_reviews"][0]["library_reviews"]), 2)


class OcrThinkingTests(unittest.TestCase):
    def test_qwen_keeps_finish_reason_and_token_usage(self) -> None:
        client = QwenClient.__new__(QwenClient)
        text = ChatCompletionText(
            '{"comments":[]}',
            {
                "finish_reason": "stop",
                "prompt_tokens": 120,
                "completion_tokens": 8,
                "total_tokens": 128,
            },
        )

        result = client._parse_chat_json(text)

        self.assertEqual(result["comments"], [])
        self.assertEqual(result["_llm_meta"]["prompt_tokens"], 120)
        self.assertEqual(result["_llm_meta"]["completion_tokens"], 8)

    def test_qwen_remote_text_forwards_model_tokens_and_thinking(self) -> None:
        class RecordingRemote:
            enabled = True
            kwargs = {}

            def audit_text(self, prompt, **kwargs):
                self.kwargs = kwargs
                return {"segments": []}

        client = QwenClient.__new__(QwenClient)
        client.remote = RecordingRemote()
        with patch.object(settings, "use_remote_llm", True):
            client.audit_text(
                "translate",
                max_tokens=6000,
                model="qwen3.7-plus",
                enable_thinking=True,
                request_timeout=90,
            )

        self.assertEqual(client.remote.kwargs["max_tokens"], 6000)
        self.assertEqual(client.remote.kwargs["model"], "qwen3.7-plus")
        self.assertTrue(client.remote.kwargs["enable_thinking"])
        self.assertEqual(client.remote.kwargs["request_timeout"], 90)

    def test_qwen_direct_text_forwards_request_timeout_to_http_call(self) -> None:
        client = QwenClient.__new__(QwenClient)
        client.api_key = "test-key"
        recorded = {}

        def post_chat(payload, *, request_timeout=None):
            recorded["payload"] = payload
            recorded["request_timeout"] = request_timeout
            return "{}"

        client._post_chat = post_chat
        with patch.object(settings, "use_remote_llm", False):
            client.audit_text(
                "fusion",
                max_tokens=3000,
                enable_thinking=False,
                request_timeout=90,
            )

        self.assertEqual(recorded["request_timeout"], 90)
        self.assertEqual(recorded["payload"]["max_tokens"], 3000)
        self.assertFalse(recorded["payload"]["enable_thinking"])

    def test_qwen_image_payload_disables_thinking_when_requested(self) -> None:
        client = QwenClient.__new__(QwenClient)
        client.api_key = "test-key"
        client._to_image_url = lambda *args, **kwargs: "data:image/jpeg;base64,dGVzdA=="
        payloads = []
        client._post_chat = lambda payload: payloads.append(payload) or "{}"

        with patch.object(settings, "use_remote_vlm", False):
            client.analyze_image("unused.png", "OCR", enable_thinking=False)

        self.assertIs(payloads[0]["enable_thinking"], False)

    def test_qwen_remote_image_forwards_max_tokens(self) -> None:
        class RecordingRemote:
            enabled = True
            kwargs = {}

            def analyze_image(self, image_bytes, prompt, **kwargs):
                self.kwargs = kwargs
                return {"summary": "ok"}

        client = QwenClient.__new__(QwenClient)
        client.remote = RecordingRemote()
        client._image_bytes = lambda *args, **kwargs: b"image"
        client._image_mime_type = lambda *args, **kwargs: "image/jpeg"
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(settings, "use_remote_vlm", True),
        ):
            image_path = Path(tmp) / "image.jpg"
            image_path.write_bytes(b"image")
            client.analyze_image(image_path, "audit", max_tokens=3072)

        self.assertEqual(client.remote.kwargs["max_tokens"], 3072)

    def test_vlm_ocr_disables_thinking(self) -> None:
        class RecordingQwen:
            kwargs = {}

            def analyze_image(self, image_path, prompt, **kwargs):
                self.kwargs = kwargs
                return {
                    "text": "source",
                    "text_zh": "译文",
                    "language": "ug",
                    "confidence": "high",
                }

        processor = VLMOCRTranslateProcessor()
        processor._qwen = RecordingQwen()
        result = processor.ocr_image(Path("unused.png"))

        self.assertIs(processor._qwen.kwargs["enable_thinking"], False)
        self.assertEqual(processor._qwen.kwargs["max_tokens"], 1500)
        self.assertIs(result["thinking_enabled"], False)


class FusionAuditCallTests(unittest.TestCase):
    def test_timeout_retries_once_then_logs_token_usage(self) -> None:
        class RetryingQwen:
            def __init__(self):
                self.calls = []

            def audit_text(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                if len(self.calls) == 1:
                    raise TimeoutError("read timed out")
                return {
                    "summary": "ok",
                    "_llm_meta": {
                        "finish_reason": "stop",
                        "prompt_tokens": 1200,
                        "completion_tokens": 640,
                        "total_tokens": 1840,
                    },
                }

        pipeline = bare_pipeline()
        pipeline.qwen = RetryingQwen()
        with (
            patch.object(settings, "fusion_max_tokens", 3000),
            patch.object(settings, "fusion_request_timeout", 90),
            patch.object(settings, "fusion_timeout_retries", 1),
            patch("backend.audit_agent.pipeline.job_store.log") as log,
        ):
            result = pipeline._run_fusion_audit("note-1", "fusion prompt")

        self.assertEqual(result["summary"], "ok")
        self.assertEqual(len(pipeline.qwen.calls), 2)
        kwargs = pipeline.qwen.calls[-1][1]
        self.assertEqual(kwargs["max_tokens"], 3000)
        self.assertFalse(kwargs["enable_thinking"])
        self.assertEqual(kwargs["request_timeout"], 90)
        messages = [call.args[1] for call in log.call_args_list]
        self.assertTrue(any("prompt_chars=13" in message for message in messages))
        self.assertTrue(any("将重试一次" in message for message in messages))
        self.assertTrue(any(
            "finish_reason=stop" in message
            and "input_tokens=1200" in message
            and "output_tokens=640" in message
            for message in messages
        ))

    def test_second_timeout_raises_isolatable_fusion_error(self) -> None:
        class TimeoutQwen:
            def __init__(self):
                self.calls = 0

            def audit_text(self, prompt, **kwargs):
                self.calls += 1
                raise TimeoutError("read timed out")

        pipeline = bare_pipeline()
        pipeline.qwen = TimeoutQwen()
        with (
            patch.object(settings, "fusion_request_timeout", 90),
            patch.object(settings, "fusion_timeout_retries", 1),
            patch("backend.audit_agent.pipeline.job_store.log"),
        ):
            with self.assertRaises(FusionAuditTimeoutError):
                pipeline._run_fusion_audit("note-2", "fusion prompt")

        self.assertEqual(pipeline.qwen.calls, 2)

    def test_non_timeout_error_is_not_retried(self) -> None:
        class FailingQwen:
            def __init__(self):
                self.calls = 0

            def audit_text(self, prompt, **kwargs):
                self.calls += 1
                raise RuntimeError("invalid request")

        pipeline = bare_pipeline()
        pipeline.qwen = FailingQwen()
        with (
            patch.object(settings, "fusion_timeout_retries", 1),
            patch("backend.audit_agent.pipeline.job_store.log"),
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid request"):
                pipeline._run_fusion_audit("note-3", "fusion prompt")

        self.assertEqual(pipeline.qwen.calls, 1)


class CommentAuditTests(unittest.TestCase):
    @staticmethod
    def subject(count: int, *, video: bool = False) -> AuditSubject:
        return AuditSubject(
            platform="xhs",
            note_id="note-1",
            url="",
            title="帖子标题",
            desc="帖子正文",
            author={},
            image_urls=[] if video else ["image.jpg"],
            video_urls=["video.mp4"] if video else [],
            comments=[
                {"comment_id": f"c{index + 1}", "content": f"comment {index + 1}", "nickname": f"u{index + 1}"}
                for index in range(count)
            ],
        )

    def test_comment_prompt_contains_hate_context_and_severity_boundaries(self) -> None:
        pipeline = bare_pipeline()
        pipeline.prompt_profile_snapshot = {
            "libraries": [
                {
                    "id": "hate",
                    "title": "民族意识形态风险",
                    "audit_goal": "识别仇恨歧视",
                    "output_labels": ["歧视贬损", "跨群体婚恋排斥"],
                }
            ]
        }

        prompt = pipeline._render_comment_audit_prompt(
            self.subject(1),
            "帖子涉及跨民族婚恋",
            [{
                "comment_id": "c1",
                "source_text": "评论内容",
                "translation_required": False,
            }],
        )

        self.assertIn("不得依据账号 IP/发布地、使用语言、昵称、服饰等弱线索", prompt)
        self.assertIn("区分身份攻击与行为评价", prompt)
        self.assertIn("对身份已明确的受保护群体或其成员实施强侮辱", prompt)
        self.assertIn("不得补写原文没有的因果、谣言、排斥或煽动意图", prompt)
        self.assertIn("最多按低风险召回", prompt)
        self.assertIn("t/rb 应写身份关联存疑或因果不明", prompt)
        self.assertIn("t/rb 必须与 q 及可靠译文一致", prompt)

    def test_comments_are_batched_scored_and_sorted_for_image_and_video_posts(self) -> None:
        class NoTranslation:
            def should_translate(self, text, language=""):
                return False

        class BatchQwen:
            def __init__(self):
                self.calls = 0
                self.thinking_flags = []

            def audit_text(self, prompt, max_tokens=None, model=None, enable_thinking=None):
                self.calls += 1
                self.thinking_flags.append(enable_thinking)
                payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
                return {
                    "comments": [
                        {
                            "comment_id": item["comment_id"],
                            "score": int(item["comment_id"][1:]),
                            "risk_basis": "存在风险表达",
                            "exemption_basis": "结合上下文仍需复核",
                            "evidence_quote": item["source_text"],
                        }
                        for item in payload["comments"]
                    ]
                }

        for video in (False, True):
            pipeline = bare_pipeline()
            pipeline.translator = NoTranslation()
            pipeline.qwen = BatchQwen()
            with (
                patch.object(settings, "comment_audit_batch_size", 20),
                patch.object(settings, "comment_audit_concurrency", 4),
                patch("backend.audit_agent.pipeline.job_store.log"),
            ):
                comments = pipeline._audit_comments(self.subject(25, video=video), "媒体摘要")
            self.assertEqual(pipeline.qwen.calls, 2)
            self.assertEqual(pipeline.qwen.thinking_flags, [False, False])
            self.assertTrue(all(comment["audit_status"] == "completed" for comment in comments))
            self.assertEqual(comments[0]["comment_id"], "c25")
            self.assertEqual(comments[-1]["comment_id"], "c1")

    def test_comment_audit_keeps_primary_and_secondary_libraries(self) -> None:
        class NoTranslation:
            def should_translate(self, text, language=""):
                return False

        class LibraryQwen:
            def audit_text(self, prompt, max_tokens=None, model=None, enable_thinking=None):
                payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
                assert [item["id"] for item in payload["library_policies"]] == ["hate", "fraud"]
                item = payload["comments"][0]
                return {
                    "comments": [{
                        "comment_id": item["comment_id"],
                        "score": 85,
                        "risk_library_id": "hate",
                        "risk_library_label": "民族意识形态风险",
                        "secondary_library_ids": ["fraud"],
                        "risk_type": "跨群体婚恋排斥",
                        "risk_basis": "基于民族身份排斥通婚",
                        "exemption_basis": "无明显豁免语境",
                        "evidence_quote": item["source_text"],
                    }]
                }

        pipeline = bare_pipeline()
        pipeline.translator = NoTranslation()
        pipeline.qwen = LibraryQwen()
        pipeline.prompt_profile_snapshot = {
            "libraries": [
                {"id": "hate", "title": "民族意识形态风险", "audit_goal": "识别仇恨", "output_labels": ["歧视贬损"]},
                {"id": "fraud", "title": "诈骗风险", "audit_goal": "识别诈骗", "output_labels": ["诈骗"]},
            ]
        }
        with (
            patch.object(settings, "comment_audit_batch_size", 20),
            patch("backend.audit_agent.pipeline.job_store.log"),
        ):
            comments = pipeline._audit_comments(self.subject(1), "媒体摘要")
        self.assertEqual(comments[0]["risk_library_id"], "hate")
        self.assertEqual(comments[0]["secondary_library_ids"], ["fraud"])
        self.assertEqual(comments[0]["risk_type"], "跨群体婚恋排斥")

    def test_comment_audit_accepts_compact_output_schema(self) -> None:
        class NoTranslation:
            def should_translate(self, text, language=""):
                return False

        class CompactQwen:
            def audit_text(self, prompt, max_tokens=None, model=None, enable_thinking=None):
                payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
                item = payload["comments"][0]
                return {
                    "comments": [{
                        "id": item["comment_id"],
                        "s": 85,
                        "lib": "hate",
                        "sec": ["fraud"],
                        "t": "跨群体婚恋排斥",
                        "rb": "基于民族身份排斥通婚",
                        "eb": "无豁免",
                        "q": item["source_text"],
                    }]
                }

        pipeline = bare_pipeline()
        pipeline.translator = NoTranslation()
        pipeline.qwen = CompactQwen()
        pipeline.prompt_profile_snapshot = {
            "libraries": [
                {"id": "hate", "title": "民族意识形态风险", "audit_goal": "识别仇恨", "output_labels": ["歧视贬损"]},
                {"id": "fraud", "title": "诈骗风险", "audit_goal": "识别诈骗", "output_labels": ["诈骗"]},
            ]
        }
        with patch("backend.audit_agent.pipeline.job_store.log"):
            comments = pipeline._audit_comments(self.subject(1), "媒体摘要")
        self.assertEqual(comments[0]["risk_score"], 85)
        self.assertEqual(comments[0]["risk_library_id"], "hate")
        self.assertEqual(comments[0]["secondary_library_ids"], ["fraud"])
        self.assertEqual(comments[0]["exemption_basis"], "无豁免")

    def test_comment_audit_translates_in_the_same_model_call(self) -> None:
        class TranslationNeeded:
            def should_translate(self, text, language=""):
                return True

            def translate_if_needed(self, *args, **kwargs):
                raise AssertionError("comment audit must not call the standalone translator")

        class TranslatingQwen:
            def __init__(self):
                self.calls = 0
                self.enable_thinking = None
                self.max_tokens = None
                self.prompt = ""

            def audit_text(self, prompt, max_tokens=None, model=None, enable_thinking=None):
                self.calls += 1
                self.enable_thinking = enable_thinking
                self.max_tokens = max_tokens
                self.prompt = prompt
                payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
                item = payload["comments"][0]
                assert item["translation_required"] is True
                return {
                    "comments": [{
                        "id": item["comment_id"],
                        "s": 0,
                        "lib": "",
                        "sec": [],
                        "t": "",
                        "rb": "无明确违规依据",
                        "eb": "正常感谢表达",
                        "q": item["source_text"],
                        "zh": "非常感谢您的帮助",
                    }]
                }

        subject = self.subject(1)
        subject.comments[0]["content"] = "رەھمەت سىزگە"
        pipeline = bare_pipeline()
        pipeline.translator = TranslationNeeded()
        pipeline.qwen = TranslatingQwen()
        with (
            patch.object(settings, "comment_audit_max_tokens", 6000),
            patch("backend.audit_agent.pipeline.job_store.log"),
        ):
            comments = pipeline._audit_comments(subject, "媒体摘要")

        self.assertEqual(pipeline.qwen.calls, 1)
        self.assertIs(pipeline.qwen.enable_thinking, False)
        self.assertEqual(pipeline.qwen.max_tokens, 6000)
        self.assertIn("s=0 时只输出 id、s", pipeline.qwen.prompt)
        self.assertNotIn("nickname", pipeline.qwen.prompt)
        self.assertEqual(comments[0]["translation_zh"], "非常感谢您的帮助")
        self.assertEqual(comments[0]["translation_status"], "completed")
        self.assertNotIn("translation_required", comments[0])

    def test_empty_comments_are_completed_without_calling_model(self) -> None:
        class NoTranslation:
            def should_translate(self, text, language=""):
                return False

        class RecordingQwen:
            def __init__(self):
                self.comment_ids = []

            def audit_text(self, prompt, max_tokens=None, model=None, enable_thinking=None):
                payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
                self.comment_ids.append([item["comment_id"] for item in payload["comments"]])
                return {
                    "comments": [
                        {"id": item["comment_id"], "s": 0}
                        for item in payload["comments"]
                    ]
                }

        subject = self.subject(3)
        subject.comments[0]["content"] = ""
        subject.comments[1]["content"] = "   "
        pipeline = bare_pipeline()
        pipeline.translator = NoTranslation()
        pipeline.qwen = RecordingQwen()
        with patch("backend.audit_agent.pipeline.job_store.log"):
            comments = pipeline._audit_comments(subject, "媒体摘要")

        self.assertEqual(pipeline.qwen.comment_ids, [["c3"]])
        by_id = {comment["comment_id"]: comment for comment in comments}
        self.assertEqual(by_id["c1"]["audit_source"], "local_empty")
        self.assertEqual(by_id["c2"]["audit_source"], "local_empty")
        self.assertEqual(by_id["c1"]["risk_score"], 0)
        self.assertEqual(by_id["c3"]["audit_status"], "completed")

    def test_missing_comment_results_are_retried_only_once(self) -> None:
        class NoTranslation:
            def should_translate(self, text, language=""):
                return False

        class MissingQwen:
            def __init__(self):
                self.calls = 0

            def audit_text(self, prompt, max_tokens=None, model=None, enable_thinking=None):
                self.calls += 1
                return {"comments": []}

        pipeline = bare_pipeline()
        pipeline.translator = NoTranslation()
        pipeline.qwen = MissingQwen()
        with patch("backend.audit_agent.pipeline.job_store.log") as log:
            comments = pipeline._audit_comments(self.subject(4), "媒体摘要")

        self.assertEqual(pipeline.qwen.calls, 2)
        self.assertTrue(all(comment["audit_status"] == "failed" for comment in comments))
        messages = [call.args[1] for call in log.call_args_list]
        self.assertTrue(any("仅补偿重试一次" in message for message in messages))
        self.assertTrue(any("不再继续重试" in message for message in messages))

    def test_retry_sends_only_comments_missing_from_first_result(self) -> None:
        class NoTranslation:
            def should_translate(self, text, language=""):
                return False

        class PartialQwen:
            def __init__(self):
                self.requested_ids = []

            def audit_text(self, prompt, max_tokens=None, model=None, enable_thinking=None):
                payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
                ids = [item["comment_id"] for item in payload["comments"]]
                self.requested_ids.append(ids)
                returned_ids = ids[:1] if len(self.requested_ids) == 1 else ids
                return {"comments": [{"id": comment_id, "s": 0} for comment_id in returned_ids]}

        pipeline = bare_pipeline()
        pipeline.translator = NoTranslation()
        pipeline.qwen = PartialQwen()
        with patch("backend.audit_agent.pipeline.job_store.log"):
            comments = pipeline._audit_comments(self.subject(3), "媒体摘要")

        self.assertEqual(pipeline.qwen.requested_ids, [["c1", "c2", "c3"], ["c2", "c3"]])
        self.assertTrue(all(comment["audit_status"] == "completed" for comment in comments))

    def test_full_batch_retry_is_split_into_ten_comment_sub_batches(self) -> None:
        class NoTranslation:
            def should_translate(self, text, language=""):
                return False

        class TruncatedThenCompleteQwen:
            def __init__(self):
                self.batch_sizes = []

            def audit_text(self, prompt, max_tokens=None, model=None, enable_thinking=None):
                payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
                rows = payload["comments"]
                self.batch_sizes.append(len(rows))
                if len(self.batch_sizes) == 1:
                    return {"raw_response": '{"comments":[{"id":"c1"'}
                return {
                    "comments": [
                        {"id": item["comment_id"], "s": 0}
                        for item in rows
                    ]
                }

        pipeline = bare_pipeline()
        pipeline.translator = NoTranslation()
        pipeline.qwen = TruncatedThenCompleteQwen()
        with (
            patch.object(settings, "comment_audit_batch_size", 20),
            patch("backend.audit_agent.pipeline.job_store.log") as log,
        ):
            comments = pipeline._audit_comments(self.subject(20), "媒体摘要")

        self.assertEqual(pipeline.qwen.batch_sizes, [20, 10, 10])
        self.assertTrue(all(comment["audit_status"] == "completed" for comment in comments))
        messages = [call.args[1] for call in log.call_args_list]
        self.assertTrue(any(
            "补偿轮次拆分" in message and "每批最多10条" in message
            for message in messages
        ))

    def test_high_risk_comment_alone_recalls_post_as_review(self) -> None:
        pipeline = bare_pipeline()
        pipeline.prompt_set = type("PromptSet", (), {"category": "hate", "prompt_version": "test"})()
        pipeline.rule_snapshot = {"thresholds": {"review": 40, "medium": 60, "high": 80}, "scoring_rules": []}
        pipeline._analyze_images = lambda subject, image_dir: []
        pipeline._analyze_videos = lambda subject, video_dir: []
        pipeline._audit_comments = lambda subject, media_summary: [{
            "comment_id": "c1",
            "content": "不能和他们通婚",
            "source_text": "不能和他们通婚",
            "nickname": "u1",
            "audit_status": "completed",
            "risk_score": 85,
            "risk_level": "high",
            "risk_library_id": "hate",
            "risk_library_label": "民族意识形态风险",
            "risk_type": "跨群体婚恋排斥",
            "risk_basis": "基于民族身份排斥通婚",
            "exemption_basis": "无明显豁免语境",
            "evidence_quote": "不能和他们通婚",
        }]

        class FusionQwen:
            def audit_text(self, prompt, max_tokens=None, model=None, enable_thinking=None, request_timeout=None):
                return {
                    "schema_version": "audit_fusion_v4",
                    "content_title": "普通内容",
                    "summary": "主帖未见风险",
                    "decision_suggestion": "pass",
                    "risk_level_suggestion": "none",
                    "primary_risk": "",
                    "categories": [],
                    "evidence_items": [],
                    "rule_matches": [],
                }

        pipeline.qwen = FusionQwen()
        subject = self.subject(1)
        subject.image_urls = []
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(settings, "outputs_dir", Path(tmp)),
            patch("backend.audit_agent.pipeline.job_store.log"),
        ):
            result = pipeline._analyze_subject(subject)

        self.assertEqual(result["risk_level"], "high")
        self.assertEqual(result["decision"], "review")
        self.assertEqual(result["risk_basis"], "comment_evidence")
        self.assertEqual(result["evidence_items"][0]["primary_modality"], "comment")

    def test_failed_batch_retries_once_without_recursive_split(self) -> None:
        class NoTranslation:
            def should_translate(self, text, language=""):
                return False

        class SplittingQwen:
            def __init__(self):
                self.batch_sizes = []

            def audit_text(self, prompt, max_tokens=None, model=None, enable_thinking=None):
                payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
                rows = payload["comments"]
                self.batch_sizes.append(len(rows))
                if len(rows) > 1:
                    raise RuntimeError("batch too large")
                item = rows[0]
                if item["comment_id"] == "c3":
                    raise RuntimeError("permanent failure")
                return {
                    "comments": [{
                        "comment_id": item["comment_id"],
                        "score": 55,
                        "risk_basis": "存在风险表达",
                        "exemption_basis": "仍有上下文豁免",
                        "evidence_quote": item["source_text"],
                    }]
                }

        pipeline = bare_pipeline()
        pipeline.translator = NoTranslation()
        pipeline.qwen = SplittingQwen()
        with patch("backend.audit_agent.pipeline.job_store.log"):
            comments = pipeline._audit_comments(self.subject(4), "媒体摘要")
        by_id = {comment["comment_id"]: comment for comment in comments}
        self.assertEqual(pipeline.qwen.batch_sizes, [4, 4])
        self.assertEqual(by_id["c1"]["audit_status"], "failed")
        self.assertEqual(by_id["c3"]["audit_status"], "failed")
        self.assertNotIn("risk_score", by_id["c3"])

    def test_missing_comment_score_is_retried_then_marked_failed(self) -> None:
        class NoTranslation:
            def should_translate(self, text, language=""):
                return False

        class MissingScoreQwen:
            def audit_text(self, prompt, max_tokens=None, model=None, enable_thinking=None):
                payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
                return {
                    "comments": [{
                        "comment_id": item["comment_id"],
                        "risk_basis": "存在风险表达",
                        "exemption_basis": "无明显豁免语境",
                        "evidence_quote": item["source_text"],
                    } for item in payload["comments"]]
                }

        pipeline = bare_pipeline()
        pipeline.translator = NoTranslation()
        pipeline.qwen = MissingScoreQwen()
        with patch("backend.audit_agent.pipeline.job_store.log"):
            comments = pipeline._audit_comments(self.subject(1), "媒体摘要")
        self.assertEqual(comments[0]["audit_status"], "failed")
        self.assertNotIn("risk_score", comments[0])


class AsrReviewTests(unittest.TestCase):
    def test_dolphin_control_only_output_is_empty(self) -> None:
        class DolphinResult:
            text = "<ug><CN><asr><notimestamp>"
            text_nospecial = ""

        processor = DemoAudioProcessor()

        self.assertEqual(processor._extract_dolphin_text(DolphinResult()), "")
        self.assertEqual(processor._dolphin_segments({
            "text": "<ug><CN><asr><notimestamp>",
            "text_nospecial": "",
        }), [])

    def test_control_only_asr_skips_translation(self) -> None:
        pipeline = bare_pipeline()

        class Qwen:
            def audit_text(self, *args, **kwargs):
                raise AssertionError("control-only ASR must not call the translation model")

        pipeline.qwen = Qwen()
        transcript = {
            "text": "<ug><CN><asr><notimestamp>",
            "language": "ug",
            "segments": [{"start": 0.0, "end": 1.0, "text": "<ug><CN><asr><notimestamp>"}],
        }
        with patch("backend.audit_agent.pipeline.job_store.log") as log:
            result = pipeline._translate_transcript_if_needed(transcript, "视频 1")

        self.assertEqual(result["text"], "")
        self.assertEqual(result["segments"], [])
        self.assertTrue(result["translation"]["skipped"])
        self.assertEqual(result["translation"]["reason"], "no valid ASR speech text")
        messages = [call.args[1] for call in log.call_args_list]
        self.assertTrue(any("未识别到有效语音文本，跳过翻译" in message for message in messages))

    def test_asr_translation_input_does_not_truncate_segments_or_total_text(self) -> None:
        pipeline = bare_pipeline()
        first = "ا" * 221
        second = "ب" * 8001
        transcript = {
            "text": first + second,
            "segments": [
                {"start": 0.0, "end": 1.0, "text": first},
                {"start": 1.0, "end": 2.0, "text": second},
            ],
        }

        with patch.object(settings, "transcript_max_chars", 10):
            segments = pipeline._compact_asr_segments_for_translation(transcript)

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["text"], first)
        self.assertEqual(segments[1]["text"], second)
        self.assertEqual(sum(len(item["text"]) for item in segments), 8222)

    def test_asr_translation_fallback_keeps_full_unsegmented_text(self) -> None:
        pipeline = bare_pipeline()
        source = "ئۇيغۇرچە" * 1200

        with patch.object(settings, "transcript_max_chars", 10):
            segments = pipeline._compact_asr_segments_for_translation({"text": source})

        self.assertEqual(segments, [{"index": 1, "start": 0.0, "end": 0.0, "text": source}])

    def test_uyghur_translation_uses_only_dolphin_segments(self) -> None:
        pipeline = bare_pipeline()

        class Translator:
            def should_translate(self, text, language="", trust_language_label=True):
                return trust_language_label and language == "ug"

        class Qwen:
            prompt = ""
            kwargs = {}

            def audit_text(self, prompt, **kwargs):
                self.prompt = prompt
                self.kwargs = kwargs
                return {
                    "segments": [{
                        "index": 1,
                        "translation_zh": "中文译文",
                    }],
                }

        pipeline.translator = Translator()
        pipeline.qwen = Qwen()
        transcript = {
            "text": "ئۇيغۇرچە",
            "language": "ug",
            "asr_engine": "dolphin",
            "segments": [{"start": 1.0, "end": 2.0, "text": "ئۇيغۇرچە"}],
        }
        with (
            patch.object(settings, "asr_translate_engine", "qwen_text"),
            patch.object(settings, "asr_translate_max_tokens", 6000),
            patch.object(settings, "asr_translate_enable_thinking", False),
            patch("backend.audit_agent.pipeline.job_store.log"),
        ):
            result = pipeline._translate_transcript_if_needed(transcript, "视频 1")
        self.assertEqual(result["segments"][0]["start"], 1.0)
        self.assertEqual(result["segments"][0]["end"], 2.0)
        self.assertEqual(result["segments"][0]["translation_zh"], "中文译文")
        self.assertEqual(result["segments"][0]["source_text_mms"], "")
        self.assertNotIn("mms", result)
        self.assertNotIn("MMS", pipeline.qwen.prompt)
        self.assertIn('"full_source_text": "[1] ئۇيغۇرچە"', pipeline.qwen.prompt)
        self.assertNotIn('"alignment_units"', pipeline.qwen.prompt)
        self.assertNotIn('"start":', pipeline.qwen.prompt)
        self.assertNotIn('"end":', pipeline.qwen.prompt)
        self.assertIn("不要把不确定猜测写成确定事实", pipeline.qwen.prompt)
        self.assertIn('"global_translation_zh"', pipeline.qwen.prompt)
        self.assertEqual(pipeline.qwen.kwargs["max_tokens"], 6000)
        self.assertFalse(pipeline.qwen.kwargs["enable_thinking"])

    def test_invalid_asr_translation_json_reports_a_diagnostic_error(self) -> None:
        pipeline = bare_pipeline()

        class Qwen:
            def audit_text(self, prompt, **kwargs):
                return {"raw_response": '{"segments":[{"index":1,"translation_zh":"截断'}

        pipeline.qwen = Qwen()
        with (
            patch.object(settings, "asr_translate_max_tokens", 6000),
            patch.object(settings, "asr_translate_enable_thinking", True),
        ):
            translation = pipeline._translate_asr_segments_with_llm({
                "text": "ئۇيغۇرچە",
                "language": "ug",
                "asr_engine": "dolphin",
                "segments": [{"start": 1.0, "end": 2.0, "text": "ئۇيغۇرچە"}],
            })

        self.assertFalse(translation["translated"])
        self.assertIn("incomplete or invalid JSON", translation["error"])

    def test_truncated_asr_translation_is_split_and_merged_by_original_index(self) -> None:
        pipeline = bare_pipeline()

        class Qwen:
            def __init__(self):
                self.payloads = []

            def audit_text(self, prompt, **kwargs):
                payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
                self.payloads.append(payload)
                target_indexes = payload.get("target_indexes")
                if target_indexes is None:
                    return {
                        "raw_response": '{"segments":[{"index":1',
                        "_llm_meta": {
                            "finish_reason": "length",
                            "completion_tokens": 6000,
                        },
                    }
                return {
                    "segments": [
                        {
                            "index": index,
                            "translation_zh": f"译文{index}",
                            "confidence": "high",
                            "notes": "",
                        }
                        for index in target_indexes
                    ],
                    "global_translation_zh": " ".join(f"译文{index}" for index in target_indexes),
                    "_llm_meta": {"finish_reason": "stop"},
                }

        pipeline.qwen = Qwen()
        transcript = {
            "text": "a b c d",
            "language": "ug",
            "asr_engine": "dolphin",
            "segments": [
                {"start": float(index), "end": float(index) + 0.5, "text": f"source-{index + 1}"}
                for index in range(4)
            ],
        }
        with (
            patch.object(settings, "asr_translate_max_tokens", 6000),
            patch.object(settings, "asr_translate_enable_thinking", False),
            patch("backend.audit_agent.pipeline.job_store.log") as log,
        ):
            translation = pipeline._translate_asr_segments_with_llm(transcript)

        self.assertTrue(translation["translated"])
        self.assertTrue(translation["split_retry"])
        self.assertEqual(translation["batch_count"], 2)
        self.assertEqual(translation["text"], "译文1 译文2 译文3 译文4")
        self.assertEqual(
            [item["translation_zh"] for item in translation["segments"]],
            ["译文1", "译文2", "译文3", "译文4"],
        )
        self.assertEqual(
            [(item["start"], item["end"]) for item in translation["segments"]],
            [(0.0, 0.5), (1.0, 1.5), (2.0, 2.5), (3.0, 3.5)],
        )
        self.assertEqual(
            [payload.get("target_indexes") for payload in pipeline.qwen.payloads],
            [None, [1, 2], [3, 4]],
        )
        self.assertTrue(all("[4] source-4" in payload["full_source_text"] for payload in pipeline.qwen.payloads))
        self.assertTrue(any("自动按段拆批重试" in call.args[1] for call in log.call_args_list))

    def test_truncated_asr_sub_batches_are_not_retried_recursively(self) -> None:
        pipeline = bare_pipeline()

        class Qwen:
            calls = 0

            def audit_text(self, prompt, **kwargs):
                self.calls += 1
                payload = json.loads(prompt.split("输入 JSON：\n", 1)[1])
                target_indexes = payload.get("target_indexes") or [1, 2, 3, 4]
                if len(target_indexes) > 1:
                    return {
                        "raw_response": '{"segments":[]',
                        "_llm_meta": {"finish_reason": "length"},
                    }
                index = target_indexes[0]
                return {
                    "segments": [{"index": index, "translation_zh": f"译文{index}"}],
                    "global_translation_zh": f"译文{index}",
                    "_llm_meta": {"finish_reason": "stop"},
                }

        pipeline.qwen = Qwen()
        transcript = {
            "text": "a b c d",
            "segments": [
                {"start": float(index), "end": float(index + 1), "text": f"source-{index + 1}"}
                for index in range(4)
            ],
        }
        with patch("backend.audit_agent.pipeline.job_store.log"):
            translation = pipeline._translate_asr_segments_with_llm(transcript)

        self.assertFalse(translation["translated"])
        self.assertEqual(translation["batch_count"], 2)
        self.assertEqual(pipeline.qwen.calls, 3)
        self.assertIn("missing_indexes=1,2,3,4", translation["error"])


class SubjectMetadataTests(unittest.TestCase):
    @staticmethod
    def subject(title: str = "ئۇيغۇرچە تېكىست", desc: str = "ئۇيغۇرچە تېكىست") -> AuditSubject:
        return AuditSubject(
            platform="dy",
            note_id="7662302011467765617",
            url="https://www.douyin.com/video/7662302011467765617",
            title=title,
            desc=desc,
            author={},
            image_urls=[],
            video_urls=[],
            comments=[],
        )

    def test_subject_title_and_desc_translation_reuse_duplicate_text(self) -> None:
        class Translator:
            def should_translate(self, text, language="", trust_language_label=True):
                return True

        class Qwen:
            def __init__(self):
                self.calls = []

            def audit_text(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                return {"title_zh": "中文译文", "desc_zh": "中文译文"}

        pipeline = bare_pipeline()
        pipeline.translator = Translator()
        pipeline.qwen = Qwen()
        subject = self.subject()
        with patch("backend.audit_agent.pipeline.job_store.log"):
            pipeline._translate_subject_texts(subject)

        self.assertEqual(subject.title_zh, "中文译文")
        self.assertEqual(subject.desc_zh, "中文译文")
        self.assertEqual(len(pipeline.qwen.calls), 1)
        self.assertEqual(pipeline.qwen.calls[0][1]["max_tokens"], 512)
        self.assertFalse(pipeline.qwen.calls[0][1]["enable_thinking"])

    def test_missing_content_title_is_generated_once_without_thinking(self) -> None:
        class Qwen:
            def __init__(self):
                self.calls = []

            def audit_text(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                return {"content_title": "维吾尔语征婚介绍"}

        pipeline = bare_pipeline()
        pipeline.qwen = Qwen()
        subject = self.subject(title="", desc="")
        with patch("backend.audit_agent.pipeline.job_store.log"):
            title = pipeline._ensure_content_title(
                {"summary": "视频为维吾尔语征婚介绍，属于正常个人诉求。"},
                subject,
                {"image_units": [], "segment_reviews": []},
            )

        self.assertEqual(title, "维吾尔语征婚介绍")
        self.assertEqual(len(pipeline.qwen.calls), 1)
        self.assertEqual(pipeline.qwen.calls[0][1]["max_tokens"], 64)
        self.assertFalse(pipeline.qwen.calls[0][1]["enable_thinking"])

    def test_safe_evidence_is_not_exposed_as_risk_evidence(self) -> None:
        result = {
            "decision": "pass",
            "risk_level": "none",
            "evidence_items": [{
                "evidence_id": "text:title",
                "primary_modality": "text",
                "source": "title",
                "text": "ئۇيغۇرچە تېكىست",
                "translation_zh": "中文译文",
                "risk_library_id": "hate",
                "evidence_risk_level": "none",
                "reason": "正常生活表达，无风险",
            }],
        }
        self.assertEqual(build_evidence_groups(result), [])

    def test_normalization_discards_none_evidence_but_keeps_risk(self) -> None:
        pipeline = bare_pipeline()
        subject = self.subject(title="标题", desc="正文")
        audit = {
            "evidence_items": [
                {"evidence_id": "text:title", "evidence_risk_level": "none", "reason": "安全"},
                {"evidence_id": "text:desc", "evidence_risk_level": "low", "reason": "待复核"},
            ]
        }
        evidence_index = {
            "evidence_catalog": [
                {"evidence_id": "text:title", "source": "title", "primary_modality": "text"},
                {"evidence_id": "text:desc", "source": "desc", "primary_modality": "text"},
            ]
        }
        normalized = pipeline._normalize_evidence_items(audit, subject, evidence_index)

        self.assertEqual([item["evidence_id"] for item in normalized], ["text:desc"])
        self.assertEqual(normalized[0]["text"], "正文")


if __name__ == "__main__":
    unittest.main()
