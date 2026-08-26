from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hermes_m0.source_projection import read_verified_raw_item, unix_timestamp

ACCOUNT_CORPUS_SCHEMA_VERSION = "account-global-corpus-m2.2/v3"
LEGACY_ACCOUNT_CORPUS_SCHEMA_VERSION = "account-global-corpus-a0/v2"
SUPPORTED_ACCOUNT_CORPUS_SCHEMA_VERSIONS = frozenset(
    {ACCOUNT_CORPUS_SCHEMA_VERSION, LEGACY_ACCOUNT_CORPUS_SCHEMA_VERSION}
)
ACCOUNT_IDENTITY_SCHEMA_VERSION = "account-identity/v2"
DOUYIN_PLATFORM = "douyin"
DOUYIN_ACCOUNT_NAMESPACE = "douyin.sec_uid"
DEFAULT_AUTHORIZED_TASK_IDS = (
    "8bc179209e1e",
    "3ad102e072f6",
)


class FrozenDict(dict):
    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("Account corpus records are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return FrozenDict({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    return value


def normalize_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", text)


def normalize_source_id(value: Any) -> str:
    return normalize_text(value).strip()


def canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): canonicalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    if isinstance(value, str):
        return normalize_text(value)
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonicalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def account_ref(
    *, platform: str, source_namespace: str, source_account_key: str
) -> str:
    identity = {
        "schema_version": ACCOUNT_IDENTITY_SCHEMA_VERSION,
        "platform": normalize_source_id(platform).lower(),
        "source_namespace": normalize_source_id(source_namespace),
        "source_account_key": normalize_source_id(source_account_key),
    }
    if not all(identity[key] for key in ("platform", "source_namespace", "source_account_key")):
        raise ValueError("account identity requires platform, namespace, and source key")
    return "account:v2:" + content_hash(identity)


def _domain_ref(kind: str, payload: dict[str, Any]) -> str:
    return f"{kind}:v2:{content_hash(payload)}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_timestamp(value: Any) -> str:
    raw = normalize_source_id(value)
    if not raw:
        return ""
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _platform(value: Any) -> str:
    normalized = normalize_source_id(value).lower()
    if normalized in {"dy", "douyin"}:
        return DOUYIN_PLATFORM
    return normalized


def _identity_from_source(platform: str, source: dict[str, Any]) -> dict[str, Any]:
    if platform != DOUYIN_PLATFORM:
        return {"status": "unresolved", "account_ref": None}
    sec_uid = normalize_source_id(source.get("sec_uid"))
    if not sec_uid:
        return {"status": "unresolved", "account_ref": None}
    return {
        "status": "resolved",
        "account_ref": account_ref(
            platform=platform,
            source_namespace=DOUYIN_ACCOUNT_NAMESPACE,
            source_account_key=sec_uid,
        ),
        "source_namespace": DOUYIN_ACCOUNT_NAMESPACE,
        "source_account_key": sec_uid,
    }


def _snapshot_identity(
    snapshot: dict[str, Any],
    *,
    schema_version: str = ACCOUNT_CORPUS_SCHEMA_VERSION,
) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "task_id": snapshot["task_id"],
        "source_hash": snapshot["source_hash"],
        "captured_at": snapshot["captured_at"],
        "coverage": snapshot["coverage"],
        "data_quality": snapshot["data_quality"],
    }


