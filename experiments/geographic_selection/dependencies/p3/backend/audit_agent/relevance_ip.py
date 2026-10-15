"""Pure, explicit-catalog IP-to-province statistics. No file or network access.

This deliberately does not use the investigation city's preferred-match resolver.
Exact names/recorded aliases at every administrative level remain candidates;
only agreement on province permits a vote. IP is publisher provenance, never
evidence locating the event itself.
"""
from __future__ import annotations

import copy
import re

from .relevance import RelevanceInputError, digest, normalize_candidate, normalize_context

_PROVINCE_CODES = frozenset(
    "110000 120000 130000 140000 150000 210000 220000 230000 310000 "
    "320000 330000 340000 350000 360000 370000 410000 420000 430000 "
    "440000 450000 460000 500000 510000 520000 530000 540000 610000 "
    "620000 630000 640000 650000 710000 810000 820000".split())
_MUNICIPALITY_CODES = frozenset({"110000", "120000", "310000", "500000"})
_KINDS = {
    "province": {"省", "自治区", "直辖市", "特别行政区", "province", "autonomous_region",
                 "municipality", "special_administrative_region"},
    "prefecture": {"地级市", "自治州", "地区", "盟", "prefecture_city", "autonomous_prefecture",
                   "prefecture", "league"},
    "county": {"市辖区", "区", "县", "自治县", "县级市", "旗", "自治旗", "特区", "林区",
               "district", "county", "autonomous_county", "county_city", "banner",
               "autonomous_banner", "special_district", "forestry_district"},
}


def _catalog_index(catalog: dict) -> tuple[dict, dict, dict]:
    if not isinstance(catalog, dict) or catalog.get("schema_version") != "geographic-catalog-v1":
        raise RelevanceInputError("INVALID_IP_CATALOG")
    records = catalog.get("records")
    validation = catalog.get("validation", {})
    if (not isinstance(records, list) or not records or not isinstance(catalog.get("version"), str)
            or not catalog["version"].strip()
            or not isinstance(validation, dict) or validation.get("status") != "complete"
            or validation.get("records_sha256") != digest(records)):
        raise RelevanceInputError("INVALID_IP_CATALOG")
    by_code, by_name = {}, {}
    for row in records:
        if (not isinstance(row, dict)
                or set(row) != {"code", "name", "level", "kind", "province_code", "aliases"}
                or not isinstance(row.get("code"), str)
                or not re.fullmatch(r"[0-9]{6}", row["code"])
                or row["code"] in by_code
                or not isinstance(row.get("level"), str) or row["level"] not in _KINDS
                or not isinstance(row.get("province_code"), str)
                or not isinstance(row.get("name"), str) or not row["name"]
                or row["name"] != row["name"].strip()
                or not isinstance(row.get("kind"), str) or not row["kind"].strip()
                or not isinstance(row.get("aliases"), list)
                or any(not isinstance(a, str) or not a or a != a.strip() for a in row["aliases"])
                or len(set(row["aliases"])) != len(row["aliases"])
                or row["name"] in row["aliases"]):
            raise RelevanceInputError("INVALID_IP_CATALOG")
        if row["kind"] not in _KINDS[row["level"]]:
            raise RelevanceInputError("INVALID_IP_CATALOG")
        if row["level"] == "province":
            if (row["code"] not in _PROVINCE_CODES or row["province_code"] != row["code"]
                    or ((row["kind"] in {"直辖市", "municipality"})
                        != (row["code"] in _MUNICIPALITY_CODES))):
                raise RelevanceInputError("INVALID_IP_CATALOG")
        elif (row["code"].endswith("0000")
              or (row["level"] == "prefecture" and (
                  not row["code"].endswith("00") or row["province_code"] in _MUNICIPALITY_CODES))
              or (row["level"] == "county" and row["code"].endswith("00"))):
            raise RelevanceInputError("INVALID_IP_CATALOG")
        by_code[row["code"]] = row
        for name in {row["name"], *row["aliases"]}:
            by_name.setdefault(name, []).append(row)
    for row in records:
        province = by_code.get(row["province_code"])
        if (province is None or province["level"] != "province"
                or province["province_code"] != province["code"]
                or row["code"][:2] != province["code"][:2]
                or (row["level"] == "province" and row["code"] != province["code"])):
            raise RelevanceInputError("INVALID_IP_CATALOG")
    provenance = {"version": catalog["version"], "schema_version": catalog["schema_version"],
                  "records_sha256": validation["records_sha256"], "catalog_digest": digest(catalog)}
    return by_code, by_name, provenance


