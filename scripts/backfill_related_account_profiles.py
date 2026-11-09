"""Fill legacy Douyin relation fields from the original, same-owner comment."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.audit_agent.config import settings
from backend.audit_agent.job_store import JobStore


def _source_comment(result_json: str, comment_id: str) -> dict:
    try:
        payload = json.loads(result_json or "{}")
    except (TypeError, ValueError):
        return {}
    comments = payload.get("comments") if isinstance(payload, dict) else None
    if not isinstance(comments, list):
        return {}
    matches = [
        c for c in comments if isinstance(c, dict)
        and str(c.get("comment_id") or c.get("id") or "").strip() == comment_id
    ]
    return matches[0] if len(matches) == 1 else {}


def _backup(conn: sqlite3.Connection, path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = path.with_name(f"{path.name}.profile-backfill-{stamp}.bak")
    os.close(os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    try:
        with closing(sqlite3.connect(backup)) as dest:
            conn.backup(dest)
            if dest.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise RuntimeError("backup integrity check failed")
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    return backup


def backfill_related_account_profiles(
    db_path: str | Path, *, apply: bool = False,
    owner_user_id: str | None = None, create_backup: bool = True,
) -> dict:
    path = Path(db_path).expanduser().resolve(strict=True)
    mode = "rw" if apply else "ro"
    with closing(sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        backup = _backup(conn, path) if apply and create_backup else None
        conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        rows = conn.execute(
            """
            SELECT r.id, r.public_douyin_id, r.sec_uid, r.profile_url,
                   r.source_comment_id, a.result_json
            FROM suspected_related_accounts r
            JOIN monitored_users m ON m.id = r.monitored_user_id
                AND m.owner_user_id = r.owner_user_id AND m.platform = 'dy'
            JOIN audit_results a ON a.id = r.source_audit_result_id AND a.platform = 'dy'
            JOIN jobs j ON j.id = a.job_id AND j.owner_user_id = r.owner_user_id
                AND j.platform = 'dy' AND j.archived = 0
            WHERE r.platform = 'dy' AND r.source_comment_id <> ''
              AND (COALESCE(r.source_job_id, '') = '' OR r.source_job_id = a.job_id)
              AND (? IS NULL OR r.owner_user_id = ?)
              AND (COALESCE(r.public_douyin_id, '') = '' OR COALESCE(r.sec_uid, '') = ''
                   OR COALESCE(r.profile_url, '') = '')
            """, (owner_user_id, owner_user_id),
        )
        updates = []
        candidates = missing = conflicts = 0
        for row in rows:
            candidates += 1
            comment = _source_comment(row["result_json"], row["source_comment_id"].strip())
            if not comment:
                missing += 1
                continue
            before = tuple(str(row[k] or "").strip() for k in ("public_douyin_id", "sec_uid", "profile_url"))
            source_sec = str(comment.get("sec_uid") or "").strip()
            if source_sec and before[1] and source_sec != before[1]:
                conflicts += 1
                continue
            public_id = JobStore._public_douyin_id(comment, "dy") or before[0]
            sec_uid = source_sec or before[1]
            source_profile = JobStore._first_text(
                *(comment.get(k) for k in ("profile_url", "homepage_url", "homepage", "user_url", "url"))
            )
            profile_url = (JobStore._douyin_profile_url(sec_uid)
                           or JobStore._verified_douyin_profile_url(source_profile)
                           or JobStore._verified_douyin_profile_url(before[2]))
            after = (public_id, sec_uid, profile_url)
            if after != before:
                updates.append((*after, row["id"]))
        if apply:
            conn.executemany(
                """UPDATE suspected_related_accounts SET public_douyin_id=?, sec_uid=?, profile_url=?
                   WHERE id=?""", updates,
            )
            conn.commit()
        return {"mode": "apply" if apply else "dry-run", "database": str(path),
                "candidate_relations": candidates, "updated_relations": len(updates),
                "missing_source_comments": missing, "conflicting_identities": conflicts,
                "backup": str(backup or "")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=settings.data_dir / "audit_index.sqlite3")
    parser.add_argument("--owner-user-id")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(backfill_related_account_profiles(
        args.db, apply=args.apply, owner_user_id=args.owner_user_id,
    ), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
