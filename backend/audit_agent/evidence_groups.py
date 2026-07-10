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
    if isinstance(result.get("evidence_items"), list) and result.get("evidence_items"):
        return _build_groups_from_evidence_items(result, result_libraries)
    seen_items: set[tuple[str, str, str]] = set()

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
        decorated = _decorate_item(evidence_item, item, result, source, result_libraries)
        if _append_unique(buckets, source, decorated, seen_items):
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
        decorated = _decorate_item(evidence_item, item, result, source, result_libraries)
        if _append_unique(buckets, source, decorated, seen_items):
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
        decorated = _decorate_item(evidence_item, item if isinstance(item, dict) else {}, result, "vision", result_libraries)
        if _append_unique(buckets, "vision", decorated, seen_items):
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
        decorated = _decorate_item(evidence_item, item if isinstance(item, dict) else {}, result, "vision", result_libraries)
        if _append_unique(buckets, "vision", decorated, seen_items):
            rule_hits["vision"]["关键帧命中"] += 1

    return _groups_from_buckets(buckets, contributions, rule_hits, result_libraries)


def _build_groups_from_evidence_items(result: dict, result_libraries: list[dict]) -> list[dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    contributions: Counter[str] = Counter()
    rule_hits: dict[str, Counter[str]] = defaultdict(Counter)
    score_by_evidence: dict[str, int] = defaultdict(int)
    rules_by_evidence: dict[str, list[dict]] = defaultdict(list)
    for row in result.get("score_breakdown") or []:
        if not isinstance(row, dict):
            continue
        score = _as_int(row.get("score"))
        rule = str(row.get("rule") or row.get("label") or row.get("rule_id") or "评分规则命中")
        evidence_ids = row.get("evidence_ids") if isinstance(row.get("evidence_ids"), list) else []
        if not evidence_ids:
            continue
        for evidence_id in evidence_ids:
            key = str(evidence_id)
            score_by_evidence[key] += score
            rules_by_evidence[key].append(row)
    seen_items: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(result.get("evidence_items") or [], start=1):
        if not isinstance(raw, dict):
            continue
        evidence_id = str(raw.get("evidence_id") or raw.get("id") or f"ev_{index:03d}")
        source = _normalize_group(raw.get("primary_modality") or raw.get("modality") or raw.get("source"))
        rule_rows = rules_by_evidence.get(evidence_id) or []
        rule_names = [
            str(row.get("rule") or row.get("label") or row.get("rule_id") or "")
            for row in rule_rows
            if str(row.get("rule") or row.get("label") or row.get("rule_id") or "")
        ]
        rule = " / ".join(rule_names) or str(raw.get("rule_name") or raw.get("risk_type") or "风险证据")
        content = _original_content(raw, source)
        evidence_item = {
            "id": evidence_id,
            "title": rule,
            "source": source,
            "rule": rule,
            "risk_contribution": score_by_evidence.get(evidence_id, 0),
            "confidence": raw.get("confidence") or "",
            "content": content,
            "context": raw.get("visual_context") or raw.get("context") or "",
            "position": raw.get("source_label") or _position_from_raw_source(raw.get("source")) or _source_position(source),
            "score_rule_ids": [str(row.get("rule_id") or row.get("id") or "") for row in rule_rows if row.get("rule_id") or row.get("id")],
            "score_rules": rule_rows,
        }
        decorated = _decorate_item(evidence_item, raw, result, source, result_libraries)
        if _append_unique(buckets, source, decorated, seen_items):
            contributions[source] += _as_int(decorated.get("risk_contribution"))
            for name in rule_names or [rule]:
                rule_hits[source][name] += 1
    return _groups_from_buckets(buckets, contributions, rule_hits, result_libraries)


def _groups_from_buckets(
    buckets: dict[str, list[dict]],
    contributions: Counter[str],
    rule_hits: dict[str, Counter[str]],
    result_libraries: list[dict],
) -> list[dict]:
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


def _append_unique(
    buckets: dict[str, list[dict]],
    source: str,
    item: dict,
    seen_items: set[tuple[str, str, str]],
) -> bool:
    key = (
        source,
        str(item.get("position") or item.get("source") or ""),
        _signature_text(item.get("content") or item.get("text") or item.get("ocr_text") or item.get("reason") or ""),
    )
    if key in seen_items:
        return False
    seen_items.add(key)
    buckets[source].append(item)
    return True


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
        "visual_context",
        "visual_elements",
        "features",
        "evidence_risk_level",
        "supporting_modalities",
        "primary_modality",
        "comment_id",
        "nickname",
        "source_label",
        "score_rule_ids",
        "score_rules",
        "frame_id",
        "ocr_engine",
        "ocr_language",
        "ocr_confidence",
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
        if position_text == "标题" or ("标题" in rule_text and "正文" not in rule_text):
            return "标题命中"
        if position_text == "正文" or ("正文" in rule_text and "标题" not in rule_text):
            return "正文命中"
    return label


def _original_content(item: dict, source: str) -> str:
    if source == "ocr":
        return str(item.get("ocr_text_zh") or item.get("ocr_text") or item.get("text") or "")
    if source == "asr":
        return str(
            item.get("translation_zh")
            or item.get("text_zh")
            or item.get("source_text_dolphin")
            or item.get("text")
            or ""
        )
    if source == "comment":
        return str(item.get("text") or item.get("content") or "")
    if source == "vision":
        elements = item.get("visual_elements") if isinstance(item.get("visual_elements"), list) else []
        if elements:
            return "、".join(str(value) for value in elements if str(value).strip())
        return str(item.get("visual_context") or item.get("visual_summary") or item.get("text") or "")
    return str(item.get("text") or item.get("content") or "")


def _position_from_raw_source(source) -> str:
    text = str(source or "")
    if text == "title":
        return "标题"
    if text == "desc":
        return "正文"
    if text.startswith("comment"):
        return "评论/弹幕"
    if text.startswith("image"):
        return "图片"
    if text.startswith("video_audio"):
        return "语音转写"
    if text.startswith("video_frame") or text.startswith("video:"):
        return "视频关键帧"
    return ""


def _signature_text(value) -> str:
    return "".join(str(value or "").split()).lower()[:120]


def _source_details(result: dict, source, group: str) -> dict:
    index = result.get("evidence_index") if isinstance(result.get("evidence_index"), dict) else {}
    if not index:
        return {}
    if group == "ocr":
        image = _match_image_unit(index.get("image_units") or [], source)
        item = _match_by_source_or_timestamp(index.get("ocr_items") or [], source)
        frame = _match_by_source_or_timestamp(index.get("timeline_frames") or [], source)
        if image:
            return {
                "ocr_text": image.get("ocr_text") or "",
                "ocr_text_zh": image.get("ocr_text_zh") or "",
                "language": image.get("ocr_language") or "",
                "asset_rel": image.get("asset_rel") or "",
            }
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
        image = _match_image_unit(index.get("image_units") or [], source)
        if image:
            return {
                "asset_rel": image.get("asset_rel") or "",
                "visual_summary": image.get("visual_summary") or "",
            }
        frame = _match_by_source_or_timestamp(index.get("timeline_frames") or [], source)
        if frame:
            return {
                "asset_rel": frame.get("asset_rel") or "",
                "frame_id": frame.get("frame_id") or "",
                "timestamp": frame.get("timestamp"),
            }
    return {}


def _match_image_unit(items: list[dict], source) -> dict:
    text = str(source or "")
    for item in items:
        if str(item.get("source") or item.get("evidence_id") or "") == text:
            return item
        index = item.get("index")
        if index is not None and text == f"image:{index}":
            return item
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
