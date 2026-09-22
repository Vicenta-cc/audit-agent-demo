"""Per-keyword candidate scoring and selection: rules first, one flash call otherwise."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .triage_matcher import TriageHit, TriageTerm, diversion_hits, match_terms, terms_from_rows

RULE_HIT_SCORE = 300
RULE_HIT_CAP = 900
TRUSTED_PENALTY = -500
MODEL_SCORES = {"strong": 200, "weak": 100, "none": 0}


@dataclass
class CandidateScore:
    content_key: str
    rank: int
    score: int
    band: str
    reason: str
    hits: list[dict] = field(default_factory=list)
    model: dict | None = None
    engagement: int = 0


def _text(value) -> str:
    return str(value or "").strip()


def _int(value) -> int:
    try:
        return int(str(value or "0").strip() or 0)
    except ValueError:
        return 0


def _str_list(value) -> list[str]:
    """模型可能把 matched_terms/locations 答成字符串；非 list 一律当空，避免按字拆成命中。"""
    return [_text(item) for item in value if _text(item)] if isinstance(value, list) else []


def _comment_text(comment: dict) -> str:
    return _text(comment.get("content") or comment.get("source_text") or comment.get("text"))


class TriageEngine:
    def __init__(self, lexicon_store, qwen, *, model: str, max_comments: int, request_timeout: int):
        self.lexicon_store = lexicon_store
        self.qwen = qwen
        self.model = model
        self.max_comments = max_comments
        self.request_timeout = request_timeout
        self._terms_cache: dict[tuple[str, ...], list[TriageTerm]] = {}

    def terms_for(self, category_ids: list[str]) -> list[TriageTerm]:
        key = tuple(sorted(str(item) for item in category_ids if str(item).strip()))
        if key not in self._terms_cache:
            self._terms_cache[key] = terms_from_rows(self.lexicon_store.triage_terms(list(key))) if key else []
        return self._terms_cache[key]

    def _rule_hits(self, item: dict, comments: list[dict], terms: list[TriageTerm]) -> list[TriageHit]:
        hits: list[TriageHit] = []
        for name, value in (("title", _text(item.get("title"))), ("desc", _text(item.get("desc"))),
                            ("signature", _text(item.get("user_signature")))):
            hits.extend(match_terms(value, name, terms))
            hits.extend(diversion_hits(value, name))
        for comment in comments[: self.max_comments]:
            label = f"comment:{_text(comment.get('comment_id'))}"
            value = _comment_text(comment)
            hits.extend(match_terms(value, label, terms))
            hits.extend(diversion_hits(value, label))
        return hits

    def build_prompt(self, item: dict, comments: list[dict], terms: list[TriageTerm]) -> str:
        payload = {
            "post": {
                "title": _text(item.get("title"))[:200],
                "desc": _text(item.get("desc"))[:800],
                "author": _text(item.get("nickname")),
                "signature": _text(item.get("user_signature"))[:200],
                "verify": _text(item.get("custom_verify")) or _text(item.get("enterprise_verify_reason")),
                "stats": {"liked": _text(item.get("liked_count")), "comments": _text(item.get("comment_count")),
                          "shares": _text(item.get("share_count"))},
                "source_keyword": _text(item.get("source_keyword")),
            },
            "comments": [{"id": _text(c.get("comment_id")), "text": _comment_text(c)[:300]}
                         for c in comments[: self.max_comments] if _comment_text(c)],
            "lexicon_terms": sorted({term.keyword for term in terms if term.match_type != "正则"}),
        }
        return (
            "你是内容风险初筛器，任务是判断一条社交平台帖子是否值得进入深度多模态审核。"
            "lexicon_terms 是本次调查的黑话词与变体清单，请把它当作识别暗语、谐音、拆字、拼音缩写的参考，"
            "不要只做字面匹配。评论区往往是隐藏交易的接头处，要逐条看。\n"
            "只输出合法 JSON，不要输出 Markdown：\n"
            "{\"content_type\":\"科普|新闻|日常|带货|婚恋|擦边|暗语交易|其他\","
            "\"suspicion\":\"none|weak|strong\",\"matched_terms\":[\"命中的词或变体\"],"
            "\"locations\":[\"desc|signature|comment:<id>\"],\"reason\":\"不超过40字\"}\n"
            "判定标准：strong = 正文、签名或评论中存在与 lexicon_terms 同义的暗语、导流方式或交易意图；"
            "weak = 有可疑但不明确的信号，或作者是小号且内容擦边；none = 明显正常内容且评论区无异常。"
            "不确定时选 weak，不要选 none。\n输入 JSON：\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

    def score(self, content_key: str, rank: int, item: dict, comments: list[dict], terms: list[TriageTerm]) -> CandidateScore:
        engagement = _int(item.get("liked_count")) + _int(item.get("comment_count")) + _int(item.get("share_count"))
        hits = self._rule_hits(item, comments, terms)
        verify_reason = _text(item.get("enterprise_verify_reason"))
        if hits:
            top = hits[0]
            # 规则层三个信号相加（方案 §4）：认证账号的 −500 要能抵掉命中分，
            # 否则蓝V的反诈科普只要出现"微信"就会拿走该词唯一的深审名额。
            total = min(RULE_HIT_CAP, RULE_HIT_SCORE * len(hits)) + (TRUSTED_PENALTY if verify_reason else 0)
            reason = f"命中{top.category_id or '导流'}：{top.keyword}（{top.field}）"
            if verify_reason:
                reason += "，认证账号 −500"
            return CandidateScore(content_key, rank, total, "rule", reason,
                                  hits=[asdict(h) for h in hits], engagement=engagement)
        if verify_reason:
            return CandidateScore(content_key, rank, TRUSTED_PENALTY, "trusted",
                                  f"认证账号（{verify_reason[:40]}）且无命中", engagement=engagement)
        try:
            raw = self.qwen.audit_text(self.build_prompt(item, comments, terms), max_tokens=400, model=self.model,
                                       enable_thinking=False, request_timeout=self.request_timeout)
        except Exception as exc:
            return CandidateScore(content_key, rank, MODEL_SCORES["weak"], "weak",
                                  f"初筛模型调用失败，按 weak 计：{exc}"[:200], engagement=engagement)
        # 模型答一个数组或字符串时 json.loads 原样返回，按调用失败同样处理，不能让整个词失败
        if not isinstance(raw, dict):
            return CandidateScore(content_key, rank, MODEL_SCORES["weak"], "weak",
                                  "初筛模型输出不合法（非 JSON 对象），按 weak 计", engagement=engagement)
        suspicion = _text(raw.get("suspicion")).lower()
        if suspicion not in MODEL_SCORES:
            return CandidateScore(content_key, rank, MODEL_SCORES["weak"], "weak", "初筛模型输出不合法，按 weak 计",
                                  model=raw, engagement=engagement)
        matched = _str_list(raw.get("matched_terms"))
        model = {**raw, "matched_terms": matched, "locations": _str_list(raw.get("locations"))}
        return CandidateScore(content_key, rank, MODEL_SCORES[suspicion], suspicion,
                              _text(raw.get("reason"))[:120] or f"模型判定 {suspicion}",
                              hits=[{"keyword": v, "match_type": "model", "category_id": "", "risk_level": "", "field": "", "snippet": ""} for v in matched],
                              model=model, engagement=engagement)


def rank_candidates(scores: list[CandidateScore]) -> list[CandidateScore]:
    return sorted(scores, key=lambda s: (-s.score, -s.rank, s.engagement))


def select_candidate(ranked: list[CandidateScore], *, exclude_keys: set[str]) -> CandidateScore | None:
    """Pick the best positive-score candidate; a keyword whose candidates all look normal yields None."""
    for candidate in ranked:
        if candidate.score <= 0:
            return None
        if candidate.content_key not in exclude_keys:
            return candidate
    return None


def write_candidates_file(directory: Path, keyword: str, ranked: list[CandidateScore],
                          selected: CandidateScore | None, strategy: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "candidates.json"
    path.write_text(json.dumps({
        "keyword": keyword,
        "strategy": strategy,
        "selected": selected.content_key if selected else None,
        "collected": False,
        "candidates": [asdict(item) for item in ranked],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _read_candidates_file(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def mark_candidates_collected(candidate_root: Path) -> None:
    """Record that this keyword's pick was precisely collected, so a resumed run skips the word."""
    path = candidate_root / "candidates.json"
    payload = _read_candidates_file(path)
    if payload is None:
        return
    payload["collected"] = True
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return


def load_collected_selections(job_crawl_dir: Path) -> dict[str, str]:
    """keyword -> content_key for every word already collected in this job, rotation dirs included."""
    collected: dict[str, str] = {}
    for path in sorted(Path(job_crawl_dir).rglob("candidates.json")):
        payload = _read_candidates_file(path)
        if payload is None or not payload.get("collected"):
            continue
        keyword = str(payload.get("keyword") or "").strip()
        selected = str(payload.get("selected") or "").strip()
        if keyword and selected:
            collected[keyword] = selected
    return collected