def _write_fixture(path: Path, fixture: dict[str, Any]) -> None:
    encoded = (canonical_json(fixture) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        with path.open("wb") as target, gzip.GzipFile(
            filename="", mode="wb", fileobj=target, compresslevel=9, mtime=0
        ) as compressed:
            compressed.write(encoded)
        return
    path.write_bytes(encoded)


def _read_fixture(path: Path) -> dict[str, Any]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as source:
            return json.load(source)
    return json.loads(path.read_text(encoding="utf-8"))


def _readonly_connection(source_db: Path) -> sqlite3.Connection:
    uri = f"file:{source_db.resolve().as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def build_account_fixture(
    *,
    source_db: Path,
    output_path: Path,
    task_ids: Iterable[str] = DEFAULT_AUTHORIZED_TASK_IDS,
) -> dict[str, Any]:
    requested_tasks = tuple(sorted({normalize_source_id(value) for value in task_ids}))
    if not requested_tasks or any(not value for value in requested_tasks):
        raise ValueError("at least one non-empty task id is required")
    source_db = source_db.resolve()
    source_db_hash = _sha256_file(source_db)

    placeholders = ",".join("?" for _ in requested_tasks)
    with _readonly_connection(source_db) as connection:
        jobs = {
            row["id"]: dict(row)
            for row in connection.execute(
                f"""
                SELECT id, display_name, platform, status, created_at, updated_at,
                       json_array_length(items) AS item_count
                FROM jobs
                WHERE id IN ({placeholders})
                ORDER BY id
                """,
                requested_tasks,
            )
        }
        if set(jobs) != set(requested_tasks):
            missing = sorted(set(requested_tasks) - set(jobs))
            raise ValueError(f"authorized tasks are missing from source database: {missing}")

        task_content_counts = {
            row["task_id"]: int(row["member_count"])
            for row in connection.execute(
                f"""
                SELECT task_id, COUNT(*) AS member_count
                FROM task_contents
                WHERE task_id IN ({placeholders})
                GROUP BY task_id
                """,
                requested_tasks,
            )
        }
        rows = list(
            connection.execute(
                f"""
                SELECT ar.job_id, ar.platform, ar.content_key, ar.title,
                       ar.author_json, ar.result_json, ar.decision, ar.risk_level,
                       ar.analyzed_at,
                       ar.created_at, ar.updated_at, ar.review_status,
                       ar.reviewed_at, tc.raw_item_path,
                       tc.created_at AS task_content_captured_at
                FROM audit_results AS ar
                LEFT JOIN task_contents AS tc
                  ON tc.task_id = ar.job_id
                 AND tc.content_id = ar.content_id
                WHERE ar.job_id IN ({placeholders})
                ORDER BY ar.job_id, ar.content_key
                """,
                requested_tasks,
            )
        )

    rows_by_task: dict[str, list[sqlite3.Row]] = {task: [] for task in requested_tasks}
    for row in rows:
        rows_by_task[row["job_id"]].append(row)

    accounts: dict[str, dict[str, Any]] = {}
    aliases: list[dict[str, Any]] = []
    occurrences: list[dict[str, Any]] = []
    interactions: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    global_post_identities: set[tuple[str, str]] = set()
    global_comment_identities: set[tuple[str, str]] = set()

    for task_id in requested_tasks:
        job = jobs[task_id]
        task_rows = rows_by_task[task_id]
        source_posts: list[dict[str, Any]] = []
        staged_posts: list[dict[str, Any]] = []
        missing_comment_text = 0
        missing_comment_identity = 0
        missing_post_identity = 0
        missing_post_published_at = 0
        audit_generated_post_titles = 0
        source_post_titles = 0
        unavailable_post_titles = 0
        manual_corrected = 0
        comments_total = 0
        post_author_keys: set[str] = set()
        comment_author_keys: set[str] = set()
        task_content_keys: set[str] = set()
        task_comment_ids: set[str] = set()

        for row in task_rows:
            try:
                author = json.loads(row["author_json"] or "{}")
                result = json.loads(row["result_json"] or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid source JSON for task {task_id}, content {row['content_key']}"
                ) from exc
            if not isinstance(author, dict) or not isinstance(result, dict):
                raise TypeError(
                    f"source record is not an object for task {task_id}, content {row['content_key']}"
                )
            comments = result.get("comments") or []
            if not isinstance(comments, list):
                raise TypeError(
                    f"comments are not an array for task {task_id}, content {row['content_key']}"
                )

            platform = _platform(row["platform"])
            content_key = normalize_source_id(row["content_key"])
            if not content_key or content_key in task_content_keys:
                raise ValueError(
                    f"duplicate or empty Post identity for task {task_id}: {content_key}"
                )
            task_content_keys.add(content_key)
            global_post_identity = (platform, content_key)
            if global_post_identity in global_post_identities:
                raise ValueError(
                    f"cross-task duplicate Post identity is unsupported: {content_key}"
                )
            global_post_identities.add(global_post_identity)
            raw_item = read_verified_raw_item(
                row["raw_item_path"], expected_aweme_id=content_key
            )
            raw_source_title = normalize_text(
                (raw_item or {}).get("title") or (raw_item or {}).get("desc")
            )
            stored_source_title = normalize_text(row["title"])
            if (
                raw_source_title
                and stored_source_title
                and raw_source_title != stored_source_title
            ):
                raise ValueError(
                    f"raw and stored source titles disagree for task {task_id}, "
                    f"content {content_key}"
                )
            source_title = raw_source_title or stored_source_title
            audit_generated_title = normalize_text(result.get("content_title")).strip()
            if audit_generated_title:
                display_title = audit_generated_title
                display_title_source = "audit_generated"
                audit_generated_post_titles += 1
            elif source_title:
                display_title = source_title
                display_title_source = "source"
                source_post_titles += 1
            else:
                display_title = ""
                display_title_source = "unavailable"
                unavailable_post_titles += 1
            published_at_epoch, published_at_value = unix_timestamp(
                (raw_item or {}).get("create_time")
            )
            published_at = published_at_value or None
            if published_at is None:
                missing_post_published_at += 1
            captured_at = _utc_timestamp(row["task_content_captured_at"])
            analyzed_at = _utc_timestamp(row["analyzed_at"])
            updated_at = _utc_timestamp(row["updated_at"])
            decision = normalize_source_id(
                row["decision"] or result.get("decision")
            ).lower()
            risk_level = normalize_source_id(
                row["risk_level"] or result.get("risk_level")
            ).lower()
            if decision not in {"pass", "review", "reject"}:
                raise ValueError(
                    f"invalid Post decision for task {task_id}, content {content_key}"
                )
            if risk_level not in {"none", "low", "medium", "high"}:
                raise ValueError(
                    f"invalid Post risk level for task {task_id}, content {content_key}"
                )
            author_identity = _identity_from_source(platform, author)
            author_nickname = normalize_text(author.get("nickname"))
            if author_identity["status"] == "resolved":
                author_ref = author_identity["account_ref"]
                post_author_keys.add(author_identity["source_account_key"])
                accounts.setdefault(
                    author_ref,
                    {
                        "account_ref": author_ref,
                        "platform": platform,
                        "source_namespace": author_identity["source_namespace"],
                        "source_account_key": author_identity["source_account_key"],
                    },
                )
            else:
                author_ref = None
                missing_post_identity += 1

            normalized_comments: list[dict[str, Any]] = []
            for source_comment in comments:
                if not isinstance(source_comment, dict):
                    raise TypeError(
                        f"comment is not an object for task {task_id}, content {content_key}"
                    )
                comment_id = normalize_source_id(source_comment.get("comment_id"))
                if not comment_id:
                    raise ValueError(
                        f"comment is missing comment_id for task {task_id}, content {content_key}"
                    )
                if comment_id in task_comment_ids:
                    raise ValueError(
                        f"duplicate Comment identity for task {task_id}: {comment_id}"
                    )
                task_comment_ids.add(comment_id)
                global_comment_identity = (platform, comment_id)
                if global_comment_identity in global_comment_identities:
                    raise ValueError(
                        f"cross-task duplicate Comment identity is unsupported: {comment_id}"
                    )
                global_comment_identities.add(global_comment_identity)
                aweme_id = normalize_source_id(source_comment.get("aweme_id"))
                if aweme_id and aweme_id != content_key:
                    raise ValueError(
                        f"comment {comment_id} points to the wrong parent post"
                    )
                parent_comment_id = normalize_source_id(
                    source_comment.get("parent_comment_id")
                )
                if parent_comment_id not in {"", "0"}:
                    raise ValueError(
                        f"A0 source contains unsupported sub-comment {comment_id}"
                    )
                comment_identity = _identity_from_source(platform, source_comment)
                comment_nickname = normalize_text(source_comment.get("nickname"))
                comment_text = normalize_text(source_comment.get("content"))
                comment_epoch, comment_time = unix_timestamp(
                    source_comment.get("create_time")
                )
                comment_audit_status = normalize_source_id(
                    source_comment.get("audit_status")
                ).lower()
                comment_risk_level = normalize_source_id(
                    source_comment.get("risk_level")
                ).lower()
                if comment_audit_status != "completed":
                    raise ValueError(
                        f"invalid Comment audit status for task {task_id}, "
                        f"comment {comment_id}"
                    )
                if comment_risk_level not in {"none", "low", "medium", "high"}:
                    raise ValueError(
                        f"invalid Comment risk level for task {task_id}, "
                        f"comment {comment_id}"
                    )
                if not comment_text:
                    missing_comment_text += 1
                if comment_identity["status"] == "resolved":
                    comment_ref = comment_identity["account_ref"]
                    comment_author_keys.add(comment_identity["source_account_key"])
                    accounts.setdefault(
                        comment_ref,
                        {
                            "account_ref": comment_ref,
                            "platform": platform,
                            "source_namespace": comment_identity["source_namespace"],
                            "source_account_key": comment_identity["source_account_key"],
                        },
                    )
                else:
                    comment_ref = None
                    missing_comment_identity += 1
                normalized_comments.append(
                    {
                        "comment_id": comment_id,
                        "comment_time_epoch": comment_epoch,
                        "comment_time": comment_time,
                        "comment_text": comment_text,
                        "audit_status": comment_audit_status,
                        "risk_level": comment_risk_level,
                        "risk_type": normalize_text(source_comment.get("risk_type")),
                        "account_ref": comment_ref,
                        "identity_status": comment_identity["status"],
                        "nickname": comment_nickname,
                    }
                )
            normalized_comments.sort(key=lambda item: item["comment_id"])
            comments_total += len(normalized_comments)
            manual_corrected += int(
                normalize_source_id(row["review_status"]) == "manual_corrected"
            )
            source_posts.append(
                {
                    "platform": platform,
                    "content_key": content_key,
                    "source_title": source_title,
                    "display_title": display_title,
                    "display_title_source": display_title_source,
                    "published_at_epoch": published_at_epoch,
                    "published_at": published_at,
                    "captured_at": captured_at,
                    "analyzed_at": analyzed_at,
                    "updated_at": updated_at,
                    "audit_status": "completed",
                    "decision": decision,
                    "risk_level": risk_level,
                    "author": {
                        "account_ref": author_ref,
                        "identity_status": author_identity["status"],
                        "nickname": author_nickname,
                    },
                    "comments": normalized_comments,
                }
            )
            staged_posts.append(
                {
                    "platform": platform,
                    "content_key": content_key,
                    "source_title": source_title,
                    "display_title": display_title,
                    "display_title_source": display_title_source,
                    "published_at_epoch": published_at_epoch,
                    "published_at": published_at,
                    "captured_at": captured_at,
                    "analyzed_at": analyzed_at,
                    "updated_at": updated_at,
                    "author_ref": author_ref,
                    "author_status": author_identity["status"],
                    "author_nickname": author_nickname,
                    "audit_status": "completed",
                    "decision": decision,
                    "risk_level": risk_level,
                    "comments": normalized_comments,
                }
            )

        source_posts.sort(key=lambda item: item["content_key"])
        source_projection = {
            "schema_version": ACCOUNT_CORPUS_SCHEMA_VERSION,
            "task_id": task_id,
            "task_display_name": normalize_text(job["display_name"]),
            "platform": _platform(job["platform"]),
            "posts": source_posts,
        }
        source_hash = content_hash(source_projection)
        captured_at = max(
            (item["updated_at"] or item["captured_at"] for item in staged_posts),
            default=_utc_timestamp(job["updated_at"]),
        )
        coverage = {
            "post_count": len(staged_posts),
            "comment_count": comments_total,
            "resolved_post_author_occurrences": len(staged_posts)
            - missing_post_identity,
            "unresolved_post_author_occurrences": missing_post_identity,
            "resolved_comment_author_occurrences": comments_total
            - missing_comment_identity,
            "unresolved_comment_author_occurrences": missing_comment_identity,
        }
        data_quality = {
            "formal_report_version_present": False,
            "cross_dataset_only": True,
            "job_status": normalize_source_id(job["status"]),
            "job_items_count": int(job["item_count"] or 0),
            "task_contents_count": int(task_content_counts.get(task_id, 0)),
            "audit_result_projection_count": len(staged_posts),
            "unprojected_task_contents_count": max(
                int(task_content_counts.get(task_id, 0)) - len(staged_posts), 0
            ),
            "comments_with_empty_text": missing_comment_text,
            "posts_with_audit_generated_display_title": audit_generated_post_titles,
            "posts_with_source_display_title": source_post_titles,
            "posts_with_unavailable_display_title": unavailable_post_titles,
            "posts_with_unavailable_published_at": missing_post_published_at,
            "manual_corrected_audit_rows": manual_corrected,
            "risk_and_decision_fields_projected": True,
            "source_basis": (
                "current audit_results rows and linked raw items at the frozen cutoff"
            ),
            "post_author_account_count": len(post_author_keys),
            "comment_author_account_count": len(comment_author_keys),
        }
        snapshot_identity = {
            "schema_version": ACCOUNT_CORPUS_SCHEMA_VERSION,
            "task_id": task_id,
            "source_hash": source_hash,
            "captured_at": captured_at,
            "coverage": coverage,
            "data_quality": data_quality,
        }
        snapshot_ref = _domain_ref("frozen-task-snapshot", snapshot_identity)
        snapshot = {
            "snapshot_ref": snapshot_ref,
            "task_id": task_id,
            "task_display_name": normalize_text(job["display_name"]),
            "platform": _platform(job["platform"]),
            "source_hash": source_hash,
            "captured_at": captured_at,
            "coverage": coverage,
            "data_quality": data_quality,
        }
        snapshots.append(snapshot)

        for post in sorted(staged_posts, key=lambda item: item["content_key"]):
            post_source = {
                "platform": post["platform"],
                "content_key": post["content_key"],
                "source_title": post["source_title"],
                "display_title": post["display_title"],
                "display_title_source": post["display_title_source"],
                "published_at_epoch": post["published_at_epoch"],
                "published_at": post["published_at"],
                "captured_at": post["captured_at"],
                "analyzed_at": post["analyzed_at"],
                "updated_at": post["updated_at"],
                "author_ref": post["author_ref"],
                "author_status": post["author_status"],
                "author_nickname": post["author_nickname"],
                "audit_status": post["audit_status"],
                "decision": post["decision"],
                "risk_level": post["risk_level"],
            }
            post_source_hash = content_hash(post_source)
            post_occurrence_identity = {
                "schema_version": ACCOUNT_CORPUS_SCHEMA_VERSION,
                "kind": "post_author",
                "task_id": task_id,
                "source_snapshot_ref": snapshot_ref,
                "content_key": post["content_key"],
                "source_record_hash": post_source_hash,
            }
            post_occurrence_ref = _domain_ref(
                "account-occurrence", post_occurrence_identity
            )
            post_occurrence = {
                "occurrence_ref": post_occurrence_ref,
                "account_ref": post["author_ref"],
                "identity_status": post["author_status"],
                "kind": "post_author",
                "task_id": task_id,
                "source_snapshot_ref": snapshot_ref,
                "occurred_at": post["published_at"],
                "captured_at": post["captured_at"],
                "analyzed_at": post["analyzed_at"],
                "audit_status": post["audit_status"],
                "decision": post["decision"],
                "risk_level": post["risk_level"],
                "post": {
                    "platform": post["platform"],
                    "content_key": post["content_key"],
                    "source_title": post["source_title"],
                    "display_title": post["display_title"],
                    "display_title_source": post["display_title_source"],
                    "published_at": post["published_at"],
                },
                "comment": None,
                "parent_post_occurrence_ref": None,
                "parent_post_author_account_ref": post["author_ref"],
                "source_record_hash": post_source_hash,
                "source_locator": {
                    "table": "audit_results",
                    "task_id": task_id,
                    "content_key": post["content_key"],
                    "published_at_source": "task_contents.raw_item_path:item.create_time",
                },
            }
            occurrences.append(post_occurrence)
            if post["author_ref"] and post["author_nickname"]:
                alias_identity = {
                    "schema_version": ACCOUNT_CORPUS_SCHEMA_VERSION,
                    "account_ref": post["author_ref"],
                    "nickname": post["author_nickname"],
                    "observed_at": post["captured_at"],
                    "captured_at": post["captured_at"],
                    "source_occurrence_ref": post_occurrence_ref,
                }
                aliases.append(
                    {
                        "alias_observation_ref": _domain_ref(
                            "account-alias-observation", alias_identity
                        ),
                        **{key: value for key, value in alias_identity.items() if key != "schema_version"},
                        "task_id": task_id,
                        "source_snapshot_ref": snapshot_ref,
                        "source_kind": "post_author",
                    }
                )

            for comment in post["comments"]:
                comment_source = {
                    "comment_id": comment["comment_id"],
                    "comment_time_epoch": comment["comment_time_epoch"],
                    "comment_time": comment["comment_time"],
                    "comment_text": comment["comment_text"],
                    "account_ref": comment["account_ref"],
                    "identity_status": comment["identity_status"],
                    "nickname": comment["nickname"],
                    "audit_status": comment["audit_status"],
                    "risk_level": comment["risk_level"],
                    "risk_type": comment["risk_type"],
                    "parent_content_key": post["content_key"],
                }
                comment_source_hash = content_hash(comment_source)
                occurrence_identity = {
                    "schema_version": ACCOUNT_CORPUS_SCHEMA_VERSION,
                    "kind": "comment_author",
                    "task_id": task_id,
                    "source_snapshot_ref": snapshot_ref,
                    "content_key": post["content_key"],
                    "comment_id": comment["comment_id"],
                    "source_record_hash": comment_source_hash,
                }
                occurrence_ref = _domain_ref(
                    "account-occurrence", occurrence_identity
                )
                occurrence = {
                    "occurrence_ref": occurrence_ref,
                    "account_ref": comment["account_ref"],
                    "identity_status": comment["identity_status"],
                    "kind": "comment_author",
                    "task_id": task_id,
                    "source_snapshot_ref": snapshot_ref,
                    "occurred_at": comment["comment_time"],
                    "captured_at": post["captured_at"],
                    "analyzed_at": post["analyzed_at"],
                    "audit_status": comment["audit_status"],
                    "decision": None,
                    "risk_level": comment["risk_level"],
                    "post": {
                        "platform": post["platform"],
                        "content_key": post["content_key"],
                        "source_title": post["source_title"],
                        "display_title": post["display_title"],
                        "display_title_source": post["display_title_source"],
                        "published_at": post["published_at"],
                    },
                    "comment": {
                        "comment_id": comment["comment_id"],
                        "comment_time_epoch": comment["comment_time_epoch"],
                        "comment_time": comment["comment_time"],
                        "text": comment["comment_text"],
                        "audit_status": comment["audit_status"],
                        "risk_level": comment["risk_level"],
                        "risk_type": comment["risk_type"],
                    },
                    "parent_post_occurrence_ref": post_occurrence_ref,
                    "parent_post_author_account_ref": post["author_ref"],
                    "source_record_hash": comment_source_hash,
                    "source_locator": {
                        "table": "audit_results",
                        "task_id": task_id,
                        "content_key": post["content_key"],
                        "comment_id": comment["comment_id"],
                    },
                }
                occurrences.append(occurrence)
                if comment["account_ref"] and comment["nickname"]:
                    alias_identity = {
                        "schema_version": ACCOUNT_CORPUS_SCHEMA_VERSION,
                        "account_ref": comment["account_ref"],
                        "nickname": comment["nickname"],
                        "observed_at": comment["comment_time"],
                        "captured_at": post["captured_at"],
                        "source_occurrence_ref": occurrence_ref,
                    }
                    aliases.append(
                        {
                            "alias_observation_ref": _domain_ref(
                                "account-alias-observation", alias_identity
                            ),
                            **{
                                key: value
                                for key, value in alias_identity.items()
                                if key != "schema_version"
                            },
                            "task_id": task_id,
                            "source_snapshot_ref": snapshot_ref,
                            "source_kind": "comment_author",
                        }
                    )
                if comment["account_ref"] and post["author_ref"]:
                    interaction_identity = {
                        "schema_version": ACCOUNT_CORPUS_SCHEMA_VERSION,
                        "task_id": task_id,
                        "source_snapshot_ref": snapshot_ref,
                        "actor_account_ref": comment["account_ref"],
                        "post_author_account_ref": post["author_ref"],
                        "comment_occurrence_ref": occurrence_ref,
                        "parent_post_occurrence_ref": post_occurrence_ref,
                    }
                    interactions.append(
                        {
                            "direct_interaction_ref": _domain_ref(
                                "direct-interaction", interaction_identity
                            ),
                            **{
                                key: value
                                for key, value in interaction_identity.items()
                                if key != "schema_version"
                            },
                            "fact_kind": "comment_on_authored_post",
                        }
                    )

    account_values = sorted(accounts.values(), key=lambda item: item["account_ref"])
    aliases.sort(key=lambda item: item["alias_observation_ref"])
    occurrences.sort(key=lambda item: item["occurrence_ref"])
    interactions.sort(key=lambda item: item["direct_interaction_ref"])
    snapshots.sort(key=lambda item: item["task_id"])
    collection_hashes = {
        "task_snapshots": content_hash(
            [_snapshot_identity(snapshot) for snapshot in snapshots]
        ),
        "accounts": content_hash(account_values),
        "alias_observations": content_hash(aliases),
        "occurrences": content_hash(occurrences),
        "direct_interactions": content_hash(interactions),
    }
    revision_identity = {
        "schema_version": ACCOUNT_CORPUS_SCHEMA_VERSION,
        "authorized_task_ids": list(requested_tasks),
        "collection_hashes": collection_hashes,
    }
    corpus_revision = content_hash(revision_identity)
    for snapshot in snapshots:
        snapshot["corpus_revision"] = corpus_revision

    fixture = {
        "schema_version": ACCOUNT_CORPUS_SCHEMA_VERSION,
        "authorized_task_ids": list(requested_tasks),
        "corpus_revision": corpus_revision,
        "source": {
            "database_file_name": source_db.name,
            "database_sha256": source_db_hash,
            "read_contract": "sqlite mode=ro, immutable=1, query_only=ON",
            "raw_item_read_contract": (
                "read-only item projection from task_contents.raw_item_path; "
                "item.aweme_id must equal audit_results.content_key"
            ),
            "tables": ["audit_results", "jobs", "task_contents"],
        },
        "collection_hashes": collection_hashes,
        "task_snapshots": snapshots,
        "accounts": account_values,
        "alias_observations": aliases,
        "occurrences": occurrences,
        "direct_interactions": interactions,
    }
    _write_fixture(output_path, fixture)
    return fixture


