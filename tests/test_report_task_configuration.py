from hermes_m0.real_report_repository import _project_task_configuration
from hermes_m0.report_task_service import ReportTaskInvestigationToolService
from types import SimpleNamespace


def test_report_task_configuration_projects_frozen_rules_and_recall_terms():
    projected = _project_task_configuration(
        {
            "resolved_search_terms": ["维汉夫妻"],
            "ruleset_revision": {"id": "ruleset-revision:ethnic:v1", "version": "1"},
            "source_jobs": [
                {
                    "id": "job-1",
                    "display_name": "调查任务",
                    "current_audit_config_revision_id": "audit-config:v1",
                    "lexicon_keywords": ["维汉夫妻"],
                }
            ],
        },
        configuration_revision_id="audit-config:v1",
    )

    assert projected["available"] is True
    assert projected["search_terms"] == ["维汉夫妻"]
    assert projected["ruleset"]["revision_ids"] == [
        "audit-config:v1",
        "ruleset-revision:ethnic:v1",
    ]
    assert projected["ruleset"]["versions"] == ["1"]


def test_report_task_configuration_does_not_invent_missing_provenance():
    projected = _project_task_configuration(
        None,
        configuration_revision_id="audit-config:legacy",
    )

    assert projected == {
        "available": False,
        "configuration_revision_id": "audit-config:legacy",
        "reason": "该发布快照没有保存可展示的任务配置。",
    }


def test_post_detail_links_stay_bound_to_the_same_public_post():
    service = ReportTaskInvestigationToolService.__new__(ReportTaskInvestigationToolService)
    service.repository = SimpleNamespace(
            fixture=SimpleNamespace(
                snapshot=SimpleNamespace(
                    posts=(SimpleNamespace(ref="post:201"),), findings=()
                ),
                posts=(SimpleNamespace(id="post:201"),),
                report_version=SimpleNamespace(id="report-version:test"),
            provenance=SimpleNamespace(source_task_id="task-1"),
        )
    )
    links = service._post_detail_links(
        "investigation-session:test",
        SimpleNamespace(id="post:201"),
        {"audit_result_id": 811},
    )

    assert links["detail_url"] == links["task_output_detail_url"]
    assert "/tasks/task-1/outputs/811" in links["detail_url"]
    assert "post-001" in links["detail_url"]


def test_post_detail_links_fall_back_when_task_output_is_not_available():
    service = ReportTaskInvestigationToolService.__new__(ReportTaskInvestigationToolService)
    service.repository = SimpleNamespace(
        fixture=SimpleNamespace(
            snapshot=SimpleNamespace(posts=(SimpleNamespace(ref="post:201"),), findings=()),
            posts=(SimpleNamespace(id="post:201"),),
            report_version=SimpleNamespace(id="report-version:test"),
            provenance=SimpleNamespace(source_task_id="report-c-synthetic"),
        )
    )
    links = service._post_detail_links(
        "investigation-session:test",
        SimpleNamespace(id="post:201"),
        {
            "audit_result_id": 811,
            "source_provenance": {
                "source_job_id": "report-c-synthetic",
                "task_output_available": False,
            },
        },
    )

    assert links["task_output_detail_url"] == ""
    assert links["detail_url"] == links["report_detail_url"]
