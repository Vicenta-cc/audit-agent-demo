from __future__ import annotations

from collections import Counter, defaultdict


SOURCE_GROUPS = {
    "keyword": "text",
    "text": "text",
    "title": "text",
    "desc": "text",
    "ocr": "ocr",
    "asr": "asr",
    "audio": "asr",
    "video_audio": "asr",
    "comment": "comment",
    "vision": "vision",
    "image": "vision",
    "frame": "vision",
    "video": "vision",
}

GROUP_META = {
    "text": {"label": "文本证据", "icon": "file-text"},
    "ocr": {"label": "画面文字", "icon": "image"},
    "asr": {"label": "音频证据", "icon": "mic"},
    "comment": {"label": "评论证据", "icon": "message-square"},
    "vision": {"label": "视觉证据", "icon": "scan-eye"},
}

RISK_LIBRARY_LABELS = {
    "prohibited": "违禁引流",
    "soft": "软色情",
    "terror": "暴恐",
    "drug": "涉毒",
    "gambling": "赌博博彩",
    "fraud": "诈骗",
    "hate": "仇恨歧视",
}

RISK_LIBRARY_IDS = {
    "违规引流": "prohibited",
    "违禁引流": "prohibited",
    "非法交易入口": "prohibited",
    "管制物品": "prohibited",
    "灰产服务": "prohibited",
    "绕平台交易": "prohibited",
    "软色情": "soft",
    "色情低俗": "soft",
    "隐私部位暴露": "soft",
    "性暗示动作": "soft",
    "色情资源导流": "soft",
    "低俗互动": "soft",
    "暴恐": "terror",
    "暴恐极端": "terror",
    "暴恐极端风险": "terror",
    "涉毒": "drug",
    "赌博": "gambling",
    "赌博博彩": "gambling",
    "诈骗": "fraud",
    "仇恨": "hate",
    "仇恨歧视": "hate",
}

RISK_LIBRARY_ALIASES = (
    ("prohibited", ("引流", "导流", "私域", "绕平台", "联系方式", "私信", "加群", "二维码", "外链", "非法交易", "交易入口", "管制物品", "灰产")),
    ("soft", ("软色情", "色情", "低俗", "性暗示", "露肤", "私密", "穿着", "身体", "凸显", "擦边", "泳装", "裸露", "暴露", "隐私部位", "衣物")),
    ("terror", ("暴恐", "恐怖", "爆炸", "袭击", "武器", "行动号召", "组织招募")),
    ("drug", ("涉毒", "毒品", "吸毒", "贩毒", "违禁药", "吸食", "同城接头")),
    ("gambling", ("赌博", "博彩", "盘口", "投注", "上分", "提现", "赔率")),
    ("fraud", ("诈骗", "刷单", "返利", "虚假投资", "钓鱼", "冒充", "转账", "认证金")),
    ("hate", ("仇恨", "歧视", "排斥", "贬损", "群体攻击", "暴力煽动", "恶性谣言")),
)