def validate_catalog(catalog: dict) -> dict:
    """Validate hashes/shape/parents and return an isolated code-to-record mapping.

    Repeated names or aliases across records are legitimate ambiguity, whereas
    duplicate codes and repeated aliases within one record are malformed.
    This is runtime integrity checking, not an assertion of official authenticity.
    """
    by_code, _, _ = _catalog_index(catalog)
    return copy.deepcopy(by_code)


def _resolve(value, by_code: dict, by_name: dict) -> dict:
    if value is not None and not isinstance(value, str):
        raise RelevanceInputError("INVALID_TEXT_TYPE")
    text = re.sub(r"^IP\s*属地\s*[:：]?\s*", "", (value or "").strip(), flags=re.IGNORECASE).strip()
    result = {"raw": value, "input_name": text, "status": "unknown",
              "province_code": None, "province_name": None, "matches": []}
    if text.lower() in {"", "unknown", "none", "null", "未知", "暂无", "未显示", "-", "0"}:
        result["status"] = "missing"
        return result
    matches = by_name.get(text, [])
    result["matches"] = [{key: row[key] for key in ("code", "name", "level", "province_code")}
                         for row in sorted(matches, key=lambda row: row["code"])]
    provinces = {row["province_code"] for row in matches}
    if len(provinces) == 1:
        code = next(iter(provinces))
        result.update(status="resolved", province_code=code, province_name=by_code[code]["name"])
    elif provinces:
        result["status"] = "ambiguous"
    return result


def normalize_ip_province(value, *, catalog: dict) -> dict:
    """Resolve an exact administrative name/alias, retaining all candidate evidence."""
    by_code, by_name, _ = _catalog_index(catalog)
    return _resolve(value, by_code, by_name)


def compute_ip_stats(context: dict, candidate: dict, *, catalog: dict) -> dict:
    """Normalize each IP before stable-user deduplication and province voting."""
    context, candidate = normalize_context(context), normalize_candidate(candidate)
    by_code, by_name, provenance = _catalog_index(catalog)
    policy, province = context["policy"]["ip"], context["scope"]["province"]
    target = _resolve(province["name"], by_code, by_name)
    if target["status"] != "resolved":
        raise RelevanceInputError("UNRESOLVED_IP_TARGET_PROVINCE")
    target_code = target["province_code"]
    users, comment_sources = {}, []
    for comment in candidate["comments"]:
        resolved = _resolve(comment["ip_location"], by_code, by_name)
        identity = comment["author_identity"]
        comment_sources.append({"source_index": comment["source_index"],
                                "comment_id": comment["comment_id"],
                                "eligible_identity": identity is not None, **resolved})
        if identity:
            values = users.setdefault((identity["namespace"], identity["value"]), set())
            if resolved["status"] == "resolved":
                values.add(resolved["province_code"])
    valid = [next(iter(values)) for values in users.values() if len(values) == 1]
    target_count = sum(code == target_code for code in valid)
    coverage = len(valid) / len(users) if users else None
    share = target_count / len(valid) if valid else None
    author_sources = []
    for field in ("author_ip_location", "ip_location"):
        resolved = _resolve(candidate["post"][field], by_code, by_name)
        author_sources.append({"field": field, **resolved, "normalized": resolved["province_name"]})
    author_values = {s["province_code"] for s in author_sources if s["status"] == "resolved"}
    author_support = author_values == {target_code}
    distribution_support = (len(valid) >= policy["min_valid_users"] and coverage is not None
                            and coverage >= policy["min_valid_coverage"] and share is not None
                            and share >= policy["min_target_share"])
    return {"sampled_comment_count": len(candidate["comments"]), "unique_commenters": len(users),
            "valid_ip_commenters": len(valid), "target_province_commenters": target_count,
            "valid_coverage": coverage, "target_share": share,
            "unidentified_comment_count": sum(c["author_identity"] is None for c in candidate["comments"]),
            "conflicting_ip_commenters": sum(len(values) > 1 for values in users.values()),
            "author_sources": author_sources, "author_ip_conflict": len(author_values) > 1,
            "author_support": author_support, "comment_distribution_support": distribution_support,
            "ip_support": author_support or distribution_support, "policy": copy.deepcopy(policy),
            "interpretation": "publisher_provenance_only_not_event_location",
            "catalog_provenance": provenance,
            "normalization_audit": {
                "target_province_code": target_code,
                "unknown_ip_commenters": sum(not values for values in users.values()),
                "comments": comment_sources,
                "normalization": "exact_catalog_name_or_alias_all_candidates_same_province",
                "unknown_policy": "not_in_valid_province_denominator"}}
