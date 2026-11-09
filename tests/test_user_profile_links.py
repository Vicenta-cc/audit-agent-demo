from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from scripts.backfill_related_account_profiles import backfill_related_account_profiles
from backend.audit_agent.ingestion import AuditResultStore
from backend.audit_agent.job_store import JobStore
from backend.investigation_creation.principal import Principal


def _parent(*comments: dict) -> dict:
    return {
        "id": 41,
        "audit_result_id": 41,
        "job_id": "parent-job",
        "platform": "dy",
        "author": {
            "nickname": "父作者",
            "user_unique_id": "parent-public",
            "sec_uid": "parent/sec",
        },
        "comments": list(comments),
    }


def _relation(comment_id: str, **extra) -> dict:
    return {
        "source": "comment_user_analysis",
        "parent_audit_result_id": 41,
        "source_comment_id": comment_id,
        **extra,
    }


def test_monitored_and_related_users_publish_source_owned_douyin_identity(tmp_path):
    store = JobStore(tmp_path / "identity.sqlite3")
    main = store.upsert_monitored_user_from_audit_result(
        _parent(), owner_user_id="owner-a"
    )
    assert main["public_douyin_id"] == "parent-public"
    assert main["sec_uid"] == "parent/sec"
    assert main["profile_url"] == "https://www.douyin.com/user/parent%2Fsec"

    parent = _parent(
        {
            "comment_id": "comment-a",
            "nickname": "同名用户",
            "user_unique_id": "public-a",
            "sec_uid": "sec/a",
            "content": "A",
        },
        {
            "comment_id": "comment-b",
            "nickname": "同名用户",
            "user_unique_id": "public-b",
            "sec_uid": "sec/b",
            "content": "B",
        },
        {
            "comment_id": "comment-public-only",
            "nickname": "只有公开号",
            "user_unique_id": "public-only",
        },
        {"comment_id": "comment-name-only", "nickname": "只有昵称"},
        {"comment_id": "comment-name-only-2", "nickname": "只有昵称"},
    )
    for comment_id in (
        "comment-a",
        "comment-b",
        "comment-public-only",
        "comment-name-only",
        "comment-name-only-2",
    ):
        store.upsert_comment_user_relation(
            analysis_job_id="",
            relation_context=_relation(
                comment_id,
                suspect_profile_url="https://www.douyin.com/user/wrong-user",
                suspect_raw_identity="wrong-user",
                suspect_display_name="伪造名称",
            ),
            parent_audit_result=parent,
            owner_user_id="owner-a",
        )

    account = store.list_monitored_users(owner_user_id="owner-a")[0]
    related = {item["source_comment_id"]: item for item in account["suspected_related_accounts"]}
    assert related["comment-a"]["public_douyin_id"] == "public-a"
    assert related["comment-a"]["profile_url"] == "https://www.douyin.com/user/sec%2Fa"
    assert related["comment-b"]["public_douyin_id"] == "public-b"
    assert related["comment-b"]["profile_url"] == "https://www.douyin.com/user/sec%2Fb"
    assert related["comment-a"]["id"] != related["comment-b"]["id"]
    assert related["comment-public-only"]["public_douyin_id"] == "public-only"
    assert related["comment-public-only"]["profile_url"] == ""
    assert related["comment-name-only"]["profile_url"] == ""
    assert related["comment-name-only"]["public_douyin_id"] == ""
    assert related["comment-name-only"]["id"] != related["comment-name-only-2"]["id"]


def test_readding_legacy_source_preserves_monitored_user_and_relation_ids(tmp_path):
    store = JobStore(tmp_path / "legacy.sqlite3")
    parent = _parent({"comment_id": "reply", "sec_uid": "comment-sec", "nickname": "同名"})
    def link():
        return store.upsert_comment_user_relation(
            analysis_job_id="", relation_context=_relation("reply"), parent_audit_result=parent,
            owner_user_id="owner-a",
        )

    before = link()
    with store._connect() as conn:
        conn.execute(
            "UPDATE monitored_users SET stable_key = ?, profile_url = '' WHERE id = ?",
            ("id:parent/sec", before["monitored_user_id"]),
        )
        conn.execute(
            "UPDATE suspected_related_accounts SET stable_key = 'id:同名', sec_uid = NULL, "
            "profile_url = '' WHERE id = ?", (before["related_account_id"],),
        )
    assert store.upsert_monitored_user_from_audit_result(parent, owner_user_id="owner-a")["id"] == before["monitored_user_id"]
    assert link() == before
    assert link() == before
    [account] = store.list_monitored_users(owner_user_id="owner-a")
    [related] = account["suspected_related_accounts"]
    assert related["profile_url"] == "https://www.douyin.com/user/comment-sec"


