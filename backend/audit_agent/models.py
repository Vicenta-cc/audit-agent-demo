from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TextEvidence:
    source: str
    text: str
    reason: str = ""
    start: str | None = None
    end: str | None = None


@dataclass
class ImageAnalysis:
    source: str
    path_or_url: str
    ocr_text: str = ""
    visual_summary: str = ""
    risk_items: list[dict] = field(default_factory=list)


@dataclass
class VideoAnalysis:
    source: str
    path_or_url: str
    transcript_text: str = ""
    transcript_segments: list[dict] = field(default_factory=list)
    frame_analyses: list[dict] = field(default_factory=list)


@dataclass
class AuditSubject:
    platform: str
    note_id: str
    url: str
    title: str
    desc: str
    author: dict
    image_urls: list[str]
    video_urls: list[str]
    comments: list[dict]
    local_image_paths: list[str] = field(default_factory=list)
    local_video_paths: list[str] = field(default_factory=list)


@dataclass
class AuditResult:
    note_id: str
    url: str
    title: str
    summary: str
    decision: str
    risk_level: str
    categories: list[str]
    evidence: list[dict]
    raw: dict
