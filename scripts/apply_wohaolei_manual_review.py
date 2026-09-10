#!/usr/bin/env python3
"""Apply the user-approved manual translations and risk ratings for task 3ad102e072f6."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


JOB_ID = "3ad102e072f6"
RISK_LIBRARY_ID = "hate"
RISK_LIBRARY_LABEL = "民族意识形态风险"
BACKUP_NAME = f"review-{JOB_ID}-20260720-before-provenance-cleanup"

LEVEL_SCORE = {"none": 0, "low": 40, "medium": 60, "high": 80}


RECORDS: dict[str, dict[str, Any]] = {
    "7636196225720562367": {
        "level": "low",
        "title": "老式家庭合影评论区出现人身攻击",
        "summary": "主帖为老式家庭合影，画面内容正常；评论区出现一条针对具体人物的侮辱性评论。",
        "evidence_ids": ["7637344230097404698"],
    },
    "7636783717638666858": {
        "level": "high",
        "title": "女子卧床视频评论区出现粗俗攻击",
        "summary": "视频记录女子卧床日常；评论区出现极度粗俗人身攻击、性骚扰及泛化责骂言论。",
        "evidence_ids": [
            "7638620152414192447",
            "7645369357539001142",
            "7638799000980980480",
            "7639170732517262117",
            "7637188793958826771",
            "7637172671688098597",
            "7637031535236924197",
        ],
    },
    "7637069400713867846": {
        "level": "high",
        "title": "评论区出现多条人身攻击与性羞辱言论",
        "summary": "视频为女子日常内容；评论区出现多条人身攻击、非人化辱骂、外貌羞辱及低俗性羞辱言论。",
        "evidence_ids": [
            "7638092115339969280",
            "7637338069427274559",
            "7638046432444334907",
            "7637364294514328369",
            "7637409646516765477",
            "7638155814020465460",
            "7637831716681990961",
            "7637334953239954182",
        ],
    },
    "7656203227280728554": {
        "level": "medium",
        "title": "相亲话题视频评论区出现粗鄙言论",
        "summary": "视频围绕相亲话题展开；评论区出现两条涉及性取向和性别气质的粗鄙辱骂言论。",
        "evidence_ids": ["7656251761296458545", "7656227965945053990"],
    },
    "7660739864656707327": {
        "level": "none",
        "title": "女子直播回应争议并介绍产品",
        "summary": "视频中女子直播回应争议并介绍产品，现有文本、画面、语音及评论未发现明确风险证据。",
        "evidence_ids": [],
    },
    "7536935733123763497": {
        "level": "low",
        "title": "女子分享离婚经历及带货",
        "summary": "视频中女子分享离婚经历并带货；评论区出现一条围绕民族婚配与交易关系的引战式言论。",
        "evidence_ids": ["7614815128953455400"],
    },
    "7632593025227203327": {
        "level": "none",
        "title": "博主回应评论及日常争议",
        "summary": "视频中博主回应评论及日常争议；评论区相关内容为对同一表达的转述，未发现明确风险证据。",
        "evidence_ids": [],
    },
    "7637134122841831507": {
        "level": "none",
        "title": "博主回应评论并推销护发产品",
        "summary": "视频中博主回应评论并推销护发产品；评论区相关表达缺少明确攻击对象，未发现明确风险证据。",
        "evidence_ids": [],
    },
    "7637340745296418486": {
        "level": "medium",
        "title": "女主播讲述情感经历并带货",
        "summary": "视频中女主播讲述情感经历并带货；评论区出现外貌年龄羞辱、婚恋羞辱和人身攻击言论。",
        "evidence_ids": [
            "7637731493863949094",
            "7637549394279269169",
            "7637435525732926245",
            "7637735933228892985",
            "7637906877061579574",
        ],
    },
    "7656655127848140137": {
        "level": "medium",
        "title": "女性日常视频评论区出现年龄羞辱",
        "summary": "视频为女性日常生活记录；评论区出现一条针对主播年龄和人格的攻击性评论。",
        "evidence_ids": ["7656919272250458914"],
    },
    "7656656385367487942": {
        "level": "medium",
        "title": "女子自拍视频评论区出现婚史羞辱",
        "summary": "视频为女子日常自拍；评论区出现一条针对主播婚史、生育和年龄的羞辱性评论。",
        "evidence_ids": ["7656915177807594275"],
    },
    "7656658969469181318": {
        "level": "medium",
        "title": "女子生活记录评论区出现年龄羞辱",
        "summary": "视频为女子个人生活记录；评论区出现一条针对主播年龄和人格的羞辱性评论。",
        "evidence_ids": ["7656896718265074467"],
    },
}


COMMENT_TRANSLATIONS = {
    "7637344230097404698": "你看起来就像是个拿一碗麸糠就能换走的货色",
    "7638620152414192447": "去死吧，操你。",
    "7645369357539001142": "呸，你这像猪唾沫一样恶心下流的东西，真是开眼界了",
    "7638799000980980480": "看，这女人身边睡着的，就是个畜生",
    "7639170732517262117": "宝贝，还不快去操她，你在干什么呢？",
    "7637188793958826771": "女人们，你们那些见不得人的勾当快藏不住了，哎，这无情的世道。",
    "7637122786733409073": "有个汉族朋友说，夏天的时候绝不碰老婆，因为天热黏糊糊的。",
    "7637172671688098597": "没操到你吗",
    "7637031535236924197": "像是没被那玩意儿满足似的，之后就睡不着了。。[捂脸][捂脸][捂脸][捂脸]",
    "7638092115339969280": "这个男人要是拿东西在你那儿（注：带有隐喻的粗俗俚语）啪啪敲几下，你恐怕以后都坐不下了；再拿工具加力折腾，你可就要受罪了。",
    "7638179138432779058": "像鸡一样咬住鸡冠爬上去，咕咕叫着靠近放个屁，就会哇哇叫着下蛋。",
    "7638261811725550336": "不是说阿布来提是个秃子吗？",
    "7637338069427274559": "爱放屁的秃子",
    "7638046432444334907": "你们俩真般配，结婚吧，两个蠢货",
    "7637364294514328369": "疯子、疯子、疯子、疯子、疯子、疯子。",
    "7637409646516765477": "不当猪的话，你还能干什么？",
    "7638155814020465460": "你怎么会喜欢上这个又丑又老的人？",
    "7637134083181953850": "女人对这个秃头阿不来提不感兴趣吗？",
    "7637831716681990961": "关你们什么事？他有没有小鸡鸡，难道我们这位大姐还要把阿布来提江的小鸡鸡当笛子吹不成？",
    "7637334953239954182": "连这个丑秃子也有人爱上吗？",
    "7637151623870448399": "可惜了，人终归是物以类聚的。",
    "7656251761296458545": "啧啧，原来那帮可怜虫全都是一帮死基佬（屁眼男）啊",
    "7656227965945053990": "一群可怜的死娘炮。",
    "7614815128953455400": "既然你说不嫁给维吾尔人，那你那些卖不出去的东西也别卖给维吾尔人，不行吗？",
    "7632606435280978722": "身为一个女人，看看她写的这些话，真是的/天哪。",
    "7632892066083849011": "@🎈🎈🎈🎈：身为一个女人，看看她写的这些话，真是的/天哪。",
    "7637173610679714617": "这些人疯了吗？",
    "7637485500248752906": "行吧，那你就去试试那个呗，要是你相中了那敢情好。不是有句俗话说“喂玉米要喂给鹅，嫁人就要嫁给秃子”吗，是这么说的吧？这又是怎么了呢？",
    "7637731493863949094": "绝对就是这么回事……我就搞不懂你图啥，还口口声声说喜欢那个死秃子、非要去倒贴（倒追）他。",
    "7637549394279269169": "别在抖音上丢人现眼了，你这个丑八怪、恶心玩意。",
    "7637895414443885349": "千万别嫁他，这男人长得太丑了。",
    "7637435525732926245": "喂，你到底想嫁几个男人啊？自己明明都有老公，你算个什么女人？",
    "7637735933228892985": "呸，又丑又老的老母鸡。",
    "7637906877061579574": "我的天，你这声音怎么听着跟个老太婆、老太奶奶似的。",
    "7656919272250458914": "喂，老太婆，结个婚你又跑出来显摆（直播）上了？丢不丢人啊，你就不能要点脸吗？",
    "7656915177807594275": "都嫁了三个男人了，你还没折腾够吗？之前连个孩子都没趁早生下来。你还天天跑出来跳什么舞？属于你的青春早都过完了。",
    "7656896718265074467": "嘿，别出来了，老太婆。看到你，我们都觉得丢脸。",
}


# Evidence omitted from this mapping is explicitly pass/none in the user's review.
COMMENT_RISKS: dict[str, tuple[str, str, str]] = {
    "7637344230097404698": ("low", "人身攻击", "以侮辱性比喻贬损评论对象"),
    "7638620152414192447": ("high", "极度粗俗人身攻击", "使用极端性脏话直接攻击评论对象"),
    "7645369357539001142": ("high", "人身攻击", "使用‘像猪唾沫一样恶心下流的东西’等侮辱性措辞贬损评论对象"),
    "7638799000980980480": ("medium", "非人化辱骂", "将具体人物称作“畜生”，构成人身攻击"),
    "7639170732517262117": ("high", "性骚扰", "使用明确性行为粗话指使并骚扰他人"),
    "7637188793958826771": ("low", "泛化责骂", "以“见不得人的勾当”泛化责骂女性"),
    "7637172671688098597": ("high", "性骚扰", "使用明确性行为粗话对评论对象进行低俗性骚扰"),
    "7637031535236924197": ("medium", "低俗性暗示", "以‘没被那玩意儿满足’影射性行为，疑似对评论对象开黄腔"),
    "7638092115339969280": ("medium", "性羞辱", "使用低俗性暗示描述针对具体人物的伤害"),
    "7637338069427274559": ("medium", "侮辱性称呼", "使用“爱放屁的秃子”直接辱骂具体人物"),
    "7638046432444334907": ("medium", "人身攻击", "使用“蠢货”辱骂两名具体人物"),
    "7637364294514328369": ("medium", "人身攻击", "连续重复“疯子”辱骂具体人物"),
    "7637409646516765477": ("medium", "非人化辱骂", "将具体人物贬称为“猪”"),
    "7638155814020465460": ("low", "外貌年龄羞辱", "以“又丑又老”贬损具体人物"),
    "7637831716681990961": ("high", "性羞辱", "使用明确性器官粗口进行低俗性羞辱"),
    "7637334953239954182": ("medium", "外貌羞辱", "使用“丑秃子”贬损具体人物"),
    "7656251761296458545": ("medium", "粗鄙辱骂", "使用涉及性取向和生殖器的粗鄙称呼进行辱骂"),
    "7656227965945053990": ("medium", "粗鄙辱骂", "使用“死娘炮”进行低俗人身攻击"),
    "7614815128953455400": ("low", "煽动对立", "以民族婚配和交易关系作对立性反问，具有引战倾向"),
    "7637731493863949094": ("medium", "人身攻击", "以“死秃子”“倒贴”贬损具体人物"),
    "7637549394279269169": ("medium", "人身攻击", "使用“丑八怪、恶心玩意”攻击具体人物"),
    "7637435525732926245": ("low", "婚恋羞辱", "以婚姻关系质问并贬损具体女性"),
    "7637735933228892985": ("medium", "外貌年龄羞辱", "使用“又丑又老的老母鸡”羞辱具体女性"),
    "7637906877061579574": ("medium", "年龄羞辱", "以“老太婆、老太奶奶”贬损具体女性的声音和年龄"),
    "7656919272250458914": ("medium", "年龄人格羞辱", "以年龄、婚礼和“不知羞耻”攻击具体主播"),
    "7656915177807594275": ("medium", "婚史生育羞辱", "针对主播婚史、生育和年龄进行持续羞辱"),
    "7656896718265074467": ("medium", "年龄人格羞辱", "以“老太婆”和“丢脸”攻击具体主播"),
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def backup(root: Path, db_path: Path, paths: list[Path]) -> Path:
    target_root = root / "artifacts" / BACKUP_NAME
    manifest = target_root / "manifest.json"
    if manifest.exists():
        return target_root

    target_root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as source, sqlite3.connect(target_root / db_path.name) as target:
        source.backup(target)

    copied = []
    for source in paths:
        relative = source.relative_to(root)
        destination = target_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(str(relative))
    manifest.write_text(
        json.dumps({"job_id": JOB_ID, "database": db_path.name, "files": copied}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target_root


def set_comment_review(comment: dict[str, Any], comment_id: str) -> None:
    translation = COMMENT_TRANSLATIONS[comment_id]
    comment["translation_zh"] = translation
    comment["translation_status"] = "completed"
    comment.pop("translation_error", None)
    comment.update({
        "audit_status": "completed",
        "audit_source": "",
        "risk_score": 0,
        "risk_level": "none",
        "risk_library_id": "",
        "risk_library_label": "",
        "secondary_library_ids": [],
        "risk_type": "",
        "risk_basis": "",
        "exemption_basis": "未发现明确风险",
        "evidence_quote": "",
    })
    risk = COMMENT_RISKS.get(comment_id)
    if not risk:
        return
    level, risk_type, reason = risk
    comment.update({
        "risk_score": LEVEL_SCORE[level],
        "risk_level": level,
        "risk_library_id": RISK_LIBRARY_ID,
        "risk_library_label": RISK_LIBRARY_LABEL,
        "risk_type": risk_type,
        "risk_basis": reason,
        "exemption_basis": "",
        "evidence_quote": translation,
    })


def comment_id_from_node(node: dict[str, Any]) -> str:
    comment_id = str(node.get("comment_id") or "")
    if comment_id:
        return comment_id
    for key in ("source", "evidence_id", "id"):
        value = str(node.get(key) or "")
        if value.startswith("comment:"):
            return value.removeprefix("comment:")
    return ""


def synchronize_comment_copies(node: Any, comments: dict[str, dict[str, Any]]) -> None:
    if isinstance(node, dict):
        comment_id = comment_id_from_node(node)
        source = comments.get(comment_id)
        if source:
            for key in (
                "translation_zh", "translation_status", "audit_status", "audit_source",
                "risk_score", "risk_level", "risk_library_id", "risk_library_label",
                "secondary_library_ids", "risk_type", "risk_basis", "exemption_basis",
                "evidence_quote",
            ):
                node[key] = copy.deepcopy(source.get(key, ""))
            reason = str(source.get("risk_basis") or "")
            if "reason" in node or node.get("evidence_id") or str(node.get("source") or "").startswith("comment:"):
                node["reason"] = reason
            if "hit_explanation" in node:
                node["hit_explanation"] = reason
            if "evidence_risk_level" in node:
                node["evidence_risk_level"] = source.get("risk_level") or "none"
            if "severity" in node:
                node["severity"] = source.get("risk_level") or "none"
        for value in node.values():
            synchronize_comment_copies(value, comments)
    elif isinstance(node, list):
        for value in node:
            synchronize_comment_copies(value, comments)


def comment_stats(comments: list[dict[str, Any]]) -> dict[str, int]:
    completed = [item for item in comments if item.get("audit_status") == "completed"]
    return {
        "total": len(comments),
        "completed": len(completed),
        "failed": len(comments) - len(completed),
        "translation_required": sum(bool(item.get("translation_zh") or item.get("translation_error")) for item in comments),
        "translation_completed": sum(item.get("translation_status") == "completed" for item in comments),
        "translation_failed": sum(item.get("translation_status") == "failed" for item in comments),
        "review_count": sum(int(item.get("risk_score") or 0) >= 40 for item in completed),
        "high_count": sum(item.get("risk_level") == "high" for item in completed),
        "max_score": max((int(item.get("risk_score") or 0) for item in completed), default=0),
    }


def evidence_from_comment(comment: dict[str, Any]) -> dict[str, Any]:
    comment_id = str(comment["comment_id"])
    return {
        "evidence_id": f"comment:{comment_id}",
        "source": f"comment:{comment_id}",
        "primary_modality": "comment",
        "comment_id": comment_id,
        "nickname": comment.get("nickname") or "",
        "text": comment.get("source_text") or comment.get("content") or "",
        "translation_zh": comment.get("translation_zh") or "",
        "risk_library_id": RISK_LIBRARY_ID,
        "risk_library_label": RISK_LIBRARY_LABEL,
        "secondary_library_ids": [],
        "risk_type": comment["risk_type"],
        "reason": comment["risk_basis"],
        "hit_explanation": comment["risk_basis"],
        "exemption_basis": "",
        "risk_score": int(comment["risk_score"]),
        "evidence_risk_level": comment["risk_level"],
        "id": f"comment:{comment_id}",
        "modality": "comment",
        "source_label": "评论",
    }


def update_result(content_id: str, result: dict[str, Any], reviewed_at: str) -> dict[str, Any]:
    spec = RECORDS[content_id]
    comments = result.get("comments") if isinstance(result.get("comments"), list) else []
    comments_by_id = {
        str(comment.get("comment_id") or ""): comment
        for comment in comments
        if isinstance(comment, dict) and comment.get("comment_id")
    }

    record_translation_ids = set(comments_by_id) & set(COMMENT_TRANSLATIONS)
    if not set(spec["evidence_ids"]).issubset(record_translation_ids):
        raise RuntimeError(f"{content_id}: retained evidence is missing from translated comments")

    for comment_id in record_translation_ids:
        set_comment_review(comments_by_id[comment_id], comment_id)

    retained = []
    for comment_id in spec["evidence_ids"]:
        comment = comments_by_id.get(comment_id)
        if not comment:
            raise RuntimeError(f"{content_id}: missing retained comment {comment_id}")
        if comment_id not in COMMENT_RISKS:
            raise RuntimeError(f"{content_id}: retained comment lacks risk mapping {comment_id}")
        retained.append(comment)

    actual_level = max((comment["risk_level"] for comment in retained), key=LEVEL_SCORE.get, default="none")
    if actual_level != spec["level"]:
        raise RuntimeError(f"{content_id}: record level {spec['level']} does not match evidence maximum {actual_level}")

    evidence_items = [evidence_from_comment(comment) for comment in retained]
    category_scores: dict[str, int] = {}
    for comment in retained:
        category_scores[comment["risk_type"]] = max(
            category_scores.get(comment["risk_type"], 0), int(comment["risk_score"])
        )
    categories = list(category_scores)
    level = spec["level"]
    score = LEVEL_SCORE[level]
    decision = "pass" if level == "none" else "review"
    score_breakdown = [
        {
            "rule_id": f"hate_comment_{index}",
            "rule": item["risk_type"],
            "category": item["risk_type"],
            "library_id": RISK_LIBRARY_ID,
            "modality": "comment",
            "source": "comment",
            "score": item["risk_score"],
            "score_policy": "once_per_rule",
            "evidence_ids": [item["evidence_id"]],
        }
        for index, item in enumerate(evidence_items, 1)
    ]
    rule_matches = [
        {
            "rule_id": row["rule_id"],
            "rule_name": row["rule"],
            "modality": "comment",
            "evidence_ids": row["evidence_ids"],
            "confidence": "high",
            "features": [],
        }
        for row in score_breakdown
    ]
    result.update({
        "content_title": spec["title"],
        "summary": spec["summary"],
        "decision": decision,
        "risk_level": level,
        "risk_score": score,
        "primary_risk": categories[0] if categories else "",
        "categories": categories,
        "category_scores": [
            {
                "category": category,
                "score": category_score,
                "level": next(name for name, value in LEVEL_SCORE.items() if value == category_score),
            }
            for category, category_score in category_scores.items()
        ],
        "score_breakdown": score_breakdown,
        "risk_basis": "评论区风险证据命中" if retained else "未发现明确风险",
        "evidence_items": evidence_items,
        "rule_matches": rule_matches,
        "evidence": [],
        "risk_evidence": [
            {
                "kind": "comment",
                "source": item["source"],
                "severity": item["evidence_risk_level"],
                "text": item["text"],
                "translation_zh": item["translation_zh"],
                "reason": item["reason"],
                "hit_explanation": item["reason"],
                "risk_library_id": RISK_LIBRARY_ID,
                "risk_library_label": RISK_LIBRARY_LABEL,
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
    })
    result.pop("review", None)
    result.pop("raw_audit", None)

    evidence_index = result.get("evidence_index")
    if isinstance(evidence_index, dict):
        synchronize_comment_copies(evidence_index, comments_by_id)
        evidence_index["final_evidence_refs"] = [item["evidence_id"] for item in evidence_items]
    return result


def update_asset(payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    comments = {
        str(comment.get("comment_id") or ""): comment
        for comment in result.get("comments") or []
        if isinstance(comment, dict) and comment.get("comment_id")
    }
    synchronize_comment_copies(payload, comments)
    payload["final_evidence_refs"] = [item["evidence_id"] for item in result["evidence_items"]]
    return payload


def verify_payloads(results: dict[str, dict[str, Any]], assets: dict[str, dict[str, Any]]) -> None:
    all_translated_ids: set[str] = set()
    retained_ids: set[str] = set()
    for content_id, result in results.items():
        spec = RECORDS[content_id]
        if (result["risk_level"], result["risk_score"], result["decision"]) != (
            spec["level"], LEVEL_SCORE[spec["level"]], "pass" if spec["level"] == "none" else "review"
        ):
            raise RuntimeError(f"{content_id}: final record fields are inconsistent")
        comments = {
            str(comment.get("comment_id") or ""): comment
            for comment in result.get("comments") or []
            if isinstance(comment, dict) and comment.get("comment_id")
        }
        for comment_id in set(comments) & set(COMMENT_TRANSLATIONS):
            all_translated_ids.add(comment_id)
            if comments[comment_id].get("translation_zh") != COMMENT_TRANSLATIONS[comment_id]:
                raise RuntimeError(f"{content_id}: translation mismatch for {comment_id}")
        actual_refs = [str(item.get("comment_id") or "") for item in result.get("evidence_items") or []]
        if actual_refs != spec["evidence_ids"]:
            raise RuntimeError(f"{content_id}: retained evidence mismatch")
        for item in result.get("evidence_items") or []:
            if (item.get("risk_library_id"), item.get("risk_library_label")) != (
                RISK_LIBRARY_ID, RISK_LIBRARY_LABEL
            ):
                raise RuntimeError(f"{content_id}: unexpected risk library on {item.get('comment_id')}")
        retained_ids.update(actual_refs)
        if assets[content_id].get("final_evidence_refs") != [f"comment:{value}" for value in spec["evidence_ids"]]:
            raise RuntimeError(f"{content_id}: external evidence refs mismatch")
    if all_translated_ids != set(COMMENT_TRANSLATIONS):
        raise RuntimeError(f"missing translated comments: {sorted(set(COMMENT_TRANSLATIONS) - all_translated_ids)}")
    if retained_ids != set(COMMENT_RISKS):
        raise RuntimeError(f"retained risk IDs differ from user ratings")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    db_path = root / "data" / "audit_index.sqlite3"
    result_paths = {
        content_id: root / "outputs" / JOB_ID / "results" / f"{content_id}.result.json"
        for content_id in RECORDS
    }
    asset_paths = {
        content_id: root / "outputs" / JOB_ID / "assets" / content_id / "evidence_index.json"
        for content_id in RECORDS
    }
    missing = [path for path in [db_path, *result_paths.values(), *asset_paths.values()] if not path.exists()]
    if missing:
        raise FileNotFoundError("missing required files: " + ", ".join(map(str, missing)))

    reviewed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = {
        content_id: update_result(content_id, read_json(path), reviewed_at)
        for content_id, path in result_paths.items()
    }
    assets = {
        content_id: update_asset(read_json(path), results[content_id])
        for content_id, path in asset_paths.items()
    }
    verify_payloads(results, assets)

    counts = {level: 0 for level in LEVEL_SCORE}
    for spec in RECORDS.values():
        counts[spec["level"]] += 1
    output = {
        "job_id": JOB_ID,
        "updated_records": len(RECORDS),
        "updated_comment_translations": len(COMMENT_TRANSLATIONS),
        "retained_evidence": len(COMMENT_RISKS),
        "risk_counts": counts,
    }
    if args.dry_run:
        output["dry_run"] = True
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    backup_root = backup(root, db_path, [*result_paths.values(), *asset_paths.values()])
    for content_id, path in result_paths.items():
        write_json_atomic(path, results[content_id])
    for content_id, path in asset_paths.items():
        write_json_atomic(path, assets[content_id])

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            f"SELECT id, note_id FROM audit_results WHERE job_id = ? AND note_id IN ({','.join('?' for _ in RECORDS)})",
            (JOB_ID, *RECORDS),
        ).fetchall()
        if len(rows) != len(RECORDS):
            raise RuntimeError(f"expected {len(RECORDS)} database rows, found {len(rows)}")
        for row in rows:
            content_id = str(row["note_id"])
            result = results[content_id]
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
                    "", "", "", reviewed_at, row["id"], JOB_ID,
                ),
            )

        job_row = conn.execute("SELECT items FROM jobs WHERE id = ?", (JOB_ID,)).fetchone()
        if not job_row:
            raise RuntimeError(f"job {JOB_ID} not found")
        items = json.loads(job_row["items"] or "[]")
        replaced = set()
        for index, item in enumerate(items):
            content_id = str(item.get("note_id") or item.get("content_key") or "")
            if content_id in results:
                merged = {**item, **results[content_id]}
                for stale_key in ("evidence_groups", "raw_audit", "review"):
                    merged.pop(stale_key, None)
                items[index] = merged
                replaced.add(content_id)
        if replaced != set(RECORDS):
            raise RuntimeError(f"jobs.items missing records: {sorted(set(RECORDS) - replaced)}")
        conn.execute(
            "UPDATE jobs SET items = ?, updated_at = ? WHERE id = ?",
            (json.dumps(items, ensure_ascii=False), reviewed_at, JOB_ID),
        )
        conn.commit()

    output["backup"] = str(backup_root)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
