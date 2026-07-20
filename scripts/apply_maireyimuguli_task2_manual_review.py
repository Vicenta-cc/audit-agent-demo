#!/usr/bin/env python3
"""Apply the user-approved manual review for task 8bc179209e1e.

The script is intentionally data-driven and idempotent. Before the first write it
backs up the SQLite index, all 27 result JSON files, and the corrected OCR/ASR
asset indexes so the pre-review state remains recoverable.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


JOB_ID = "8bc179209e1e"
REVIEW_STATUS = "manual_corrected"
REVIEWER = "Codex（按用户人工标注）"
REVIEW_NOTE = "已按用户提供的人工翻译、危险等级和命中解释修正。"


RECORDS = {
    "7661149016680670129": ("none", "媒人介绍多民族征婚对象"),
    "7660111458273430521": ("none", "女子户外烧烤日常记录"),
    "7658155347656743353": ("none", "婚介服务介绍新疆女孩相亲"),
    "7656310250287868785": ("none", "女子征婚生活记录"),
    "7655528867624494586": ("none", "女子分享相亲经历"),
    "7651868243075038129": ("none", "视频倡导女性自立"),
    "7653718815465693169": ("none", "彩礼债务与择偶讨论"),
    "7649605705973163001": ("low", "女子与戴面具者互动"),
    "7648512254963153658": ("medium", "婚介视频引地域攻击评论"),
    "7648168795894870257": ("none", "博主吐槽婚介营销骚扰"),
    "7647142350804884410": ("medium", "生育话题引民族刻板印象评论"),
    "7646359699722730169": ("none", "征婚服务宣传"),
    "7641834784402078129": ("low", "夫妻珠宝店纪念日常分享"),
    "7639620479205456378": ("low", "阿克苏阿瓦提旅行记录"),
    "7636324454851861113": ("low", "女子分享夫妻争吵日常"),
    "7634070051762535601": ("none", "儿童合影与家庭话题"),
    "7631527301838613157": ("none", "维吾尔族女子介绍婚介服务"),
    "7629185084818112689": ("low", "女子户外散步记录"),
    "7624024569484941553": ("none", "家庭观念分享"),
    "7623259311515222011": ("high", "新疆足浴店评论区争议"),
    "7620346782509758193": ("high", "婚介视频评论区争议言论"),
    "7617122827347724281": ("none", "婚介女性回应误解倡导尊重"),
    "7616250205041952689": ("none", "媒人分享工作遭遇"),
    "7606993736912603761": ("none", "跨民族家庭春节日常"),
    "7587683723097845690": ("none", "雪地舞蹈与美好祝愿"),
    "7581079984975916465": ("none", "媒人介绍定向婚介服务"),
    "7577792412267153637": ("none", "夫妻调侃婚恋与婚介"),
}


COMMENT_TRANSLATIONS = {
    "7661556310471951141": "难道叫玛丽亚姆古丽的女孩，找的老公都长得这么黑吗？",
    "7655302269974283027": "穿黄外套的，你先去把牙镶好再来吧，嘴巴都快瘪成没牙老头/老太太了。",
    "7649651373013107462": "哎呀你们这些造假的人（小骗子），这胸怎么这么丰满啊",
    "7648861723563066149": "你来和田干嘛？是来把这座城市搞得乌烟瘴气的吗？",
    "7648422925260309300": "我也在看、在听其他那些嫁给汉族人的女人的情况。在这网络世界里，大多数嫁给汉族的女人，我纳闷为什么她们都生不出孩子呢？别说你了，除了你之外，其他嫁给‘同志’（指汉族人）的女人也生不出孩子，我在网上看到听到，百分之五这样嫁给‘同志’的女人都生不出孩子。",
    "7644185995956454186": "您打字稍微轻一点吧，别（把手机/键盘）敲坏了",
    "7631533407759139622": "这口红的颜色叫什么呀！哇哦，宝贝，我真想亲吻你的嘴巴",
    "7629259904641319717": "这个女人长得真丑",
    "7624093210688996096": "男人不是那种你吆喝一声就要干活的驴。如果你不尊重他，把他当驴对待，那他也会把你当驴看，你懂吗？",
    "7624058847627559722": "你说得对，还有那种饭做好了，却非要挑刺说没熟的‘虱子眼’（阴险挑剔的人）",
    "7624134065939514171": "嘴里含着那脏东西不吐（下流性暗示），看你丈夫那烂样，一家子都是货色。",
    "7623287188390019876": "嘿，你个[极其恶毒的女性侮辱词]，把你那破嘴闭上，别在那儿哭丧了。",
    "7620471937974027067": "你这个骚货/荡妇，长得倒是挺大只。你那男人（丈夫）也是个没割包皮的烂货",
    "7617823622681674511": "哎，大家别没事闲着就去乱骂人。与其跑到大街上（公开场合）丢人现眼、去当第三者破坏别人的家庭，倒不如就像这些女孩介绍的对象那样，哪怕是汉族人，找个男人安安稳稳地过日子，这不更好吗？",
    "7658387761137074944": "既然一直找不到维吾尔族姑娘，那就帮忙介绍个像这样的姑娘当对象吧",
    "7651928373543551793": "像我们这样没结婚的，就继续单着呗。那些拿了彩礼就把男人当冤大头耍的婆娘，连我们的一根头发都比不上！",
    "7651927314242814769": "现在那种靠骗男人钱才肯嫁人的女人多得是",
    "7648915672194794275": "连我一天都有好几个人追着找（搭讪），您要是还没遇到合适的，干脆我给您介绍个汉族对象得了",
    "7634425259404559154": "您的孩子是跟前夫生的，还是跟现在的汉族老公生的？",
    "7616417752585143098": "伽师县那边的人也不怎么样（不怎么热情/大方），上次我和老公路过伽师，在那里住了一晚，真是被折腾（刁难）坏了，素质差的人太多了 ...",
    "7607648156499854129": "您儿子太可爱了，长得真像他爸爸。他对待孩子就像对待亲生骨肉一样，倾注了满满的关爱，照顾得真好。您丈夫真是心胸宽广，太难得了。要是换个维吾尔族男人，绝对不肯这么尽心尽力地去养一个非亲生的孩子。我也有个孩子，可我丈夫对孩子特别排斥、当外人看，我都没法把孩子带在自己身边。祝你们永远幸福，生活美满，开开心心。",
}


COMMENT_RISKS = {
    "7649651373013107462": ("low", "低俗骚扰", "对女博主身体进行露骨评论，带有性骚扰和不尊重意味"),
    "7648861723563066149": ("medium", "地域攻击", "以外来者身份贬损当事人“污染/搞得乌烟瘴气”，构成地域攻击"),
    "7648422925260309300": ("medium", "民族刻板印象", "将嫁给汉族与不孕泛化关联，构成民族刻板印象"),
    "7645326157534085922": ("low", "群体贬损", "以维吾尔族身份泛化“只想要黄金”，带有群体贬损"),
    "7641382976681984774": ("low", "低俗骚扰", "对女性身体进行露骨物化评论，属于不当发言"),
    "7636687718320472869": ("low", "人身攻击", "使用严重侮辱性词汇直接攻击博主"),
    "7629259904641319717": ("low", "人身攻击", "直接贬损女性外貌，构成人身攻击"),
    "7624134065939514171": ("high", "低俗辱骂", "包含下流性暗示并辱骂当事人及其家人"),
    "7623287188390019876": ("high", "恶毒人身攻击", "使用极其恶毒的女性侮辱词并进行持续谩骂"),
    "7620471937974027067": ("high", "性侮辱", "使用“骚货/荡妇”等性侮辱并辱骂其丈夫"),
}


CONTENT_SUMMARIES = {
    "7661149016680670129": "视频发布婚介信息，面向19至38岁真心想结婚的单身女性，可按意愿介绍维吾尔族、回族或汉族对象。",
    "7660111458273430521": "视频记录女子在户外吃烧烤、与镜头互动的日常生活场景。",
    "7658155347656743353": "视频展示婚介相亲服务，为有结婚意愿的新疆单身女性介绍合适对象。",
    "7656310250287868785": "女子介绍自己的婚介规则，说明为女性免费登记、向男性收取介绍和登记费用。",
    "7655528867624494586": "女子在车内分享婚介沟通经历，提醒咨询者直接说明结婚和找对象需求，并介绍收费方式。",
    "7651868243075038129": "视频围绕女性自立和生活选择展开，劝诫女性依靠劳动改善生活。",
    "7653718815465693169": "视频讨论择偶、彩礼和婚礼债务等现实婚恋问题。",
    "7649605705973163001": "视频记录女子与一名佩戴红色面具的人互动，整体为娱乐或恶作剧场景。",
    "7648512254963153658": "女子在和田介绍婚介登记和对象匹配服务，并说明女方免费、男方支付介绍费的规则。",
    "7648168795894870257": "博主讲述朋友圈及私信中的婚介营销骚扰，表达对频繁搭讪和无效推销的不满。",
    "7647142350804884410": "女子分享自己的生育经历和身体情况，讲述婚后备孕与生育困难。",
    "7646359699722730169": "视频宣传阿克苏婚介服务，介绍征婚流程、隐私保护和面向各地单身男女的对象匹配服务。",
    "7641834784402078129": "视频分享夫妻经营珠宝店的纪念日常，并配文鼓励积极生活、珍惜当下。",
    "7639620479205456378": "视频记录阿克苏阿瓦提多浪部落的旅行见闻和当地风光。",
    "7636324454851861113": "女子分享夫妻争吵后的家庭日常和自己的感受。",
    "7634070051762535601": "视频展示儿童合影和家庭生活片段，围绕孩子及家庭关系展开。",
    "7631527301838613157": "女子解释自己的婚介工作，为有结婚意愿的单身男女介绍合适对象。",
    "7629185084818112689": "视频记录女子在户外散步、面对镜头互动的日常生活场景。",
    "7624024569484941553": "视频分享夫妻相处和家庭观念，强调相互尊重、不要把伴侣当作使唤对象。",
    "7623259311515222011": "视频展示新疆一家足浴店的店内环境、服务过程和日常经营场景。",
    "7620346782509758193": "女子记录驾照考试通过后的喜悦，并与丈夫讨论庆祝、买车和接送安排。",
    "7617122827347724281": "女子讲述从事婚介工作的经历，回应外界误解并倡导相互尊重。",
    "7616250205041952689": "女子分享从事婚介工作时遇到的咨询和沟通经历。",
    "7606993736912603761": "视频记录跨民族家庭春节期间的亲子互动和温馨生活日常。",
    "7587683723097845690": "视频展示雪地舞蹈场景，并配文表达对生活顺利和美好的祝愿。",
    "7581079984975916465": "女子说明自己的婚介服务和责任范围，面向20至35岁的汉族小伙发布征婚介绍信息。",
    "7577792412267153637": "视频记录夫妻间关于婚恋、相亲和婚介话题的日常调侃。",
}


OCR_CONTENT_ID = "7661149016680670129"
OCR_TRANSLATION = "我一直在喀什从事相亲牵线工作。19岁到38岁之间、真心想结婚的单身女孩们，不用反复私信问条件了。无论您想嫁给维吾尔族、回族还是汉族小伙，我都会为您量身介绍合您心意的对象。"
ASR_CONTENT_ID = "7581079984975916465"
ASR_TRANSLATION = "我自己介绍的（对象/婚事），我自己做主负责。此外，只找20岁到35岁之间的汉族小伙子结婚。"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def back_up(root: Path, db_path: Path, result_paths: list[Path], asset_paths: list[Path]) -> Path:
    backup_root = root / "artifacts" / f"manual-review-{JOB_ID}-20260719-before"
    marker = backup_root / "manifest.json"
    if marker.exists():
        return backup_root

    backup_root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as source, sqlite3.connect(backup_root / db_path.name) as target:
        source.backup(target)

    copied: list[str] = []
    for source_path in [*result_paths, *asset_paths]:
        relative = source_path.relative_to(root)
        target_path = backup_root / relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        copied.append(str(relative))

    marker.write_text(
        json.dumps({"job_id": JOB_ID, "database": db_path.name, "files": copied}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return backup_root


def set_comment_safe(comment: dict[str, Any]) -> None:
    comment.update({
        "audit_status": "completed",
        "audit_source": "manual_review",
        "risk_score": 0,
        "risk_level": "none",
        "risk_library_id": "",
        "risk_library_label": "",
        "secondary_library_ids": [],
        "risk_type": "",
        "risk_basis": "",
        "exemption_basis": "人工复核：不构成风险",
        "evidence_quote": "",
    })


def comment_stats(comments: list[dict[str, Any]]) -> dict[str, int]:
    completed = [item for item in comments if item.get("audit_status") == "completed"]
    return {
        "total": len(comments),
        "completed": len(completed),
        "failed": len(comments) - len(completed),
        "translation_required": sum(
            item.get("translation_status") in {"completed", "failed"}
            for item in comments
            if item.get("translation_zh") or item.get("translation_error")
        ),
        "translation_completed": sum(item.get("translation_status") == "completed" for item in comments),
        "translation_failed": sum(item.get("translation_status") == "failed" for item in comments),
        "review_count": sum(int(item.get("risk_score") or 0) >= 40 for item in completed),
        "high_count": sum(item.get("risk_level") == "high" for item in completed),
        "max_score": max((int(item.get("risk_score") or 0) for item in completed), default=0),
    }


def evidence_from_comment(comment: dict[str, Any]) -> dict[str, Any]:
    comment_id = str(comment.get("comment_id") or "")
    return {
        "evidence_id": f"comment:{comment_id}",
        "source": f"comment:{comment_id}",
        "primary_modality": "comment",
        "comment_id": comment_id,
        "nickname": comment.get("nickname") or "",
        "text": comment.get("source_text") or comment.get("content") or "",
        "translation_zh": comment.get("translation_zh") or "",
        "risk_library_id": "manual",
        "risk_library_label": "人工复核风险",
        "secondary_library_ids": [],
        "risk_type": comment.get("risk_type") or "",
        "reason": comment.get("risk_basis") or "",
        "exemption_basis": "",
        "risk_score": int(comment.get("risk_score") or 0),
        "evidence_risk_level": comment.get("risk_level") or "none",
        "id": f"comment:{comment_id}",
        "modality": "comment",
        "source_label": "评论",
    }


def synchronize_comment_copies(node: Any, comments_by_id: dict[str, dict[str, Any]]) -> None:
    if isinstance(node, dict):
        comment_id = str(node.get("comment_id") or "")
        if not comment_id:
            source = str(node.get("source") or "")
            if source.startswith("comment:"):
                comment_id = source.removeprefix("comment:")
        source_comment = comments_by_id.get(comment_id)
        if source_comment:
            for key in (
                "translation_zh", "translation_status", "audit_status", "audit_source",
                "risk_score", "risk_level", "risk_library_id", "risk_library_label",
                "secondary_library_ids", "risk_type", "risk_basis", "exemption_basis",
                "evidence_quote",
            ):
                if key in node or key in {"translation_zh", "risk_score", "risk_level"}:
                    node[key] = source_comment.get(key, "")
            if "reason" in node:
                node["reason"] = source_comment.get("risk_basis") or ""
            if "evidence_risk_level" in node:
                node["evidence_risk_level"] = source_comment.get("risk_level") or "none"
        for value in node.values():
            synchronize_comment_copies(value, comments_by_id)
    elif isinstance(node, list):
        for value in node:
            synchronize_comment_copies(value, comments_by_id)


def clear_risk_metadata(node: Any) -> None:
    if isinstance(node, dict):
        for key in ("ocr_risks", "asr_risks", "visual_risks"):
            if key in node:
                node[key] = []
        if "segment_score" in node:
            node["segment_score"] = 0
        if "segment_level" in node:
            node["segment_level"] = "none"
        if "risk_score" in node:
            node["risk_score"] = 0
        if "evidence_risk_level" in node:
            node["evidence_risk_level"] = "none"
        if "risk_level" in node and any(key in node for key in ("frame_id", "segment_id", "risk_library_id")):
            node["risk_level"] = "none"
        for value in node.values():
            clear_risk_metadata(value)
    elif isinstance(node, list):
        for value in node:
            clear_risk_metadata(value)


def correct_ocr_translations(node: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(node, dict):
        if node.get("ocr_text_zh"):
            node["ocr_text_zh"] = OCR_TRANSLATION
        is_ocr_context = any("ocr" in part for part in path) or bool(node.get("frame_id"))
        if is_ocr_context and node.get("translation_zh"):
            node["translation_zh"] = OCR_TRANSLATION
        if is_ocr_context and node.get("text_zh"):
            node["text_zh"] = OCR_TRANSLATION
        if is_ocr_context and any(key in node for key in ("risk_library_id", "evidence_risk_level")):
            if "reason" in node:
                node["reason"] = "人工复核：OCR 翻译已修正，不构成风险"
            if "risk_basis" in node:
                node["risk_basis"] = ""
            if "exemption_basis" in node:
                node["exemption_basis"] = "人工复核：OCR 翻译已修正，不构成风险"
        for key, value in node.items():
            correct_ocr_translations(value, (*path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            correct_ocr_translations(value, (*path, str(index)))


def correct_asr_translation(node: Any) -> None:
    if isinstance(node, dict):
        start = node.get("start")
        end = node.get("end")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            if abs(float(start) - 146.48) < 0.02 and abs(float(end) - 166.534) < 0.02 and "translation_zh" in node:
                node["translation_zh"] = ASR_TRANSLATION
            elif abs(float(start) - 146.48) < 0.02 and abs(float(end) - 154.2) < 0.02 and "translation_zh" in node:
                node["translation_zh"] = ASR_TRANSLATION
            elif 154.19 <= float(start) <= 166.54 and float(end) <= 166.55 and "translation_zh" in node:
                node["translation_zh"] = ""
        for key in ("text_zh", "text"):
            value = node.get(key)
            marker = "我自己介绍的，我自己负责到底。另外，只有20到35岁的汉族小伙子想结婚的"
            if isinstance(value, str) and marker in value:
                node[key] = value[:value.index(marker)] + ASR_TRANSLATION
        for value in node.values():
            correct_asr_translation(value)
    elif isinstance(node, list):
        for value in node:
            correct_asr_translation(value)


def update_result(content_id: str, result: dict[str, Any], reviewed_at: str) -> dict[str, Any]:
    level, title = RECORDS[content_id]
    score = {"none": 0, "low": 40, "medium": 60, "high": 80}[level]
    decision = "pass" if level == "none" else "review"
    comments = result.get("comments") if isinstance(result.get("comments"), list) else []
    retained: list[dict[str, Any]] = []

    for comment in comments:
        if not isinstance(comment, dict):
            continue
        comment_id = str(comment.get("comment_id") or "")
        if comment_id in COMMENT_TRANSLATIONS:
            comment["translation_zh"] = COMMENT_TRANSLATIONS[comment_id]
            comment["translation_status"] = "completed"
            comment.pop("translation_error", None)
        set_comment_safe(comment)
        if comment_id in COMMENT_RISKS:
            evidence_level, risk_type, reason = COMMENT_RISKS[comment_id]
            evidence_score = {"low": 40, "medium": 60, "high": 80}[evidence_level]
            quote = comment.get("translation_zh") or comment.get("source_text") or comment.get("content") or ""
            comment.update({
                "risk_score": evidence_score,
                "risk_level": evidence_level,
                "risk_library_id": "manual",
                "risk_library_label": "人工复核风险",
                "risk_type": risk_type,
                "risk_basis": reason,
                "exemption_basis": "",
                "evidence_quote": quote,
            })
            retained.append(comment)

    if level == "none" and retained:
        raise RuntimeError(f"{content_id}: pass record unexpectedly retained evidence")
    if level != "none" and not retained:
        raise RuntimeError(f"{content_id}: risk record has no retained evidence")
    if retained and any(comment.get("risk_level") != level for comment in retained):
        raise RuntimeError(f"{content_id}: evidence level does not match record level")

    evidence_items = [evidence_from_comment(comment) for comment in retained]
    categories = list(dict.fromkeys(comment["risk_type"] for comment in retained))
    result.update({
        "content_title": title,
        "summary": CONTENT_SUMMARIES[content_id],
        "decision": decision,
        "risk_level": level,
        "risk_score": score,
        "primary_risk": categories[0] if categories else "",
        "categories": categories,
        "category_scores": [
            {"category": category, "score": score, "level": level}
            for category in categories
        ],
        "score_breakdown": [
            {
                "rule_id": f"manual_review_{index}",
                "rule": item["risk_type"],
                "category": item["risk_type"],
                "library_id": "manual",
                "modality": "comment",
                "source": "comment",
                "score": score if len(evidence_items) == 1 else score // len(evidence_items),
                "score_policy": "manual_review",
                "evidence_ids": [item["evidence_id"]],
            }
            for index, item in enumerate(evidence_items, 1)
        ],
        "risk_basis": "manual_review" if retained else "manual_review_no_risk",
        "evidence_items": evidence_items,
        "rule_matches": [],
        "evidence": [],
        "risk_evidence": [
            {
                "kind": "comment",
                "source": item["source"],
                "severity": item["evidence_risk_level"],
                "text": item["text"],
                "translation_zh": item["translation_zh"],
                "reason": item["reason"],
                "risk_library_id": "manual",
                "risk_library_label": "人工复核风险",
                "start": None,
                "end": None,
                "comment_id": item["comment_id"],
                "nickname": item["nickname"],
            }
            for item in evidence_items
        ],
        "risk_frames": [],
        "risk_images": [],
        "has_risk": bool(retained),
        "comment_audit_stats": comment_stats(comments),
        "review": {
            "status": REVIEW_STATUS,
            "note": REVIEW_NOTE,
            "reviewer": REVIEWER,
            "reviewed_at": reviewed_at,
        },
    })

    evidence_index = result.get("evidence_index")
    if isinstance(evidence_index, dict):
        comments_by_id = {
            str(comment.get("comment_id") or ""): comment
            for comment in comments
            if isinstance(comment, dict) and comment.get("comment_id")
        }
        synchronize_comment_copies(evidence_index, comments_by_id)
        evidence_index["final_evidence_refs"] = [item["evidence_id"] for item in evidence_items]

    if content_id == OCR_CONTENT_ID:
        correct_ocr_translations(result.get("evidence_index", {}))
        clear_risk_metadata(result.get("evidence_index", {}))
        correct_ocr_translations(result.get("video_results", []), ("video_results",))
        clear_risk_metadata(result.get("video_results", []))
    elif content_id == ASR_CONTENT_ID:
        correct_asr_translation(result.get("evidence_index", {}))
        clear_risk_metadata(result.get("evidence_index", {}))
        correct_asr_translation(result.get("video_results", []))
        clear_risk_metadata(result.get("video_results", []))

    return result


def update_asset(
    content_id: str,
    payload: dict[str, Any],
    comments_by_id: dict[str, dict[str, Any]],
    evidence_refs: list[str],
) -> dict[str, Any]:
    synchronize_comment_copies(payload, comments_by_id)
    if content_id == OCR_CONTENT_ID:
        correct_ocr_translations(payload)
    elif content_id == ASR_CONTENT_ID:
        correct_asr_translation(payload)
    clear_risk_metadata(payload)
    if "final_evidence_refs" in payload:
        payload["final_evidence_refs"] = evidence_refs
    # Clearing the original video/OCR risk fields must not erase retained manual comment risk.
    synchronize_comment_copies(payload, comments_by_id)
    return payload


def verify_rows(conn: sqlite3.Connection, records: dict[str, tuple[str, str]]) -> None:
    placeholders = ",".join("?" for _ in records)
    rows = conn.execute(
        f"SELECT note_id, decision, risk_level, evidence_count, result_json FROM audit_results "
        f"WHERE job_id = ? AND note_id IN ({placeholders})",
        (JOB_ID, *records),
    ).fetchall()
    if len(rows) != len(records):
        raise RuntimeError(f"expected {len(records)} rows, found {len(rows)}")
    for row in rows:
        content_id = str(row["note_id"])
        expected_level = records[content_id][0]
        expected_decision = "pass" if expected_level == "none" else "review"
        expected_evidence = sum(
            1
            for comment in json.loads(row["result_json"]).get("comments", [])
            if str(comment.get("comment_id") or "") in COMMENT_RISKS
        )
        if (row["decision"], row["risk_level"], row["evidence_count"]) != (
            expected_decision,
            expected_level,
            expected_evidence,
        ):
            raise RuntimeError(f"{content_id}: database verification failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--content-id",
        action="append",
        choices=RECORDS,
        dest="content_ids",
        help="Only update the selected content ID; repeat to update multiple records.",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    selected_records = {
        content_id: RECORDS[content_id]
        for content_id in (args.content_ids or RECORDS)
    }
    db_path = root / "data" / "audit_index.sqlite3"
    result_root = root / "outputs" / JOB_ID / "results"
    result_paths = [result_root / f"{content_id}.result.json" for content_id in selected_records]
    asset_paths = [
        root / "outputs" / JOB_ID / "assets" / content_id / "evidence_index.json"
        for content_id in selected_records
    ]
    if OCR_CONTENT_ID in selected_records:
        asset_paths.append(
            root / "outputs" / JOB_ID / "assets" / OCR_CONTENT_ID / "videos" / "frames_00" / "timeline_index.json"
        )
    missing = [path for path in [db_path, *result_paths, *asset_paths] if not path.exists()]
    if missing:
        raise FileNotFoundError("missing required files: " + ", ".join(map(str, missing)))

    backup_root = back_up(root, db_path, result_paths, asset_paths)
    reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    updated_results = {
        content_id: update_result(content_id, read_json(result_root / f"{content_id}.result.json"), reviewed_at)
        for content_id in selected_records
    }
    updated_assets = {}
    for path in asset_paths:
        content_id = next(content_id for content_id in selected_records if content_id in str(path))
        result = updated_results[content_id]
        comments_by_id = {
            str(comment.get("comment_id") or ""): comment
            for comment in result.get("comments", [])
            if isinstance(comment, dict) and comment.get("comment_id")
        }
        updated_assets[path] = update_asset(
            content_id,
            read_json(path),
            comments_by_id,
            [item["evidence_id"] for item in result["evidence_items"]],
        )

    for content_id, result in updated_results.items():
        write_json_atomic(result_root / f"{content_id}.result.json", result)
    for path, payload in updated_assets.items():
        write_json_atomic(path, payload)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in selected_records)
        rows = conn.execute(
            f"SELECT id, note_id FROM audit_results WHERE job_id = ? AND note_id IN ({placeholders})",
            (JOB_ID, *selected_records),
        ).fetchall()
        if len(rows) != len(selected_records):
            raise RuntimeError(f"expected {len(selected_records)} database rows, found {len(rows)}")
        for row in rows:
            content_id = str(row["note_id"])
            result = updated_results[content_id]
            conn.execute(
                """
                UPDATE audit_results
                SET decision = ?, risk_level = ?, categories_json = ?, summary = ?,
                    evidence_count = ?, risk_image_count = 0, risk_frame_count = 0,
                    result_json = ?, review_status = ?, review_note = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ? AND job_id = ?
                """,
                (
                    result["decision"], result["risk_level"], json.dumps(result["categories"], ensure_ascii=False),
                    result["summary"], len(result["evidence_items"]), json.dumps(result, ensure_ascii=False),
                    REVIEW_STATUS, REVIEW_NOTE, reviewed_at, reviewed_at, row["id"], JOB_ID,
                ),
            )
        verify_rows(conn, selected_records)
        conn.commit()

    counts = {"high": 0, "medium": 0, "low": 0, "none": 0}
    for level, _ in selected_records.values():
        counts[level] += 1
    print(json.dumps({
        "job_id": JOB_ID,
        "updated_records": len(selected_records),
        "risk_counts": counts,
        "retained_evidence": sum(len(result["evidence_items"]) for result in updated_results.values()),
        "backup": str(backup_root),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
