"""A/B: run each keyword with Douyin auto-correct on and off, compare candidate sets and lexicon hits."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.audit_agent.triage_matcher import TriageTerm, match_terms, terms_from_rows


def _key(item: dict) -> str:
    return str(item.get("aweme_id") or item.get("note_id") or "")


def _rule_hits(items: list[dict], terms: list[TriageTerm]) -> int:
    total = 0
    for item in items:
        text = " ".join(str(item.get(field) or "") for field in ("title", "desc", "user_signature"))
        total += 1 if match_terms(text, "desc", terms) else 0
    return total


def compare_runs(on_items: list[dict], off_items: list[dict], terms: list[TriageTerm]) -> dict:
    on_keys = {_key(i) for i in on_items if _key(i)}
    off_keys = {_key(i) for i in off_items if _key(i)}
    return {
        "on_count": len(on_keys),
        "off_count": len(off_keys),
        "overlap": len(on_keys & off_keys),
        "only_on": len(on_keys - off_keys),
        "only_off": len(off_keys - on_keys),
        "on_rule_hits": _rule_hits(on_items, terms),
        "off_rule_hits": _rule_hits(off_items, terms),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Douyin query_correct_type A/B for lexicon keywords")
    parser.add_argument("--category", required=True, help="lexicon category id, e.g. gambling")
    parser.add_argument("--account-id", required=True, help="crawler account id with a valid profile")
    parser.add_argument("--out", default="ab-query-correct", help="output root directory")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.crawler_adapter import MediaCrawlerAdapter
    from backend.audit_agent.lexicon_store import LexiconStore

    lexicon = LexiconStore()
    keywords = lexicon.enabled_search_keywords(args.category)
    terms = terms_from_rows(lexicon.triage_terms([args.category]))
    store = CrawlerAccountStore()
    auth_state = AuthStateCipher().decrypt(store.get_auth_state_ciphertext(args.account_id))
    adapter = MediaCrawlerAdapter()
    root = Path(args.out)
    report = {}
    for keyword in keywords:
        runs = {}
        for label, flag in (("on", 1), ("off", 0)):
            output = adapter.run_search(
                platform="dy", keyword=keyword, start_page=1, max_notes=args.limit, max_total_notes=args.limit,
                max_comments=0, max_concurrency=1, max_items_per_minute=5, get_sub_comment=False,
                collect_comments=False, collect_media=False, save_root=root / label / keyword,
                auth_state=auth_state, account_id=args.account_id, query_correct_type=flag,
            )
            runs[label] = output.contents
        report[keyword] = compare_runs(runs["on"], runs["off"], terms)
        print(keyword, json.dumps(report[keyword], ensure_ascii=False))
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