def test_comment_relation_requires_the_exact_source_comment(tmp_path):
    store = JobStore(tmp_path / "identity.sqlite3")
    try:
        store.upsert_comment_user_relation(
            analysis_job_id="",
            relation_context=_relation("missing"),
            parent_audit_result=_parent(),
            owner_user_id="owner-a",
        )
    except ValueError as exc:
        assert str(exc) == "source comment was not found in parent audit result"
    else:
        raise AssertionError("missing source comment must not create a relation")


def _insert_legacy_relation(
    store: JobStore,
    *,
    account_id: str,
    relation_id: str,
    profile_url: str = "",
    source_comment_id: str = "",
    source_audit_result_id: int | None = None,
) -> None:
    now = "2026-11-09T00:00:00"
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO suspected_related_accounts (
                id, owner_user_id, monitored_user_id, platform, stable_key,
                display_name, profile_url, raw_identity, source_comment_id,
                source_audit_result_id, analysis_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                relation_id,
                "owner-a",
                account_id,
                "dy",
                f"legacy:{relation_id}",
                relation_id,
                profile_url,
                relation_id,
                source_comment_id,
                source_audit_result_id,
                "linked",
                now,
                now,
            ),
        )


def test_legacy_related_urls_accept_only_official_douyin_profiles(tmp_path):
    store = JobStore(tmp_path / "identity.sqlite3")
    account = store.upsert_monitored_user_from_audit_result(
        _parent(), owner_user_id="owner-a"
    )
    _insert_legacy_relation(
        store,
        account_id=account["id"],
        relation_id="legacy-official",
        profile_url="https://www.douyin.com/user/legacy-sec",
    )
    _insert_legacy_relation(
        store,
        account_id=account["id"],
        relation_id="legacy-unsafe",
        profile_url="https://example.com/user/wrong",
    )

    related = {
        item["id"]: item
        for item in store.list_monitored_users(owner_user_id="owner-a")[0][
            "suspected_related_accounts"
        ]
    }
    assert related["legacy-official"]["profile_url"] == (
        "https://www.douyin.com/user/legacy-sec"
    )
    assert related["legacy-unsafe"]["profile_url"] == ""


def test_monitored_user_accepts_only_official_douyin_profile_url_without_sec_uid(tmp_path):
    store = JobStore(tmp_path / "identity.sqlite3")
    parent = _parent()
    parent["author"].pop("sec_uid")
    parent["author"]["profile_url"] = "https://www.douyin.com/user/unverified"
    account = store.upsert_monitored_user_from_audit_result(
        parent, owner_user_id="owner-a"
    )
    assert account["public_douyin_id"] == "parent-public"
    assert account["profile_url"] == "https://www.douyin.com/user/unverified"
    with store._connect() as conn:
        conn.execute("UPDATE monitored_users SET stable_key = 'id:parent-public' WHERE id = ?", (account["id"],))
    assert store.upsert_monitored_user_from_audit_result(parent, owner_user_id="owner-a")["id"] == account["id"]

    parent["author"]["profile_url"] = "https://example.com/user/unverified"
    unsafe = store.upsert_monitored_user_from_audit_result(
        parent, owner_user_id="owner-b"
    )
    assert unsafe["profile_url"] == ""


