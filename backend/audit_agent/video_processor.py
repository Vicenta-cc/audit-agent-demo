from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

from .config import settings


class DemoAudioProcessor:
    def __init__(self, model_size: str | None = None):
        self.model_size = model_size or settings.whisper_model
        self._model = None
        self.device = settings.whisper_device
        self.compute_type = settings.whisper_compute_type
        self._load_error = ""

    def extract_audio(self, video_path: Path, output_dir: Path) -> Path | None:
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_path = output_dir / "audio.wav"
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-i",
                    str(video_path),
                    "-vn",
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-y",
                    str(audio_path),
                ],
                check=True,
                capture_output=True,
            )
            return audio_path
        except Exception:
            return None

    def transcribe(self, audio_path: Path) -> dict:
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            return {"text": "", "segments": [], "error": f"faster-whisper unavailable: {exc}"}

        if self._model is None:
            self._model = self._load_model(WhisperModel)
        segments, info = self._model.transcribe(
            str(audio_path),
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            language="zh",
        )
        segment_list = list(segments)
        result = {
            "text": " ".join(seg.text for seg in segment_list),
            "language": info.language,
            "device": self.device if not self._load_error else "cpu",
            "compute_type": self.compute_type if not self._load_error else "float32",
            "segments": [
                {
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text,
                    "words": [
                        {
                            "word": word.word,
                            "start": word.start,
                            "end": word.end,
                            "probability": word.probability,
                        }
                        for word in (seg.words or [])
                    ],
                }
                for seg in segment_list
            ],
        }
        if self._load_error:
            result["fallback_reason"] = self._load_error
        return result

    def _load_model(self, whisper_model_cls):
        try:
            return whisper_model_cls(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
        except Exception as exc:
            self._load_error = f"load {self.device}/{self.compute_type} failed: {exc}"
            if not settings.whisper_cpu_fallback or self.device == "cpu":
                raise
            return whisper_model_cls(self.model_size, device="cpu", compute_type="float32")


class DemoFrameExtractor:
    threshold = 10.0

    def extract_keyframes(self, video_path: Path, output_dir: Path, max_frames: int) -> list[dict]:
        output_dir.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total_frames <= 0:
            cap.release()
            return []

        sample_interval = max(1, total_frames // max(max_frames * 3, 1))
        candidates = []
        sampled_diffs = []
        prev = None
        frame_num = 0
        while frame_num < total_frames:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_num % sample_interval == 0:
                score = self._diff(frame, prev)
                sampled_diffs.append({
                    "frame_num": frame_num,
                    "score": float(score),
                    "global_score": float(score),
                    "block_score": float(score),
                })
                if prev is None or score > self.threshold:
                    candidates.append((frame_num, frame.copy(), score))
                prev = frame.copy()
            frame_num += 1
        cap.release()

        selected = sorted(candidates, key=lambda item: item[2], reverse=True)[:max_frames]
        # Keep output easier to inspect by restoring chronological order.
        selected = sorted(selected, key=lambda item: item[0])
        selected_frame_nums = {frame_num for frame_num, _frame, _score in selected}
        self._write_keyframe_curve_artifacts(
            output_dir=output_dir,
            fps=fps,
            diffs=sampled_diffs,
            selected_frame_nums=selected_frame_nums,
        )

        frames = []
        for idx, (frame_num, frame, score) in enumerate(selected):
            path = output_dir / f"frame_{idx:02d}.jpg"
            cv2.imwrite(str(path), frame)
            frames.append({
                "index": idx,
                "path": str(path),
                "timestamp": frame_num / fps,
                "frame_number": frame_num,
                "score": float(score),
                "diff_curve_path": str(output_dir / "keyframe_curve.png"),
                "diff_curve_json_path": str(output_dir / "keyframe_curve.json"),
                "kind": "keyframe",
            })
        return frames

    def _write_keyframe_curve_artifacts(
        self,
        output_dir: Path,
        fps: float,
        diffs: list[dict],
        selected_frame_nums: set[int],
    ) -> None:
        if not diffs:
            return
        payload = {
            "fps": fps,
            "diffs": [
                {
                    "frame_num": item["frame_num"],
                    "timestamp": item["frame_num"] / fps,
                    "score": item["score"],
                }
                for item in diffs
            ],
            "selected_frames": [
                {
                    "frame_num": item["frame_num"],
                    "timestamp": item["frame_num"] / fps,
                    "score": item["score"],
                }
                for item in diffs
                if item["frame_num"] in selected_frame_nums
            ],
        }
        (output_dir / "keyframe_curve.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._write_keyframe_curve_png(
            output_dir / "keyframe_curve.png",
            fps=fps,
            diffs=diffs,
            selected_frame_nums=selected_frame_nums,
        )

    def _write_keyframe_curve_png(
        self,
        path: Path,
        fps: float,
        diffs: list[dict],
        selected_frame_nums: set[int],
    ) -> None:
        if not diffs:
            return

        width = 1200
        height = 420
        margin_left = 56
        margin_right = 24
        margin_top = 24
        margin_bottom = 44
        plot_w = width - margin_left - margin_right
        plot_h = height - margin_top - margin_bottom
        image = np.full((height, width, 3), 255, dtype=np.uint8)
        max_score = max(max(item["score"] for item in diffs), 1.0)

        def point(frame_num: int, score: float) -> tuple[int, int]:
            if len(diffs) <= 1 or diffs[-1]["frame_num"] == diffs[0]["frame_num"]:
                x = margin_left
            else:
                x = margin_left + int((frame_num - diffs[0]["frame_num"]) / (diffs[-1]["frame_num"] - diffs[0]["frame_num"]) * plot_w)
            y = margin_top + plot_h - int(score / max_score * plot_h)
            return x, y

        cv2.rectangle(image, (margin_left, margin_top), (margin_left + plot_w, margin_top + plot_h), (220, 220, 220), 1)
        duration = diffs[-1]["frame_num"] / fps
        tick_step = self._time_tick_step(duration)
        tick = 0.0
        while tick <= duration + 1e-9:
            frame_num = int(round(tick * fps))
            x, _ = point(frame_num, 0)
            cv2.line(image, (x, margin_top + plot_h), (x, margin_top + plot_h + 6), (120, 120, 120), 1)
            cv2.line(image, (x, margin_top), (x, margin_top + plot_h), (238, 238, 238), 1)
            cv2.putText(image, self._format_seconds(tick), (max(margin_left - 12, x - 14), margin_top + plot_h + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (80, 80, 80), 1, cv2.LINE_AA)
            tick += tick_step

        points = [point(item["frame_num"], item["score"]) for item in diffs]
        for start, end in zip(points, points[1:]):
            cv2.line(image, start, end, (45, 45, 45), 1)

        for item in diffs:
            if item["frame_num"] not in selected_frame_nums:
                continue
            x, y = point(item["frame_num"], item["score"])
            cv2.line(image, (x, margin_top), (x, margin_top + plot_h), (40, 40, 230), 1)
            cv2.circle(image, (x, y), 5, (0, 0, 255), -1)

        cv2.putText(image, "Keyframe diff curve", (margin_left, height - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (40, 40, 40), 1, cv2.LINE_AA)
        cv2.putText(image, "red=selected keyframes", (margin_left + 210, height - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (70, 70, 70), 1, cv2.LINE_AA)
        cv2.imwrite(str(path), image)

    def detect_flash_spikes(self, video_path: Path, output_dir: Path, max_spikes: int) -> list[dict]:
        """Scan adjacent frame triples for isolated flash insertions."""
        output_dir.mkdir(parents=True, exist_ok=True)

        diff_spikes = self._detect_diff_peak_segments(video_path, output_dir, max_spikes)
        if diff_spikes:
            return diff_spikes

        if settings.pyscenedetect_enabled:
            scene_spikes = self._detect_pyscenedetect_short_scenes(video_path, output_dir, max_spikes)
            if scene_spikes:
                return scene_spikes

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        spike_threshold = settings.video_spike_threshold
        return_threshold = max(3.0, spike_threshold * 0.45)
        dedupe_window = max(1, settings.video_spike_max_len)
        short_segment_max_frames = settings.video_short_segment_max_frames
        if short_segment_max_frames <= 0:
            short_segment_max_frames = max(1, int(fps * settings.video_short_segment_max_seconds))

        ok, prev_frame = cap.read()
        if not ok:
            cap.release()
            return []
        ok, cur_frame = cap.read()
        if not ok:
            cap.release()
            return []

        prev_gray = self._gray_small(prev_frame)
        cur_gray = self._gray_small(cur_frame)
        cur_frame_num = 1
        last_spike_frame = -dedupe_window - 1
        spikes: list[dict] = []
        fallback_candidates: list[dict] = []
        current_segment: dict | None = None

        while True:
            ok, next_frame = cap.read()
            if not ok:
                break
            next_gray = self._gray_small(next_frame)

            pc_global, pc_block = self._diff_scores(prev_gray, cur_gray)
            cn_global, cn_block = self._diff_scores(cur_gray, next_gray)
            pn_global, pn_block = self._diff_scores(prev_gray, next_gray)

            pc_spike = max(pc_global, pc_block)
            cn_spike = max(cn_global, cn_block)
            pn_return = max(pn_global, pn_block * 0.5)
            score = min(pc_spike, cn_spike) - pn_return
            fallback_score = min(pc_spike, cn_spike) - pn_return * 0.1

            if pc_spike > 0 or cn_spike > 0:
                fallback_candidates.append({
                    "frame_num": cur_frame_num,
                    "frame": cur_frame.copy(),
                    "score": float(fallback_score),
                    "prev_curr_global": float(pc_global),
                    "curr_next_global": float(cn_global),
                    "prev_next_global": float(pn_global),
                    "prev_curr_block": float(pc_block),
                    "curr_next_block": float(cn_block),
                    "prev_next_block": float(pn_block),
                    "detection_mode": "fallback_candidate",
                })

            if pc_spike >= spike_threshold and current_segment is None:
                current_segment = {
                    "start_frame": cur_frame_num,
                    "best_frame_num": cur_frame_num,
                    "best_frame": cur_frame.copy(),
                    "best_score": float(pc_spike),
                    "prev_curr_global": float(pc_global),
                    "curr_next_global": float(cn_global),
                    "prev_next_global": float(pn_global),
                    "prev_curr_block": float(pc_block),
                    "curr_next_block": float(cn_block),
                    "prev_next_block": float(pn_block),
                }
            elif current_segment is not None:
                segment_score = max(pc_spike, cn_spike)
                if segment_score > current_segment["best_score"]:
                    current_segment.update({
                        "best_frame_num": cur_frame_num,
                        "best_frame": cur_frame.copy(),
                        "best_score": float(segment_score),
                        "prev_curr_global": float(pc_global),
                        "curr_next_global": float(cn_global),
                        "prev_next_global": float(pn_global),
                        "prev_curr_block": float(pc_block),
                        "curr_next_block": float(cn_block),
                        "prev_next_block": float(pn_block),
                    })
                segment_len = cur_frame_num - current_segment["start_frame"] + 1
                if cn_spike >= spike_threshold:
                    if (
                        segment_len <= short_segment_max_frames
                        and cur_frame_num - last_spike_frame > dedupe_window
                    ):
                        spikes.append({
                            "frame_num": current_segment["best_frame_num"],
                            "frame": current_segment["best_frame"],
                            "score": current_segment["best_score"],
                            "prev_curr_global": current_segment["prev_curr_global"],
                            "curr_next_global": current_segment["curr_next_global"],
                            "prev_next_global": current_segment["prev_next_global"],
                            "prev_curr_block": current_segment["prev_curr_block"],
                            "curr_next_block": current_segment["curr_next_block"],
                            "prev_next_block": current_segment["prev_next_block"],
                            "segment_start_frame": current_segment["start_frame"],
                            "segment_end_frame": cur_frame_num,
                            "segment_duration": segment_len / fps,
                            "detection_mode": "short_segment",
                        })
                        last_spike_frame = cur_frame_num
                    current_segment = None
                elif segment_len > short_segment_max_frames:
                    current_segment = None

            if (
                pc_spike >= spike_threshold
                and cn_spike >= spike_threshold
                and pn_return <= return_threshold
                and score > 0
                and cur_frame_num - last_spike_frame > dedupe_window
            ):
                spikes.append({
                    "frame_num": cur_frame_num,
                    "frame": cur_frame.copy(),
                    "score": float(score),
                    "prev_curr_global": float(pc_global),
                    "curr_next_global": float(cn_global),
                    "prev_next_global": float(pn_global),
                    "prev_curr_block": float(pc_block),
                    "curr_next_block": float(cn_block),
                    "prev_next_block": float(pn_block),
                    "detection_mode": "strict_return",
                })
                last_spike_frame = cur_frame_num

            prev_frame, prev_gray = cur_frame, cur_gray
            cur_frame, cur_gray = next_frame, next_gray
            cur_frame_num += 1

        cap.release()

        if not spikes:
            fallback_candidates.sort(key=lambda item: item["score"], reverse=True)
            fallback_limit = max_spikes if max_spikes > 0 else len(fallback_candidates)
            for candidate in fallback_candidates:
                if len(spikes) >= fallback_limit:
                    break
                if any(abs(candidate["frame_num"] - item["frame_num"]) <= dedupe_window for item in spikes):
                    continue
                spikes.append(candidate)

        spikes.sort(key=lambda item: item["score"], reverse=True)
        if max_spikes > 0:
            spikes = spikes[:max_spikes]
        spikes.sort(key=lambda item: item["frame_num"])

        results = []
        for idx, spike in enumerate(spikes):
            path = output_dir / f"spike_{idx:02d}.jpg"
            cv2.imwrite(str(path), spike["frame"])
            results.append({
                "index": idx,
                "path": str(path),
                "timestamp": spike["frame_num"] / fps,
                "frame_number": spike["frame_num"],
                "score": spike["score"],
                "prev_curr_global": spike["prev_curr_global"],
                "curr_next_global": spike["curr_next_global"],
                "prev_next_global": spike["prev_next_global"],
                "prev_curr_block": spike["prev_curr_block"],
                "curr_next_block": spike["curr_next_block"],
                "prev_next_block": spike["prev_next_block"],
                "segment_start_frame": spike.get("segment_start_frame"),
                "segment_end_frame": spike.get("segment_end_frame"),
                "segment_duration": spike.get("segment_duration"),
                "detection_mode": spike.get("detection_mode", "strict_return"),
                "kind": "flash_spike",
            })
        return results

    def _detect_diff_peak_segments(
        self,
        video_path: Path,
        output_dir: Path,
        max_spikes: int,
    ) -> list[dict]:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        threshold = settings.video_spike_threshold
        min_gap = max(1, int(fps * settings.video_diff_pair_min_seconds))
        max_gap = settings.video_short_segment_max_frames
        if max_gap <= 0:
            max_gap = max(1, int(fps * settings.video_short_segment_max_seconds))
        dedupe_window = max(1, settings.video_spike_max_len)
        peak_limit = settings.video_diff_peak_top_k if settings.video_diff_peak_top_k > 0 else max_spikes

        ok, prev_frame = cap.read()
        if not ok:
            cap.release()
            return []

        prev_gray = self._gray_small(prev_frame)
        frame_idx = 1
        diffs: list[dict] = []
        frames_by_num: dict[int, np.ndarray] = {}

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            gray = self._gray_small(frame)
            global_score, block_score = self._diff_scores(prev_gray, gray)
            score = max(global_score, block_score)
            diffs.append({
                "frame_num": frame_idx,
                "score": float(score),
                "global_score": float(global_score),
                "block_score": float(block_score),
            })
            if score >= threshold:
                frames_by_num[frame_idx] = frame.copy()
            prev_gray = gray
            frame_idx += 1

        cap.release()
        if not diffs:
            return []

        peaks: list[dict] = []
        for i, item in enumerate(diffs):
            prev_score = diffs[i - 1]["score"] if i > 0 else -1.0
            next_score = diffs[i + 1]["score"] if i + 1 < len(diffs) else -1.0
            if item["score"] >= threshold and item["score"] >= prev_score and item["score"] >= next_score:
                peaks.append(item)

        segments: list[dict] = []
        used_frames: set[int] = set()
        for idx, start in enumerate(peaks[:-1]):
            for end in peaks[idx + 1:]:
                gap = end["frame_num"] - start["frame_num"]
                if gap < min_gap:
                    continue
                if gap > max_gap:
                    break

                start_frame = frames_by_num.get(start["frame_num"])
                if start_frame is None:
                    start_frame = self._read_frame(video_path, start["frame_num"])
                end_frame = frames_by_num.get(end["frame_num"])
                if end_frame is None:
                    end_frame = self._read_frame(video_path, end["frame_num"])
                if start_frame is None or end_frame is None:
                    continue

                segments.append({
                    "frame_num": start["frame_num"],
                    "frame": start_frame,
                    "score": float(start["score"] + end["score"]),
                    "peak_start_frame": start["frame_num"],
                    "peak_end_frame": end["frame_num"],
                    "peak_role": "start",
                    "segment_start_frame": start["frame_num"],
                    "segment_end_frame": end["frame_num"],
                    "segment_duration": gap / fps,
                    "prev_curr_global": start["global_score"],
                    "prev_curr_block": start["block_score"],
                    "detection_mode": "short_segment_diff_pair_start",
                })
                segments.append({
                    "frame_num": end["frame_num"],
                    "frame": end_frame,
                    "score": float(start["score"] + end["score"]),
                    "peak_start_frame": start["frame_num"],
                    "peak_end_frame": end["frame_num"],
                    "peak_role": "end",
                    "segment_start_frame": start["frame_num"],
                    "segment_end_frame": end["frame_num"],
                    "segment_duration": gap / fps,
                    "prev_curr_global": end["global_score"],
                    "prev_curr_block": end["block_score"],
                    "detection_mode": "short_segment_diff_pair_end",
                })
                used_frames.add(start["frame_num"])
                used_frames.add(end["frame_num"])
                break

        self._write_diff_debug_artifacts(
            output_dir=output_dir,
            fps=fps,
            threshold=threshold,
            min_gap=min_gap,
            max_gap=max_gap,
            diffs=diffs,
            peaks=peaks,
            segments=segments,
        )

        results_source = segments
        if not results_source:
            peak_candidates = sorted(peaks, key=lambda item: item["score"], reverse=True)
            if peak_limit > 0:
                peak_candidates = peak_candidates[:peak_limit]
            results_source = []
            for peak in peak_candidates:
                if any(abs(peak["frame_num"] - used) <= dedupe_window for used in used_frames):
                    continue
                frame = frames_by_num.get(peak["frame_num"])
                if frame is None:
                    frame = self._read_frame(video_path, peak["frame_num"])
                if frame is None:
                    continue
                results_source.append({
                    "frame_num": peak["frame_num"],
                    "frame": frame,
                    "score": peak["score"],
                    "prev_curr_global": peak["global_score"],
                    "prev_curr_block": peak["block_score"],
                    "detection_mode": "diff_peak",
                })
                used_frames.add(peak["frame_num"])

        results_source.sort(key=lambda item: item["score"], reverse=True)
        if max_spikes > 0:
            results_source = results_source[:max_spikes]
        results_source.sort(key=lambda item: item["frame_num"])

        results = []
        for idx, item in enumerate(results_source):
            path = output_dir / f"diff_{idx:02d}.jpg"
            cv2.imwrite(str(path), item["frame"])
            results.append({
                "index": idx,
                "path": str(path),
                "timestamp": item["frame_num"] / fps,
                "frame_number": item["frame_num"],
                "score": item["score"],
                "prev_curr_global": item.get("prev_curr_global"),
                "prev_curr_block": item.get("prev_curr_block"),
                "peak_role": item.get("peak_role"),
                "peak_start_frame": item.get("peak_start_frame"),
                "peak_end_frame": item.get("peak_end_frame"),
                "segment_start_frame": item.get("segment_start_frame"),
                "segment_end_frame": item.get("segment_end_frame"),
                "segment_duration": item.get("segment_duration"),
                "detection_mode": item["detection_mode"],
                "diff_curve_path": str(output_dir / "diff_curve.png"),
                "diff_curve_json_path": str(output_dir / "diff_curve.json"),
                "kind": "flash_spike",
            })
        return results

    def _write_diff_debug_artifacts(
        self,
        output_dir: Path,
        fps: float,
        threshold: float,
        min_gap: int,
        max_gap: int,
        diffs: list[dict],
        peaks: list[dict],
        segments: list[dict],
    ) -> None:
        debug_json = output_dir / "diff_curve.json"
        debug_png = output_dir / "diff_curve.png"
        payload = {
            "fps": fps,
            "threshold": threshold,
            "min_gap_frames": min_gap,
            "max_gap_frames": max_gap,
            "min_gap_seconds": min_gap / fps,
            "max_gap_seconds": max_gap / fps,
            "diffs": [
                {
                    "frame_num": item["frame_num"],
                    "timestamp": item["frame_num"] / fps,
                    "score": item["score"],
                    "global_score": item["global_score"],
                    "block_score": item["block_score"],
                }
                for item in diffs
            ],
            "peaks": [
                {
                    "frame_num": item["frame_num"],
                    "timestamp": item["frame_num"] / fps,
                    "score": item["score"],
                    "global_score": item["global_score"],
                    "block_score": item["block_score"],
                }
                for item in peaks
            ],
            "segments": [
                {
                    "peak_start_frame": item.get("peak_start_frame"),
                    "peak_end_frame": item.get("peak_end_frame"),
                    "peak_start_time": item.get("peak_start_frame") / fps if item.get("peak_start_frame") is not None else None,
                    "peak_end_time": item.get("peak_end_frame") / fps if item.get("peak_end_frame") is not None else None,
                    "segment_start_frame": item.get("segment_start_frame"),
                    "segment_end_frame": item.get("segment_end_frame"),
                    "segment_start_time": item.get("segment_start_frame") / fps if item.get("segment_start_frame") is not None else None,
                    "segment_end_time": item.get("segment_end_frame") / fps if item.get("segment_end_frame") is not None else None,
                    "segment_duration": item.get("segment_duration"),
                    "score": item.get("score"),
                    "detection_mode": item.get("detection_mode"),
                }
                for item in segments
            ],
        }
        debug_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_diff_curve_png(debug_png, fps, diffs, peaks, segments, threshold)

    def _write_diff_curve_png(
        self,
        path: Path,
        fps: float,
        diffs: list[dict],
        peaks: list[dict],
        segments: list[dict],
        threshold: float,
    ) -> None:
        if not diffs:
            return

        width = 1200
        height = 420
        margin_left = 56
        margin_right = 24
        margin_top = 24
        margin_bottom = 44
        plot_w = width - margin_left - margin_right
        plot_h = height - margin_top - margin_bottom
        image = np.full((height, width, 3), 255, dtype=np.uint8)

        max_score = max(max(item["score"] for item in diffs), threshold, 1.0)

        def point(frame_num: int, score: float) -> tuple[int, int]:
            if len(diffs) <= 1:
                x = margin_left
            else:
                x = margin_left + int((frame_num - diffs[0]["frame_num"]) / (diffs[-1]["frame_num"] - diffs[0]["frame_num"]) * plot_w)
            y = margin_top + plot_h - int(score / max_score * plot_h)
            return x, y

        cv2.rectangle(image, (margin_left, margin_top), (margin_left + plot_w, margin_top + plot_h), (220, 220, 220), 1)

        duration = diffs[-1]["frame_num"] / fps
        tick_step = self._time_tick_step(duration)
        tick = 0.0
        while tick <= duration + 1e-9:
            frame_num = int(round(tick * fps))
            x, _ = point(frame_num, 0)
            cv2.line(image, (x, margin_top + plot_h), (x, margin_top + plot_h + 6), (120, 120, 120), 1)
            cv2.line(image, (x, margin_top), (x, margin_top + plot_h), (238, 238, 238), 1)
            label = self._format_seconds(tick)
            cv2.putText(image, label, (max(margin_left - 12, x - 14), margin_top + plot_h + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (80, 80, 80), 1, cv2.LINE_AA)
            tick += tick_step

        threshold_y = point(diffs[0]["frame_num"], threshold)[1]
        cv2.line(image, (margin_left, threshold_y), (margin_left + plot_w, threshold_y), (80, 80, 220), 1)
        cv2.putText(image, f"threshold={threshold:.1f}", (margin_left + 8, max(14, threshold_y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (80, 80, 220), 1, cv2.LINE_AA)

        points = [point(item["frame_num"], item["score"]) for item in diffs]
        for start, end in zip(points, points[1:]):
            cv2.line(image, start, end, (45, 45, 45), 1)

        for segment in segments:
            start_frame = segment.get("segment_start_frame")
            end_frame = segment.get("segment_end_frame")
            if start_frame is None or end_frame is None:
                continue
            x1, _ = point(start_frame, 0)
            x2, _ = point(end_frame, 0)
            overlay = image.copy()
            cv2.rectangle(overlay, (x1, margin_top), (x2, margin_top + plot_h), (210, 245, 210), -1)
            image[:] = cv2.addWeighted(overlay, 0.35, image, 0.65, 0)

        for peak in peaks:
            x, y = point(peak["frame_num"], peak["score"])
            cv2.circle(image, (x, y), 4, (0, 80, 255), -1)

        cv2.putText(image, "Diff curve", (margin_left, height - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (40, 40, 40), 1, cv2.LINE_AA)
        cv2.putText(image, "orange=peaks green=paired short segment blue=threshold", (margin_left + 150, height - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (70, 70, 70), 1, cv2.LINE_AA)
        cv2.imwrite(str(path), image)

    def _time_tick_step(self, duration_seconds: float) -> float:
        if duration_seconds <= 10:
            return 1.0
        if duration_seconds <= 30:
            return 2.0
        if duration_seconds <= 120:
            return 10.0
        if duration_seconds <= 600:
            return 30.0
        return 60.0

    def _format_seconds(self, seconds: float) -> str:
        total = int(round(seconds))
        minutes = total // 60
        secs = total % 60
        if minutes:
            return f"{minutes}:{secs:02d}"
        return f"{secs}s"

    def _detect_pyscenedetect_short_scenes(
        self,
        video_path: Path,
        output_dir: Path,
        max_spikes: int,
    ) -> list[dict]:
        try:
            from scenedetect import ContentDetector, detect
        except Exception:
            return []

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        short_segment_max_frames = settings.video_short_segment_max_frames
        if short_segment_max_frames <= 0:
            short_segment_max_frames = max(1, int(fps * settings.video_short_segment_max_seconds))

        try:
            scenes = detect(
                str(video_path),
                ContentDetector(
                    threshold=settings.pyscenedetect_threshold,
                    min_scene_len=settings.pyscenedetect_min_scene_len,
                ),
                show_progress=False,
            )
        except Exception:
            cap.release()
            return []

        candidates = []
        for start_time, end_time in scenes:
            start_frame = int(start_time.get_frames())
            end_frame = int(end_time.get_frames())
            duration_frames = max(1, end_frame - start_frame)
            if duration_frames > short_segment_max_frames:
                continue

            middle_frame = start_frame + duration_frames // 2
            cap.set(cv2.CAP_PROP_POS_FRAMES, middle_frame)
            ok, frame = cap.read()
            if not ok:
                continue

            candidates.append({
                "frame_num": middle_frame,
                "frame": frame.copy(),
                "score": float(short_segment_max_frames - duration_frames + 1),
                "segment_start_frame": start_frame,
                "segment_end_frame": end_frame,
                "segment_duration": duration_frames / fps,
                "detection_mode": "pyscenedetect_short_scene",
            })

        cap.release()

        candidates.sort(key=lambda item: item["score"], reverse=True)
        if max_spikes > 0:
            candidates = candidates[:max_spikes]
        candidates.sort(key=lambda item: item["frame_num"])

        results = []
        for idx, candidate in enumerate(candidates):
            path = output_dir / f"scene_{idx:02d}.jpg"
            cv2.imwrite(str(path), candidate["frame"])
            results.append({
                "index": idx,
                "path": str(path),
                "timestamp": candidate["frame_num"] / fps,
                "frame_number": candidate["frame_num"],
                "score": candidate["score"],
                "segment_start_frame": candidate["segment_start_frame"],
                "segment_end_frame": candidate["segment_end_frame"],
                "segment_duration": candidate["segment_duration"],
                "detection_mode": candidate["detection_mode"],
                "kind": "flash_spike",
            })
        return results

    def _gray_small(self, frame, target_width: int = 320):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape[:2]
        if width > target_width:
            scale = target_width / width
            gray = cv2.resize(gray, (target_width, max(1, int(height * scale))))
        return gray

    def _diff_scores(self, gray1, gray2, grid: int = 4) -> tuple[float, float]:
        delta = cv2.absdiff(gray1, gray2)
        global_score = float(np.mean(delta))
        height, width = delta.shape[:2]
        block_score = global_score
        cell_h = max(1, height // grid)
        cell_w = max(1, width // grid)

        for row in range(grid):
            y1 = row * cell_h
            y2 = height if row == grid - 1 else min(height, (row + 1) * cell_h)
            for col in range(grid):
                x1 = col * cell_w
                x2 = width if col == grid - 1 else min(width, (col + 1) * cell_w)
                block = delta[y1:y2, x1:x2]
                if block.size:
                    block_score = max(block_score, float(np.mean(block)))
        return global_score, block_score

    def _read_frame(self, video_path: Path, frame_num: int):
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ok, frame = cap.read()
        cap.release()
        return frame if ok else None

    def _diff(self, frame1, frame2) -> float:
        if frame1 is None or frame2 is None:
            return 0.0
        gray1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY)
        return float(np.mean(cv2.absdiff(gray1, gray2)))