def build_evidence_groups(result: dict) -> list[dict]:
    """Create reviewer-friendly evidence groups from a normalized audit result."""
    if not isinstance(result, dict):
        return []
    buckets: dict[str, list[dict]] = defaultdict(list)
    contributions: Counter[str] = Counter()
    rule_hits: dict[str, Counter[str]] = defaultdict(Counter)
    result_libraries = _risk_libraries_for_result(result)

    for item in result.get("score_breakdown") or []:
        if not isinstance(item, dict):
            continue
        source = _normalize_group_for_item(item)
        score = _as_int(item.get("score"))
        rule = str(item.get("rule") or item.get("label") or item.get("rule_id") or "评分规则命中")
        evidence = str(item.get("evidence") or item.get("summary") or rule)
        evidence_item = {
            "id": str(item.get("rule_id") or item.get("id") or f"{source}_{len(buckets[source]) + 1}"),
            "title": rule,
            "source": source,
            "rule": rule,
            "risk_contribution": score,
            "confidence": item.get("confidence") or "",
            "content": evidence,
            "context": item.get("context") or "",
            "position": item.get("position") or _source_position(source),
        }
        buckets[source].append(_decorate_item(evidence_item, item, result, source, result_libraries))
        contributions[source] += score
        rule_hits[source][rule] += 1

    for item in result.get("risk_evidence") or result.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        source = _normalize_group_for_item(item)
        rule = str(item.get("rule") or item.get("label") or item.get("type") or "风险证据")
        content = str(
            item.get("evidence")
            or item.get("text")
            or item.get("content")
            or item.get("summary")
            or rule
        )
        evidence_item = {
            "id": str(item.get("id") or f"{source}_{len(buckets[source]) + 1}"),
            "title": rule,
            "source": source,
            "rule": rule,
            "risk_contribution": _as_int(item.get("score") or item.get("risk_contribution")),
            "confidence": item.get("confidence") or "",
            "content": content,
            "context": item.get("context") or "",
            "position": item.get("position") or _source_position(source),
        }
        buckets[source].append(_decorate_item(evidence_item, item, result, source, result_libraries))
        rule_hits[source][rule] += 1

    for index, item in enumerate(result.get("risk_images") or [], start=1):
        if isinstance(item, dict):
            title = item.get("title") or item.get("label") or f"风险图片 {index}"
            content = item.get("summary") or item.get("reason") or item.get("path") or title
        else:
            title = f"风险图片 {index}"
            content = str(item)
        evidence_item = {
            "id": f"risk_image_{index}",
            "title": str(title),
            "source": "vision",
            "rule": "图片/视频画面命中",
            "risk_contribution": 0,
            "confidence": "",
            "content": str(content),
            "context": "",
            "position": "图片/视频画面",
        }
        buckets["vision"].append(_decorate_item(evidence_item, item if isinstance(item, dict) else {}, result, "vision", result_libraries))
        rule_hits["vision"]["图片/视频画面命中"] += 1

    for index, item in enumerate(result.get("risk_frames") or [], start=1):
        if isinstance(item, dict):
            title = item.get("title") or item.get("label") or f"关键帧 {index}"
            content = item.get("summary") or item.get("reason") or item.get("path") or title
        else:
            title = f"关键帧 {index}"
            content = str(item)
        evidence_item = {
            "id": f"risk_frame_{index}",
            "title": str(title),
            "source": "vision",
            "rule": "关键帧命中",
            "risk_contribution": 0,
            "confidence": "",
            "content": str(content),
            "context": "",
            "position": "视频关键帧",
        }
        buckets["vision"].append(_decorate_item(evidence_item, item if isinstance(item, dict) else {}, result, "vision", result_libraries))
        rule_hits["vision"]["关键帧命中"] += 1

    groups = []
    for source in ("text", "ocr", "asr", "comment", "vision"):
        items = buckets.get(source) or []
        if not items:
            continue
        count = len(items)
        contribution = int(contributions[source] or sum(_as_int(item.get("risk_contribution")) for item in items))
        hits = [
            {"rule": rule, "count": count}
            for rule, count in rule_hits[source].most_common(5)
        ]
        libraries = _group_libraries(items, result_libraries)
        groups.append({
            "id": source,
            "type": source,
            "label": GROUP_META[source]["label"],
            "icon": GROUP_META[source]["icon"],
            "count": count,
            "hit_summary": hits,
            "confidence": _confidence_label(items, contribution),
            "risk_contribution": contribution,
            "risk_libraries": libraries,
            "preview_limit": 3,
            "items": items,
        })
    return groups


def _normalize_group_for_item(item: dict) -> str:
    source = str(item.get("source") or item.get("type") or item.get("modality") or "text").lower().strip()
    rule = str(item.get("rule") or item.get("label") or item.get("rule_id") or item.get("reason") or "").lower()
    text = f"{source} {rule}"
    if source.startswith("comment") or "评论" in rule or "弹幕" in rule:
        return "comment"
    if "video_audio" in source or "audio" in source or "asr" in text or "语音" in rule or "口播" in rule:
        return "asr"
    if "ocr" in text or "字幕" in rule or "画面文字" in rule:
        return "ocr"
    if source.startswith("image") or source.startswith("video_frame") or source.startswith("frame"):
        if "视觉" in rule or "关键帧" in rule or "画面特征" in rule:
            return "vision"
        if "文本" in rule or "语义" in rule or _looks_textual(str(item.get("evidence") or item.get("text") or "")):
            return "ocr"
        return "vision"
    if "视觉" in rule or "关键帧" in rule or "画面" in rule or "图片" in rule or "视频" in rule:
        return "vision"
    return _normalize_group(source.split(":", 1)[0])


def _normalize_group(source) -> str:
    text = str(source or "text").lower().strip()
    return SOURCE_GROUPS.get(text, text if text in GROUP_META else "text")


def _source_position(source: str) -> str:
    return {
        "text": "标题/正文",
        "ocr": "画面文字",
        "asr": "语音转写",
        "comment": "评论/弹幕",
        "vision": "图片/视频画面",
    }.get(source, "内容上下文")


def _decorate_item(item: dict, raw: dict, result: dict, source: str, result_libraries: list[dict]) -> dict:
    library = _risk_library_for_item(raw, result_libraries)
    if library:
        item["risk_library_id"] = library["id"]
        item["risk_library_label"] = library["label"]
    item["evidence_type"] = _evidence_type(source, item.get("position"), item.get("rule"))
    item["hit_explanation"] = str(raw.get("reason") or raw.get("explanation") or item.get("rule") or "")
    item["is_supplementary"] = _as_int(item.get("risk_contribution")) <= 0
    for key in (
        "ocr_text",
        "ocr_text_zh",
        "text",
        "text_zh",
        "source_text_dolphin",
        "source_text_mms",
        "translation_zh",
        "language",
        "start",
        "end",
        "timestamp",
        "asset_rel",
        "frame_asset_rel",
        "visual_summary",
    ):
        value = raw.get(key)
        if value not in (None, "", [], {}):
            item[key] = value
    item.update(_source_details(result, raw.get("source") or item.get("source"), source))
    return item