def test_legacy_relation_backfills_identity_from_exact_source_comment(tmp_path):
    path = tmp_path / "identity.sqlite3"
    store = JobStore(path)
    results = AuditResultStore(path)
    store.create(job_id="source-job", owner_user_id="owner-a", platform="dy")
    audit = results.upsert_result(
        job_id="source-job",
        platform="dy",
        content_key="source-post",
        result={
            "comments": [
                {
                    "comment_id": "source-comment",
                    "user_unique_id": "backfilled-public",
                    "sec_uid": "backfilled/sec",
                }
            ]
        },
    )
    account = store.upsert_monitored_user_from_audit_result(
        _parent(), owner_user_id="owner-a"
    )
    _insert_legacy_relation(
        store,
        account_id=account["id"],
        relation_id="legacy-backfill",
        source_comment_id="source-comment",
        source_audit_result_id=audit["id"],
    )

    # Cross-owner and cross-platform pointers cannot supply a relation identity.
    for mismatch, owner, platform in (("owner", "owner-b", "dy"), ("platform", "owner-a", "xhs")):
        job_id = "mismatch-" + mismatch
        store.create(job_id=job_id, owner_user_id=owner, platform=platform)
        other = results.upsert_result(job_id=job_id, platform=platform, content_key="other-post",
            result={"comments": [{"comment_id": "source-comment", "sec_uid": "wrong-user"}]})
        _insert_legacy_relation(store, account_id=account["id"], relation_id=job_id,
            source_comment_id="source-comment", source_audit_result_id=other["id"])
    assert backfill_related_account_profiles(path)["updated_relations"] == 1
    assert all(not row["sec_uid"] for row in
        store.list_monitored_users(owner_user_id="owner-a")[0]["suspected_related_accounts"])
    report = backfill_related_account_profiles(path, apply=True)
    assert Path(report["backup"]).is_file()
    assert report["updated_relations"] == 1

    assert backfill_related_account_profiles(path)["updated_relations"] == 0
    related = next(row for row in store.list_monitored_users(owner_user_id="owner-a")[0]["suspected_related_accounts"] if row["id"] == "legacy-backfill")
    assert related["public_douyin_id"] == "backfilled-public"
    assert related["sec_uid"] == "backfilled/sec"
    assert related["profile_url"] == "https://www.douyin.com/user/backfilled%2Fsec"