@dataclass(frozen=True)
class AccountCorpus:
    schema_version: str
    authorized_task_ids: tuple[str, ...]
    corpus_revision: str
    source: dict[str, Any]
    collection_hashes: dict[str, str]
    task_snapshots: tuple[dict[str, Any], ...]
    accounts: tuple[dict[str, Any], ...]
    alias_observations: tuple[dict[str, Any], ...]
    occurrences: tuple[dict[str, Any], ...]
    direct_interactions: tuple[dict[str, Any], ...]

    @classmethod
    def load(cls, path: Path | None = None) -> AccountCorpus:
        fixture_path = path or (
            Path(__file__).with_name("fixtures") / "account_m22_corpus.json.gz"
        )
        value = _read_fixture(fixture_path)
        corpus = cls(
            schema_version=value["schema_version"],
            authorized_task_ids=tuple(value["authorized_task_ids"]),
            corpus_revision=value["corpus_revision"],
            source=freeze(value["source"]),
            collection_hashes=freeze(value["collection_hashes"]),
            task_snapshots=tuple(freeze(item) for item in value["task_snapshots"]),
            accounts=tuple(freeze(item) for item in value["accounts"]),
            alias_observations=tuple(
                freeze(item) for item in value["alias_observations"]
            ),
            occurrences=tuple(freeze(item) for item in value["occurrences"]),
            direct_interactions=tuple(
                freeze(item) for item in value["direct_interactions"]
            ),
        )
        corpus._validate()
        corpus._build_indexes()
        object.__setattr__(corpus, "_fixture_path", fixture_path)
        return corpus

    def _validate(self) -> None:
        if self.schema_version not in SUPPORTED_ACCOUNT_CORPUS_SCHEMA_VERSIONS:
            raise ValueError("unsupported Account corpus schema version")
        if tuple(sorted(set(self.authorized_task_ids))) != self.authorized_task_ids:
            raise ValueError("authorized task ids must be unique and sorted")
        snapshots = {item["snapshot_ref"]: item for item in self.task_snapshots}
        accounts = {item["account_ref"]: item for item in self.accounts}
        occurrences = {item["occurrence_ref"]: item for item in self.occurrences}
        if len(snapshots) != len(self.task_snapshots):
            raise ValueError("snapshot references must be unique")
        if len(accounts) != len(self.accounts):
            raise ValueError("Account references must be unique")
        if len(occurrences) != len(self.occurrences):
            raise ValueError("occurrence references must be unique")
        if {item["task_id"] for item in self.task_snapshots} != set(
            self.authorized_task_ids
        ):
            raise ValueError("every authorized task requires exactly one snapshot")

        for item in self.accounts:
            expected = account_ref(
                platform=item["platform"],
                source_namespace=item["source_namespace"],
                source_account_key=item["source_account_key"],
            )
            if item["account_ref"] != expected:
                raise ValueError("Account reference does not match stable identity")
        for snapshot in self.task_snapshots:
            expected = _domain_ref(
                "frozen-task-snapshot",
                _snapshot_identity(snapshot, schema_version=self.schema_version),
            )
            if snapshot["snapshot_ref"] != expected:
                raise ValueError("snapshot reference does not match immutable payload")
            if snapshot["corpus_revision"] != self.corpus_revision:
                raise ValueError("snapshot corpus revision is stale")

        post_occurrences = {
            (item["task_id"], item["post"]["content_key"]): item
            for item in self.occurrences
            if item["kind"] == "post_author"
        }
        seen_comments: set[tuple[str, str]] = set()
        seen_global_posts: set[tuple[str, str]] = set()
        seen_global_comments: set[tuple[str, str]] = set()
        for item in self.occurrences:
            if item["task_id"] not in self.authorized_task_ids:
                raise ValueError("occurrence references an unauthorized task")
            snapshot = snapshots.get(item["source_snapshot_ref"])
            if not snapshot or snapshot["task_id"] != item["task_id"]:
                raise ValueError("occurrence references the wrong task snapshot")
            if item["identity_status"] == "resolved":
                if item["account_ref"] not in accounts:
                    raise ValueError("resolved occurrence references an unknown Account")
            elif item["account_ref"] is not None:
                raise ValueError("unresolved occurrence cannot reference an Account")
            post = item["post"]
            if item["kind"] == "post_author":
                global_post = (str(post["platform"]), str(post["content_key"]))
                if global_post in seen_global_posts:
                    raise ValueError(
                        "cross-task duplicate Post identities are unsupported"
                    )
                seen_global_posts.add(global_post)
            display_title_source = post.get("display_title_source")
            if display_title_source not in {"audit_generated", "source", "unavailable"}:
                raise ValueError("Post display title source is invalid")
            if display_title_source == "audit_generated" and not post.get(
                "display_title"
            ):
                raise ValueError("audit-generated Post display title is empty")
            if display_title_source == "source" and (
                not post.get("source_title")
                or post.get("display_title") != post.get("source_title")
            ):
                raise ValueError("source Post display title is inconsistent")
            if display_title_source == "unavailable" and post.get("display_title"):
                raise ValueError("unavailable Post display title must be empty")
            if item["kind"] == "post_author" and (
                item.get("occurred_at") != post.get("published_at")
            ):
                raise ValueError("Post occurrence time must equal raw published time")
            occurrence_identity = {
                "schema_version": self.schema_version,
                "kind": item["kind"],
                "task_id": item["task_id"],
                "source_snapshot_ref": item["source_snapshot_ref"],
                "content_key": item["post"]["content_key"],
                "source_record_hash": item["source_record_hash"],
            }
            if item["kind"] == "comment_author":
                occurrence_identity["comment_id"] = item["comment"]["comment_id"]
            if item["occurrence_ref"] != _domain_ref(
                "account-occurrence", occurrence_identity
            ):
                raise ValueError("occurrence reference does not match immutable identity")
            if item["kind"] == "comment_author":
                comment = item.get("comment") or {}
                comment_key = (item["task_id"], comment.get("comment_id", ""))
                if not comment_key[1] or comment_key in seen_comments:
                    raise ValueError("comment occurrence identity must be unique in a task")
                seen_comments.add(comment_key)
                global_comment = (
                    str(post["platform"]),
                    str(comment.get("comment_id", "")),
                )
                if global_comment in seen_global_comments:
                    raise ValueError(
                        "cross-task duplicate Comment identities are unsupported"
                    )
                seen_global_comments.add(global_comment)
                parent = post_occurrences.get(
                    (item["task_id"], item["post"]["content_key"])
                )
                if not parent or parent["occurrence_ref"] != item[
                    "parent_post_occurrence_ref"
                ]:
                    raise ValueError("comment occurrence references the wrong parent post")
                if parent["account_ref"] != item["parent_post_author_account_ref"]:
                    raise ValueError("comment occurrence references the wrong post author")
                if parent["post"] != item["post"]:
                    raise ValueError("comment occurrence has a stale Parent Post projection")
                if (
                    item.get("occurred_at")
                    and parent.get("occurred_at")
                    and item["occurred_at"] < parent["occurred_at"]
                ):
                    raise ValueError("Comment occurrence predates its Parent Post")
            elif item["kind"] != "post_author":
                raise ValueError("unsupported occurrence kind")

            if self.schema_version == ACCOUNT_CORPUS_SCHEMA_VERSION:
                audit_status = item.get("audit_status")
                if not isinstance(audit_status, str) or not audit_status:
                    raise ValueError("Account occurrence audit status is invalid")
                if item.get("risk_level") not in {"none", "low", "medium", "high"}:
                    raise ValueError("Account occurrence risk level is invalid")
                if item["kind"] == "post_author":
                    if audit_status != "completed":
                        raise ValueError("Post occurrence audit status is invalid")
                    if item.get("decision") not in {"pass", "review", "reject"}:
                        raise ValueError("Post occurrence decision is invalid")
                else:
                    comment = item.get("comment") or {}
                    if (
                        comment.get("audit_status") != item.get("audit_status")
                        or comment.get("risk_level") != item.get("risk_level")
                        or "risk_type" not in comment
                    ):
                        raise ValueError("Comment occurrence review projection is inconsistent")

        alias_refs: set[str] = set()
        for item in self.alias_observations:
            if item["alias_observation_ref"] in alias_refs:
                raise ValueError("alias observation references must be unique")
            alias_refs.add(item["alias_observation_ref"])
            occurrence = occurrences.get(item["source_occurrence_ref"])
            if not occurrence or occurrence["account_ref"] != item["account_ref"]:
                raise ValueError("alias observation has invalid Account provenance")
            if occurrence["task_id"] != item["task_id"]:
                raise ValueError("alias observation has invalid task provenance")
            alias_identity = {
                "schema_version": self.schema_version,
                "account_ref": item["account_ref"],
                "nickname": item["nickname"],
                "observed_at": item["observed_at"],
                "captured_at": item["captured_at"],
                "source_occurrence_ref": item["source_occurrence_ref"],
            }
            if item["alias_observation_ref"] != _domain_ref(
                "account-alias-observation", alias_identity
            ):
                raise ValueError("alias observation reference does not match identity")

        interaction_refs: set[str] = set()
        for item in self.direct_interactions:
            if item["direct_interaction_ref"] in interaction_refs:
                raise ValueError("direct interaction references must be unique")
            interaction_refs.add(item["direct_interaction_ref"])
            comment = occurrences.get(item["comment_occurrence_ref"])
            parent = occurrences.get(item["parent_post_occurrence_ref"])
            if not comment or comment["kind"] != "comment_author":
                raise ValueError("interaction does not reference a comment occurrence")
            if not parent or parent["kind"] != "post_author":
                raise ValueError("interaction does not reference a post occurrence")
            if (
                comment["parent_post_occurrence_ref"] != parent["occurrence_ref"]
                or comment["task_id"] != item["task_id"]
                or parent["task_id"] != item["task_id"]
                or comment["source_snapshot_ref"] != item["source_snapshot_ref"]
                or parent["source_snapshot_ref"] != item["source_snapshot_ref"]
                or comment["account_ref"] != item["actor_account_ref"]
                or parent["account_ref"] != item["post_author_account_ref"]
            ):
                raise ValueError("interaction provenance is inconsistent")
            interaction_identity = {
                "schema_version": self.schema_version,
                "task_id": item["task_id"],
                "source_snapshot_ref": item["source_snapshot_ref"],
                "actor_account_ref": item["actor_account_ref"],
                "post_author_account_ref": item["post_author_account_ref"],
                "comment_occurrence_ref": item["comment_occurrence_ref"],
                "parent_post_occurrence_ref": item["parent_post_occurrence_ref"],
            }
            if item["direct_interaction_ref"] != _domain_ref(
                "direct-interaction", interaction_identity
            ):
                raise ValueError("interaction reference does not match identity")

        expected_hashes = {
            "task_snapshots": content_hash(
                [
                    _snapshot_identity(
                        snapshot, schema_version=self.schema_version
                    )
                    for snapshot in self.task_snapshots
                ]
            ),
            "accounts": content_hash(self.accounts),
            "alias_observations": content_hash(self.alias_observations),
            "occurrences": content_hash(self.occurrences),
            "direct_interactions": content_hash(self.direct_interactions),
        }
        if self.collection_hashes != expected_hashes:
            raise ValueError("Account corpus collection hash mismatch")
        expected_revision = content_hash(
            {
                "schema_version": self.schema_version,
                "authorized_task_ids": list(self.authorized_task_ids),
                "collection_hashes": expected_hashes,
            }
        )
        if self.corpus_revision != expected_revision:
            raise ValueError("Account corpus revision mismatch")

    def _build_indexes(self) -> None:
        object.__setattr__(
            self, "_accounts_by_ref", {item["account_ref"]: item for item in self.accounts}
        )
        object.__setattr__(
            self,
            "_snapshots_by_task",
            {item["task_id"]: item for item in self.task_snapshots},
        )
        object.__setattr__(
            self,
            "_occurrences_by_ref",
            {item["occurrence_ref"]: item for item in self.occurrences},
        )
        object.__setattr__(
            self,
            "_interactions_by_ref",
            {
                item["direct_interaction_ref"]: item
                for item in self.direct_interactions
            },
        )
        occurrences_by_account: dict[str, list[dict[str, Any]]] = {}
        for item in self.occurrences:
            if item["account_ref"]:
                occurrences_by_account.setdefault(item["account_ref"], []).append(item)
        for values in occurrences_by_account.values():
            values.sort(
                key=lambda item: (
                    item["occurred_at"] or "",
                    item["task_id"],
                    item["kind"],
                    item["occurrence_ref"],
                )
            )
        object.__setattr__(self, "_occurrences_by_account", occurrences_by_account)

        aliases_by_account: dict[str, list[dict[str, Any]]] = {}
        for item in self.alias_observations:
            aliases_by_account.setdefault(item["account_ref"], []).append(item)
        for values in aliases_by_account.values():
            values.sort(
                key=lambda item: (
                    item["observed_at"],
                    item["captured_at"],
                    item["task_id"],
                    item["alias_observation_ref"],
                )
            )
        object.__setattr__(self, "_aliases_by_account", aliases_by_account)

        interactions_by_actor: dict[str, list[dict[str, Any]]] = {}
        for item in self.direct_interactions:
            interactions_by_actor.setdefault(item["actor_account_ref"], []).append(item)
        for values in interactions_by_actor.values():
            values.sort(
                key=lambda item: (
                    self._occurrences_by_ref[item["comment_occurrence_ref"]][
                        "occurred_at"
                    ],
                    item["task_id"],
                    item["direct_interaction_ref"],
                )
            )
        object.__setattr__(self, "_interactions_by_actor", interactions_by_actor)

    def snapshot_for_task(self, task_id: str) -> dict[str, Any]:
        try:
            return self._snapshots_by_task[task_id]
        except KeyError as exc:
            raise KeyError(f"task is outside the authorized Account corpus: {task_id}") from exc

    def account(self, ref: str) -> dict[str, Any]:
        try:
            return self._accounts_by_ref[ref]
        except KeyError as exc:
            raise KeyError(f"unknown Account reference: {ref}") from exc

    def occurrence(self, ref: str) -> dict[str, Any]:
        try:
            return self._occurrences_by_ref[ref]
        except KeyError as exc:
            raise KeyError(f"unknown Account occurrence reference: {ref}") from exc

    def occurrences_for(self, ref: str) -> tuple[dict[str, Any], ...]:
        self.account(ref)
        return tuple(self._occurrences_by_account.get(ref, ()))

    def aliases_for(self, ref: str) -> tuple[dict[str, Any], ...]:
        self.account(ref)
        return tuple(self._aliases_by_account.get(ref, ()))

    def interactions_for(self, ref: str) -> tuple[dict[str, Any], ...]:
        self.account(ref)
        return tuple(self._interactions_by_actor.get(ref, ()))

    def direct_interaction(self, ref: str) -> dict[str, Any]:
        try:
            return self._interactions_by_ref[ref]
        except KeyError as exc:
            raise KeyError(f"unknown DirectInteraction reference: {ref}") from exc

    def ref_for_source_account(
        self, *, platform: str, source_namespace: str, source_account_key: str
    ) -> str:
        ref = account_ref(
            platform=platform,
            source_namespace=source_namespace,
            source_account_key=source_account_key,
        )
        self.account(ref)
        return ref

    @property
    def fixture_path(self) -> Path:
        return self._fixture_path