def _risk_libraries_for_result(result: dict) -> list[dict]:
    values = []
    for value in [result.get("primary_risk"), result.get("prompt_category"), *(result.get("categories") or [])]:
        library = _normalize_risk_library(value)
        if library and library not in values:
            values.append(library)
    return values


def _risk_library_for_item(item: dict, fallback: list[dict]) -> dict:
    for key in ("risk_library_id", "library_id", "category_id", "prompt_category"):
        library = _normalize_risk_library(item.get(key))
        if library:
            return library
    for key in ("risk_library_label", "risk_type", "category", "primary_risk"):
        library = _normalize_risk_library(item.get(key))
        if library:
            return library
    rule_id = str(item.get("rule_id") or item.get("id") or "")
    if "_" in rule_id:
        library = _normalize_risk_library(rule_id.split("_", 1)[0])
        if library:
            return library
    return fallback[0] if fallback else {}


def _normalize_risk_library(value) -> dict:
    text = str(value or "").strip()
    if not text:
        return {}
    library_id = RISK_LIBRARY_IDS.get(text) or (text if text in RISK_LIBRARY_LABELS else "")
    if not library_id:
        for candidate, aliases in RISK_LIBRARY_ALIASES:
            if any(alias in text for alias in aliases):
                library_id = candidate
                break
    if not library_id:
        return {}
    label = RISK_LIBRARY_LABELS.get(library_id)
    if not label:
        return {}
    return {"id": library_id, "label": label}


def _group_libraries(items: list[dict], fallback: list[dict]) -> list[dict]:
    values = []
    seen = set()
    for item in items:
        library = _normalize_risk_library(item.get("risk_library_id") or item.get("risk_library_label"))
        if not library:
            continue
        key = library["id"] or library["label"]
        if key not in seen:
            seen.add(key)
            values.append(library)
    if values:
        return values
    return fallback


def _evidence_type(source: str, position, rule) -> str:
    label = {
        "text": "文本命中",
        "ocr": "OCR命中",
        "asr": "ASR命中",
        "comment": "评论命中",
        "vision": "视觉命中",
    }.get(source, "证据命中")
    position_text = str(position or "")
    rule_text = str(rule or "")
    if source == "text":
        if "标题" in position_text or "标题" in rule_text:
            return "标题命中"
        if "正文" in position_text or "正文" in rule_text:
            return "正文命中"
    return label


def _source_details(result: dict, source, group: str) -> dict:
    index = result.get("evidence_index") if isinstance(result.get("evidence_index"), dict) else {}
    if not index:
        return {}
    if group == "ocr":
        item = _match_by_source_or_timestamp(index.get("ocr_items") or [], source)
        frame = _match_by_source_or_timestamp(index.get("timeline_frames") or [], source)
        if item or frame:
            return {
                "ocr_text": item.get("text") or "",
                "ocr_text_zh": item.get("text_zh") or "",
                "language": item.get("language") or "",
                "timestamp": item.get("timestamp"),
                "asset_rel": frame.get("asset_rel") or "",
                "frame_id": frame.get("frame_id") or "",
            }
    if group == "asr":
        item = _match_by_source_or_timestamp(index.get("asr_segments") or [], source)
        if item:
            return {
                "source_text_dolphin": item.get("source_text_dolphin") or item.get("text") or "",
                "source_text_mms": item.get("source_text_mms") or "",
                "translation_zh": item.get("translation_zh") or item.get("text_zh") or "",
                "language": item.get("language") or "",
                "start": item.get("start"),
                "end": item.get("end"),
            }
    if group == "vision":
        frame = _match_by_source_or_timestamp(index.get("timeline_frames") or [], source)
        if frame:
            return {
                "asset_rel": frame.get("asset_rel") or "",
                "frame_id": frame.get("frame_id") or "",
                "timestamp": frame.get("timestamp"),
            }
    return {}


def _match_by_source_or_timestamp(items: list[dict], source) -> dict:
    text = str(source or "")
    for item in items:
        if str(item.get("source") or "") == text:
            return item
    timestamp = _source_timestamp(text)
    if timestamp is None:
        return {}
    for item in items:
        value = item.get("timestamp")
        if value is None:
            value = item.get("start")
        try:
            if abs(float(value) - timestamp) < 0.25:
                return item
        except (TypeError, ValueError):
            continue
    return {}


def _source_timestamp(source: str) -> float | None:
    if ":" not in source:
        return None
    tail = source.rsplit(":", 1)[-1]
    if "-" in tail:
        tail = tail.split("-", 1)[0]
    try:
        return float(tail)
    except ValueError:
        return None


def _looks_textual(value: str) -> bool:
    if not value:
        return False
    return any(token in value for token in ("“", "”", "'", "私信", "联系", "主页", "二维码", "评论", "文字", "OCR"))


def _confidence_label(items: list[dict], contribution: int) -> str:
    if contribution >= 30 or len(items) >= 5:
        return "高置信"
    if contribution >= 15 or len(items) >= 2:
        return "待确认"
    return "低置信"


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