def test_comment_risk_user_profiles_are_authorized_and_keep_same_names_separate(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    jobs = JobStore(path)
    results = AuditResultStore(path)
    jobs.create(job_id="job-a", owner_user_id="owner-a", platform="dy")
    jobs.create(job_id="job-b", owner_user_id="owner-b", platform="dy")
    results.upsert_result(
        job_id="job-a",
        platform="dy",
        content_key="post-a",
        result={
            "note_id": "post-a",
            "title": "来源 A",
            "decision": "review",
            "risk_level": "medium",
            "comments": [
                {
                    "comment_id": "a-1",
                    "nickname": "同名用户",
                    "user_unique_id": "public-a",
                    "sec_uid": "sec/a",
                    "content": "高危评论",
                    "risk_level": "high",
                    "risk_score": 91,
                },
                {
                    "comment_id": "b-1",
                    "nickname": "同名用户",
                    "user_unique_id": "public-b",
                    "sec_uid": "sec/b",
                    "content": "中危评论",
                    "risk_level": "medium",
                },
                {
                    "comment_id": "b-2",
                    "nickname": "同名用户",
                    "user_unique_id": "public-b",
                    "sec_uid": "sec/b",
                    "content": "待复核评论",
                    "risk_level": "low",
                },
                {
                    "comment_id": "public-only-1",
                    "nickname": "只有公开号",
                    "user_unique_id": "public-only",
                    "content": "高危评论",
                    "risk_level": "high",
                },
                {
                    "comment_id": "name-only-1",
                    "nickname": "只有昵称",
                    "content": "高危评论",
                    "risk_level": "high",
                },
                {
                    "comment_id": "typed-identity-sec",
                    "nickname": "稳定标识用户",
                    "user_unique_id": "sec-owner-public",
                    "sec_uid": "same-text",
                    "content": "高危评论",
                    "risk_level": "high",
                },
                {
                    "comment_id": "typed-identity-public",
                    "nickname": "公开号用户",
                    "user_unique_id": "same-text",
                    "content": "高危评论",
                    "risk_level": "high",
                },
            ],
        },
    )
    results.upsert_result(
        job_id="job-b",
        platform="dy",
        content_key="post-b",
        result={
            "note_id": "post-b",
            "decision": "reject",
            "risk_level": "high",
            "comments": [
                {
                    "comment_id": "other-owner",
                    "nickname": "不可见用户",
                    "sec_uid": "other-owner-sec",
                    "risk_level": "high",
                }
            ],
        },
    )

    profiles = results.list_comment_risk_users(owner_user_id="owner-a")
    by_name_and_id = {
        (item["display_name"], item["public_douyin_id"]): item
        for item in profiles
    }
    assert ("不可见用户", "") not in by_name_and_id
    assert by_name_and_id[("同名用户", "public-a")]["profile_url"] == (
        "https://www.douyin.com/user/sec%2Fa"
    )
    assert by_name_and_id[("同名用户", "public-b")]["profile_url"] == (
        "https://www.douyin.com/user/sec%2Fb"
    )
    assert by_name_and_id[("同名用户", "public-b")]["pending_review_count"] == 1
    assert by_name_and_id[("只有公开号", "public-only")]["profile_url"] == ""
    assert by_name_and_id[("只有昵称", "")]["profile_url"] == ""
    assert by_name_and_id[("稳定标识用户", "sec-owner-public")]["sec_uid"] == "same-text"
    assert by_name_and_id[("公开号用户", "same-text")]["sec_uid"] == ""


def test_comment_risk_users_only_include_proven_recent_comments(tmp_path):
    path = tmp_path / "recent-profiles.sqlite3"
    jobs = JobStore(path)
    results = AuditResultStore(path)
    jobs.create(job_id="job-a", owner_user_id="owner-a", platform="dy")
    now = datetime.now(timezone.utc)

    results.upsert_result(
        job_id="job-a",
        platform="dy",
        content_key="dated-comments",
        result={
            "comments": [
                {
                    "comment_id": "expired-high",
                    "nickname": "过期高危用户",
                    "sec_uid": "expired-sec",
                    "risk_level": "high",
                    "create_time": int((now - timedelta(days=31)).timestamp()),
                },
                {
                    "comment_id": "recent-high",
                    "nickname": "近期高危用户",
                    "sec_uid": "recent-sec",
                    "risk_level": "high",
                    "created_at": (now - timedelta(days=2)).isoformat(),
                },
            ]
        },
    )
    results.upsert_result(
        job_id="job-a",
        platform="dy",
        content_key="unknown-time",
        result={
            "comments": [
                {
                    "comment_id": "unknown-high",
                    "nickname": "时间未知用户",
                    "sec_uid": "unknown-sec",
                    "risk_level": "high",
                    "created_at": "无法解析",
                }
            ]
        },
    )
    with results._connect() as conn:
        conn.execute(
            "UPDATE audit_results SET analyzed_at = ?, updated_at = ? WHERE content_key = ?",
            ("无法解析", "仍无法解析", "unknown-time"),
        )

    profiles = results.list_comment_risk_users(owner_user_id="owner-a")
    names = {item["display_name"] for item in profiles}
    assert "近期高危用户" in names
    assert "过期高危用户" not in names
    # No parseable comment or audit time means recency cannot be proven.
    assert "时间未知用户" not in names


def test_comment_risk_user_api_uses_authorized_scope(monkeypatch):
    from backend import main

    calls = []

    class FakeStore:
        def list_comment_risk_users(self, **scope):
            calls.append(scope)
            return [
                {
                    "id": "dy:sec-user",
                    "platform": "dy",
                    "display_name": "评论用户",
                    "public_douyin_id": "public-user",
                    "sec_uid": "sec-user",
                    "profile_url": "https://www.douyin.com/user/sec-user",
                    "violation_count": 1,
                    "high_risk_count": 1,
                    "comments": [],
                }
            ]

    monkeypatch.setattr(main, "audit_result_store", FakeStore())
    monkeypatch.setattr(
        main,
        "_job_list_scope",
        lambda principal: (principal.id, ("shared-job",)),
    )
    main.app.dependency_overrides[main.principal_provider] = lambda: Principal("owner-a")
    try:
        response = TestClient(main.app).get("/api/comment-risk-users")
    finally:
        main.app.dependency_overrides.pop(main.principal_provider, None)

    assert response.status_code == 200
    assert response.json()["items"][0]["public_douyin_id"] == "public-user"
    assert calls == [
        {"owner_user_id": "owner-a", "granted_job_ids": ("shared-job",)}
    ]



def test_comment_risk_pages_include_granted_jobs_and_exclude_archived(tmp_path):
    path = tmp_path / "paged-profiles.sqlite3"
    jobs = JobStore(path)
    results = AuditResultStore(path)
    for key, owner in [("mine", "a"), ("granted", "b"), ("archived", "a"), ("private", "b")]:
        jobs.create(job_id=key, owner_user_id=owner, platform="dy")
    for i, key in enumerate(["mine"] * 17 + ["granted", "archived", "private"]):
        results.upsert_result(
            job_id=key, platform="dy", content_key=str(i),
            result={"comments": [{"risk_level": "high", "sec_uid": str(i), "comment_id": str(i)}]},
        )
    jobs.archive("archived")
    items = results.list_comment_risk_users(owner_user_id="a", granted_job_ids=("granted", "archived"))
    assert {i["sec_uid"] for i in items} == {str(i) for i in range(18)}
