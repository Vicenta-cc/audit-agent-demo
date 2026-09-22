from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from backend.investigation_creation.resource_authoring import (
    CompanyResourceAuthoringClient,
    ResourceAuthoringError,
    resource_generation_kinds,
)
from backend.resource_management.contracts import LexiconContent


RULESET_CONTENT = {
    "schema_version": 0,
    "name": "测试审核规则",
    "domain": "内容安全",
    "audit_goal": "识别公开内容中的风险表达，同时保护正常讨论。",
    "general_exemptions": [],
    "categories": [
        {
            "category_id": "risk_expression",
            "name": "风险表达",
            "description": "识别明确风险表达。",
            "order": 1,
            "rules": [
                {
                    "rule_id": "explicit_risk",
                    "name": "明确风险表达",
                    "hit_condition": "内容明确表达风险意图时命中。",
                    "suggested_risk_level": "high",
                    "rule_exemptions": [],
                    "application_stages": ["fusion_audit"],
                    "adjudication_notes": "仅在证据明确表达风险意图时判定。",
                    "enabled": True,
                    "order": 1,
                    "source_mappings": [],
                }
            ],
        }
    ],
}


LEXICON_CONTENT = {
    "title": "测试黑话库",
    "risk_label": "测试风险",
    "description": "用于验证主题主词与实际搜索变体。",
    "entries": [
        {
            "id": "theme:test-risk",
            "term": "测试风险主题",
            "kind": "main",
            "parent_id": "",
            "enabled": True,
            "platform": "全平台",
            "match_type": "黑话词",
            "risk_level": "中",
            "note": "主题主词",
        },
        {
            "id": "variant:test-risk-code",
            "term": "测试隐语",
            "kind": "variant",
            "parent_id": "theme:test-risk",
            "enabled": True,
            "platform": "全平台",
            "match_type": "黑话词",
            "risk_level": "中",
            "note": "实际搜索词",
        },
    ],
}


class StubResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        body: Any = None,
        json_error: ValueError | None = None,
    ) -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._body = body
        self._json_error = json_error

    def json(self) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._body


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("生成一套审核规则", ("ruleset",)),
        ("生成一套黑话库和关键词", ("lexicon",)),
        ("生成审核规则和黑话库", ("ruleset", "lexicon")),
        ("修改刚生成的关键词", ()),
        ("保存刚生成的黑话库", ()),
        ("采用这套审核规则", ()),
        ("生成一份调查报告", ()),
    ],
)
def test_resource_generation_kinds_routes_only_explicit_new_resources(
    message: str,
    expected: tuple[str, ...],
) -> None:
    assert resource_generation_kinds(message) == expected


def test_company_resource_authoring_calls_responses_with_strict_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> StubResponse:
        captured.update({"url": url, **kwargs})
        return StubResponse(
            body={
                "status": "completed",
                "model": "company-test-model",
                "output_text": json.dumps(LEXICON_CONTENT, ensure_ascii=False),
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                },
            }
        )

    monkeypatch.setattr(
        "backend.investigation_creation.resource_authoring.requests.post", fake_post
    )
    client = CompanyResourceAuthoringClient(
        api_key="unit-test-token",
        base_url="https://company.example/v1/",
        model="company-test-model",
        reasoning_effort="high",
        timeout=9,
        max_output_tokens=3210,
    )

    result = client.generate("lexicon", user_request="生成一套测试黑话库")

    assert captured["url"] == "https://company.example/v1/responses"
    assert captured["timeout"] == 9
    assert captured["headers"]["Authorization"].startswith("Bearer ")
    payload = captured["json"]
    assert payload["model"] == "company-test-model"
    assert payload["store"] is False
    assert payload["max_output_tokens"] == 3210
    assert payload["reasoning"] == {"effort": "high"}
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    schema = payload["text"]["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert result.content == LEXICON_CONTENT
    assert result.usage == {
        "input_tokens": 10,
        "output_tokens": 20,
        "total_tokens": 30,
    }


def test_company_resource_authoring_omits_reasoning_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_post(_url: str, **kwargs: Any) -> StubResponse:
        captured.update(kwargs)
        return StubResponse(
            body={
                "status": "completed",
                "output_text": json.dumps(LEXICON_CONTENT, ensure_ascii=False),
            }
        )

    monkeypatch.setattr(
        "backend.investigation_creation.resource_authoring.requests.post", fake_post
    )
    client = CompanyResourceAuthoringClient(
        api_key="unit-test-token",
        base_url="https://company.example/v1",
        model="company-test-model",
        reasoning_effort="none",
    )

    client.generate("lexicon", user_request="生成一套测试黑话库")

    assert "reasoning" not in captured["json"]


def test_company_resource_authoring_parses_nested_output_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.investigation_creation.resource_authoring.requests.post",
        lambda *_args, **_kwargs: StubResponse(
            body={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(RULESET_CONTENT, ensure_ascii=False),
                            }
                        ],
                    }
                ],
            }
        ),
    )
    client = CompanyResourceAuthoringClient(
        api_key="unit-test-token",
        base_url="https://company.example/v1",
        model="company-test-model",
    )

    result = client.generate("ruleset", user_request="生成一套测试审核规则")

    assert result.content == RULESET_CONTENT


