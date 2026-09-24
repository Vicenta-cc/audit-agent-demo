"""Per-keyword candidate scoring and selection: rules first, one flash call otherwise."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .triage_matcher import TriageHit, TriageTerm, match_terms, normalize_text, terms_from_rows

RULE_HIT_SCORE = 300
RULE_HIT_CAP = 900
DISCARD_SCORE = -1000
MODEL_SCORES = {"strong": 200, "weak": 100, "none": 0}

# 官方/机构类蓝V识别片段：命中即discard，不进精审。$ 结尾的片段只匹配 enterprise_verify_reason 的末尾
# （如"XX局"），避免"局"作为任意子串把商家认证也判成机构号。商家/企业蓝V（如"商家认证账号"、
# "XX科技有限公司"）不命中这里，按无认证账号的粉丝阈值正常打分（方案 §4，2026-09-23）。
OFFICIAL_VERIFY_PATTERNS: tuple[str, ...] = (
    "官方", "政务", "公安", "警", "法院", "检察", "政府", "委员会", "管理中心", "人民",
    "电视台", "广播", "电台", "日报", "晚报", "新闻", "通讯社", "融媒体", "传媒中心",
    "银行", "大学", "学院", "学校", "医院", "协会", "学会", "基金会", "工会", "妇联",
    "团委", "消防", "交警", "派出所", "街道", "社区",
    "局$", "厅$", "部$", "署$", "委$", "院$", "中心$",
)


def _compile_official_verify_regex(patterns: tuple[str, ...]) -> re.Pattern[str]:
    if not patterns:
        return re.compile(r"(?!)")   # 空元组：永不匹配，而不是空 pattern 匹配一切
    return re.compile("|".join(patterns))


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
    def __init__(self, lexicon_store, qwen, *, model: str, max_comments: int, request_timeout: int,
                 max_followers_personal_verified: int = 500000, max_followers_unverified: int = 1000000,
                 official_verify_patterns: tuple[str, ...] = OFFICIAL_VERIFY_PATTERNS):
        self.lexicon_store = lexicon_store
        self.qwen = qwen
        self.model = model
        self.max_comments = max_comments
        self.request_timeout = request_timeout
        self.max_followers_personal_verified = max_followers_personal_verified
        self.max_followers_unverified = max_followers_unverified
        self.official_verify_patterns = tuple(official_verify_patterns)
        self._official_verify_regex = _compile_official_verify_regex(self.official_verify_patterns)
        self._terms_cache: dict[tuple[str, ...], list[TriageTerm]] = {}
        # 归一化的搜索词 -> 归一化的所在词条主词；平台搜索词和变体不在匹配词表里，要单独查
        self._search_groups: dict[tuple[str, ...], dict[str, str]] = {}

    def terms_for(self, category_ids: list[str]) -> list[TriageTerm]:
        key = tuple(sorted(str(item) for item in category_ids if str(item).strip()))
        if key not in self._terms_cache:
            self._terms_cache[key] = terms_from_rows(self.lexicon_store.triage_terms(list(key))) if key else []
            groups = self.lexicon_store.search_word_groups(list(key)) if key else {}
            self._search_groups[key] = {normalize_text(word): normalize_text(group)
                                        for word, group in groups.items() if normalize_text(word)}
        return self._terms_cache[key]

    def _search_groups_for(self, terms: list[TriageTerm]) -> dict[str, str]:
        for key, cached in self._terms_cache.items():
            if cached is terms:
                return self._search_groups.get(key, {})
        return {}

    def _rule_hits(self, item: dict, comments: list[dict], terms: list[TriageTerm]) -> list[TriageHit]:
        hits: list[TriageHit] = []
        for name, value in (("title", _text(item.get("title"))), ("desc", _text(item.get("desc"))),
                            ("signature", _text(item.get("user_signature")))):
            hits.extend(match_terms(value, name, terms))
        for comment in comments[: self.max_comments]:
            label = f"comment:{_text(comment.get('comment_id'))}"
            hits.extend(match_terms(_comment_text(comment), label, terms))
        return hits

    @staticmethod
    def _rules_text(rules: dict | None) -> str:
        """把任务分类的判定规则原文拼进提示词：审核目标、证据规则（含高危/中危/低危与放行定义）、
        以及与证据规则不同的融合规则。没有判定规则时返回空串，提示词里就没有规则段。"""
        if not isinstance(rules, dict):
            return ""
        audit_goal = _text(rules.get("audit_goal"))
        evidence_rules = _text(rules.get("evidence_rules"))
        fusion_rules = _text(rules.get("fusion_rules"))
        sections: list[str] = []
        if audit_goal:
            sections.append(f"审核目标：{audit_goal}\n")
        if evidence_rules:
            sections.append(f"证据规则：\n{evidence_rules}\n")
        if fusion_rules and fusion_rules != evidence_rules:
            sections.append(f"融合规则：\n{fusion_rules}\n")
        return "".join(sections)

    def build_prompt(self, item: dict, comments: list[dict], terms: list[TriageTerm],
                     *, rules: dict | None = None) -> str:
        payload = {
            "post": {
                "title": _text(item.get("title"))[:200],
                "desc": _text(item.get("desc"))[:800],
                "author": _text(item.get("nickname")),
                "signature": _text(item.get("user_signature"))[:200],
                "verify": _text(item.get("custom_verify")) or _text(item.get("enterprise_verify_reason")),
                "followers": _text(item.get("follower_count")),
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
            # 判定口径全部来自任务分类的判定规则，代码里不写死豁免语境或风险定义
            + self._rules_text(rules) +
            "只输出合法 JSON，不要输出 Markdown：\n"
            "{\"content_type\":\"科普|新闻|日常|带货|婚恋|擦边|暗语交易|其他\","
            "\"suspicion\":\"none|weak|strong\",\"matched_terms\":[\"命中的词或变体\"],"
            "\"locations\":[\"desc|signature|comment:<id>\"],\"reason\":\"不超过40字\"}\n"
            "content_type 只是描述性标签，不参与判定。"
            "matched_terms 与 locations 各最多列 5 项，只列最有代表性的，不要穷举。\n"
            "按上述判定规则判断这条内容：符合高危定义答 strong，符合中危定义答 weak，属于低危、放行或安全语境答 none。\n"
            "输入 JSON：\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

    def _identity_discard_reason(self, item: dict) -> str:
        """身份丢弃（方案 §4）：官方/机构类蓝V（媒体、政务、公安、事业单位等，按
        official_verify_patterns 识别）一律丢弃；商家/企业类蓝V按无认证账号的粉丝阈值正常打分；
        个人黄V/无认证账号粉丝超阈值同样丢弃，阈值 0 = 不限。"""
        enterprise = _text(item.get("enterprise_verify_reason"))
        if enterprise and self._official_verify_regex.search(enterprise):
            return f"官方/机构认证账号（{enterprise[:40]}），不进精审"
        followers = _int(item.get("follower_count"))
        if enterprise:
            # 商家/企业认证：认证本身不是身份丢弃理由，但粉丝仍受无认证账号的阈值约束
            limit = self.max_followers_unverified
            if limit <= 0 or followers <= limit:
                return ""
            return f"商家/企业认证账号（{enterprise[:30]}）粉丝 {followers} 超过 {limit}，不进精审"
        custom_verify = _text(item.get("custom_verify"))
        limit = self.max_followers_personal_verified if custom_verify else self.max_followers_unverified
        if limit <= 0 or followers <= limit:
            return ""
        if custom_verify:
            return f"个人认证账号（{custom_verify[:30]}）粉丝 {followers} 超过 {limit}，不进精审"
        return f"粉丝 {followers} 超过 {limit}，不进精审"

    @staticmethod
    def _drop_self_hits(hits: list[TriageHit], search_keyword: str, terms: list[TriageTerm],
                        search_groups: dict[str, str] | None = None) -> tuple[list[TriageHit], int]:
        """搜「房卡代理」搜回来的帖子必然含「房卡」：搜索词所在词条（主词及全部变体）的命中不算风险信号，
        被搜索词包含的非正则词条（搜「注册送彩金」时的「彩金」）同样不算；其他词条照常计分。"""
        needle = normalize_text(search_keyword)
        if not needle:
            return hits, 0
        self_groups = {needle}
        if search_groups and search_groups.get(needle):
            self_groups.add(search_groups[needle])
        self_groups.update(normalize_text(t.entry_group) for t in terms
                           if t.entry_group and normalize_text(t.keyword) == needle)

        def is_self(hit: TriageHit) -> bool:
            if normalize_text(hit.entry_group) in self_groups:
                return True
            keyword = normalize_text(hit.keyword)
            return hit.match_type != "正则" and bool(keyword) and keyword in needle

        kept = [h for h in hits if not is_self(h)]
        return kept, len(hits) - len(kept)

    def score(self, content_key: str, rank: int, item: dict, comments: list[dict], terms: list[TriageTerm],
              *, search_keyword: str = "", rules: dict | None = None) -> CandidateScore:
        engagement = _int(item.get("liked_count")) + _int(item.get("comment_count")) + _int(item.get("share_count"))
        # 身份先于规则和模型：蓝V的反诈科普必然带黑话和联系方式，不丢掉就会占走该词唯一的深审名额
        discard_reason = self._identity_discard_reason(item)
        if discard_reason:
            return CandidateScore(content_key, rank, DISCARD_SCORE, "discard", discard_reason, engagement=engagement)
        hits, self_hits = self._drop_self_hits(self._rule_hits(item, comments, terms), search_keyword, terms,
                                               self._search_groups_for(terms))
        # 自命中被扣掉后才走模型的候选，要在理由里说清楚，运营看日志时不会以为规则层漏判
        note = f"（搜索词所在词条命中 {self_hits} 处不计分）" if self_hits else ""
        if hits:
            top = hits[0]
            total = min(RULE_HIT_CAP, RULE_HIT_SCORE * len(hits))
            reason = f"命中{top.category_id or '词库'}：{top.keyword}（{top.field}）"
            return CandidateScore(content_key, rank, total, "rule", reason,
                                  hits=[asdict(h) for h in hits], engagement=engagement)
        try:
            raw = self.qwen.audit_text(self.build_prompt(item, comments, terms, rules=rules), max_tokens=800,
                                       model=self.model, enable_thinking=False,
                                       request_timeout=self.request_timeout)
        except Exception as exc:
            return CandidateScore(content_key, rank, MODEL_SCORES["weak"], "weak",
                                  f"初筛模型调用失败，按 weak 计：{exc}"[:200] + note, engagement=engagement)
        # 模型答一个数组或字符串时 json.loads 原样返回，按调用失败同样处理，不能让整个词失败
        if not isinstance(raw, dict):
            return CandidateScore(content_key, rank, MODEL_SCORES["weak"], "weak",
                                  "初筛模型输出不合法（非 JSON 对象），按 weak 计" + note, engagement=engagement)
        suspicion = _text(raw.get("suspicion")).lower()
        if suspicion not in MODEL_SCORES:
            return CandidateScore(content_key, rank, MODEL_SCORES["weak"], "weak", "初筛模型输出不合法，按 weak 计" + note,
                                  model=raw, engagement=engagement)
        matched = _str_list(raw.get("matched_terms"))
        model = {**raw, "matched_terms": matched, "locations": _str_list(raw.get("locations"))}
        score_value, band = MODEL_SCORES[suspicion], suspicion
        reason = _text(raw.get("reason"))[:120] or f"模型判定 {suspicion}"
        return CandidateScore(content_key, rank, score_value, band, reason + note,
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
