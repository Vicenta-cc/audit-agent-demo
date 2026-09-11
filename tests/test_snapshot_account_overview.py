from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.reporting.account_overview import ReportAccountOverviewProjector
from backend.reporting.errors import ReportValidationError
from backend.reporting.presentation_projection import _account_collections
from hermes_m0.account_corpus import account_ref
from hermes_m0.pass_support import SnapshotAccountData, SnapshotAccountRepository


def snapshot():
    def comment(cid, author, risk="none", status="completed"):
        return dict(comment_id=cid, sec_uid=author, nickname="同名账号", content="文本",
                    create_time="2026-09-01T00:00:00Z", audit_status=status,
                    risk_level=risk, risk_score=0, risk_type="")

    posts = []
    findings = []
    for key, author, source, comments in (
        ("1", "publisher-a", "source-1", [comment("c1", "reader"), comment("c2", "reader", "high")]),
        ("2", "publisher-a", "source-2", [comment("c3", "reader"), comment("c4", "other", status="failed")]),
        ("3", "publisher-b", "source-2", [comment("c5", "publisher-a")]),
    ):
        posts.append(SimpleNamespace(ref="post:" + key, canonical_key=key, payload={
            "platform": "dy", "published_at": "2026-08-01T00:00:00Z", "title": "帖子",
            "raw_content_payload": {"author": {"sec_uid": author, "nickname": "同名账号"}},
            "comments": comments, "source_provenance": {"source_job_id": source},
        }))
        findings.append(SimpleNamespace(post_ref="post:" + key, payload={
            "decision": "review" if key == "1" else "pass", "risk_level": "medium" if key == "1" else "none",
        }))
    return SimpleNamespace(task_id="report-c", display_name="C", snapshot_hash="a" * 64,
                           snapshot_ref="frozen-c", posts=posts, findings=findings)


def test_keyword_publishers_keep_both_roles_without_inventing_a_target():
    source = snapshot()
    projection = ReportAccountOverviewProjector.from_snapshot(source).build(
        source, target_account_identity=None, require_target=False,
    )
    assert projection["statistics"] == dict(target_account_count=0, post_author_account_count=2,
        comment_author_account_count=2, distinct_account_count=3,
        default_active_comment_account_count=2, full_account_index_available=True)
    entries = projection["entries"]
    groups = _account_collections(projection, account_repository=None, current_task_id="report-c")
    assert [e["current_investigation_statistics"]["published_post_count"] for e in groups["other_post_authors"]] == [2, 1]
    publisher = groups["other_post_authors"][0]
    assert publisher["roles"] == ["post_author", "comment_author"]
    assert publisher["current_investigation_statistics"]["comment_count"] == 1
    assert publisher["current_investigation_statistics"]["risk_comment_count"] == 0
    reader = next(e for e in entries if e["roles"] == ["comment_author"])
    assert reader["current_investigation_statistics"]["comment_count"] == 3
    assert reader["current_investigation_statistics"]["risk_comment_count"] == 1
    assert reader["current_investigation_statistics"]["commented_post_count"] == 2
    assert len({e["internal_account_ref"] for e in entries}) == 3
    assert len({e["display_name"] for e in entries}) == 1


def test_creator_target_stays_required_when_requested_and_must_be_a_publisher():
    source = snapshot()
    projector = ReportAccountOverviewProjector.from_snapshot(source)
    with pytest.raises(ReportValidationError, match="no explicit target"):
        projector.build(source, target_account_identity=None)
    target = dict(platform="douyin", source_namespace="douyin.sec_uid", source_account_key="reader")
    with pytest.raises(ReportValidationError, match="not a frozen Post author"):
        projector.build(source, target_account_identity=target)
    target["source_account_key"] = "publisher-a"
    result = projector.build(source, target_account_identity=target)
    assert result["statistics"]["target_account_count"] == 1
    assert result["statistics"]["default_active_comment_account_count"] == 1


def test_snapshot_identity_mismatch_is_rejected_and_sources_stay_one_investigation():
    source = snapshot()
    projector = ReportAccountOverviewProjector.from_snapshot(source)
    changed = deepcopy(source)
    changed.posts[0].payload["comments"][0]["sec_uid"] = "another"
    with pytest.raises(ReportValidationError, match="mismatched identity"):
        projector.build(changed, target_account_identity=None, require_target=False)
    reader = account_ref(platform="douyin", source_namespace="douyin.sec_uid", source_account_key="reader")
    repo = SnapshotAccountRepository(SnapshotAccountData.from_snapshot(source))
    overview = repo.overview(reader)
    assert overview["activity_source_count"] == 1
    assert overview["authorized_task_count"] == 1
    assert overview["statistics"]["comment_count"] == 3


def test_publisher_drawer_pages_and_searches_publishers_without_commenters():
    from backend.reporting.presentation_projection import build_filtered_account_index_page
    source = snapshot()
    projection = ReportAccountOverviewProjector.from_snapshot(source).build(
        source, target_account_identity=None, require_target=False,
    )
    def page(**kwargs):
        return build_filtered_account_index_page(projection, account_repository=None,
            current_task_id=source.task_id, account_filter="post_author", limit=1, cursor=kwargs.pop("cursor", None), **kwargs)
    first = page()
    assert first["total_count"] == 2
    assert first["entries"][0]["statistics"]["published_post_count"] == 2
    second = page(cursor=first["next_cursor"])
    assert second["entries"][0]["statistics"]["published_post_count"] == 1
    assert not second["has_more"]
    assert first["entries"][0]["entry_ref"] != second["entries"][0]["entry_ref"]
    assert page(search="不存在的昵称")["total_count"] == 0
    assert page(sort_order="risk_published_post_count")["entries"][0]["statistics"]["risk_published_post_count"] == 1