def test_company_resource_authoring_keeps_only_top_five_enabled_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = json.loads(json.dumps(LEXICON_CONTENT, ensure_ascii=False))
    parent_id = content["entries"][0]["id"]
    content["entries"][0]["enabled"] = False
    content["entries"] = [
        content["entries"][0],
        *[
            {
                **content["entries"][1],
                "id": f"variant:rank-{index}",
                "term": f"高价值搜索词{index}",
                "parent_id": parent_id,
            }
            for index in range(1, 8)
        ],
    ]
    monkeypatch.setattr(
        "backend.investigation_creation.resource_authoring.requests.post",
        lambda *_args, **_kwargs: StubResponse(
            body={
                "status": "completed",
                "output_text": json.dumps(content, ensure_ascii=False),
            }
        ),
    )
    client = CompanyResourceAuthoringClient(
        api_key="unit-test-token",
        base_url="https://company.example/v1",
        model="company-test-model",
    )

    result = client.generate("lexicon", user_request="生成高价值黑话库")

    assert [
        entry["term"]
        for entry in result.content["entries"]
        if entry["kind"] == "variant"
    ] == [f"高价值搜索词{index}" for index in range(1, 6)]
    assert result.content["entries"][0]["enabled"] is True
    assert CompanyResourceAuthoringClient._focus_lexicon_variants(
        LexiconContent.model_validate(content)
    ).search_terms() == [f"高价值搜索词{index}" for index in range(1, 6)]


@pytest.mark.parametrize(
    ("status_code", "expected_kind", "retryable"),
    [
        (401, "authentication", False),
        (429, "rate_limit", True),
        (500, "server", True),
        (400, "request", False),
    ],
)
def test_company_resource_authoring_classifies_http_errors(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_kind: str,
    retryable: bool,
) -> None:
    monkeypatch.setattr(
        "backend.investigation_creation.resource_authoring.requests.post",
        lambda *_args, **_kwargs: StubResponse(status_code=status_code, body={}),
    )
    client = CompanyResourceAuthoringClient(
        api_key="unit-test-token",
        base_url="https://company.example/v1",
        model="company-test-model",
    )

    with pytest.raises(ResourceAuthoringError) as captured:
        client.generate("lexicon", user_request="生成词库")

    assert captured.value.kind == expected_kind
    assert captured.value.retryable is retryable


def test_company_resource_authoring_classifies_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*_args: Any, **_kwargs: Any) -> StubResponse:
        raise requests.Timeout("unit test")

    monkeypatch.setattr(
        "backend.investigation_creation.resource_authoring.requests.post", timeout
    )
    client = CompanyResourceAuthoringClient(
        api_key="unit-test-token",
        base_url="https://company.example/v1",
        model="company-test-model",
    )

    with pytest.raises(ResourceAuthoringError) as captured:
        client.generate("lexicon", user_request="生成词库")

    assert captured.value.kind == "timeout"
    assert captured.value.retryable is True


@pytest.mark.parametrize(
    ("body", "expected_kind"),
    [
        (
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "refusal", "refusal": "no"}],
                    }
                ],
            },
            "refusal",
        ),
        ({"status": "completed", "output_text": "not-json"}, "schema_validation"),
        ({"status": "completed", "output": []}, "empty_response"),
    ],
)
def test_company_resource_authoring_classifies_invalid_outputs(
    monkeypatch: pytest.MonkeyPatch,
    body: dict[str, Any],
    expected_kind: str,
) -> None:
    monkeypatch.setattr(
        "backend.investigation_creation.resource_authoring.requests.post",
        lambda *_args, **_kwargs: StubResponse(body=body),
    )
    client = CompanyResourceAuthoringClient(
        api_key="unit-test-token",
        base_url="https://company.example/v1",
        model="company-test-model",
    )

    with pytest.raises(ResourceAuthoringError) as captured:
        client.generate("lexicon", user_request="生成词库")

    assert captured.value.kind == expected_kind
