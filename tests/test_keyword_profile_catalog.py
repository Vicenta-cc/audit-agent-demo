"""Catalog wiring tests: no live model, resource or service mutations."""
import hashlib
import json
from pathlib import Path
import re
from typing import get_args

import pytest

from backend.resource_management.keyword_profiles import (
    PROFILES, KeywordProfileName, keyword_profile_metadata,
    KEYWORD_PROFILE_ROUTING_SUMMARIES, KEYWORD_PROFILE_CATALOG_DESCRIPTION,
    KEYWORD_PROFILE_TITLES,
)
from backend.resource_management.keyword_profile_catalog import MARKDOWN_PROFILES
from backend.resource_management.generation_contracts import LexiconGenerationRequest
from backend.resource_management.generation import generation_messages
from backend.resource_management.contracts import ResourceError
from test_resource_generation import generator, REQUEST
from backend.resource_management.tools import RESOURCE_DESCRIPTIONS

DOC = Path(__file__).resolve().parents[1] / "docs/search-keyword-prompts-candidate-20260926.md"
EXISTING_HASHES = {
    "sexual_service_leadgen": "a3122cf2b3a281f623cbab14acd93ecd948d188b893a59ebe838dd3234f50ee8",
    "gambling_financial_abuse": "c25959988a7af0dc3059917c35d54dd368753fb61671d87ef9e79d0c9b518534",
    "religion_content_risk": "db500f8808b2027140a2f96c540406f8a3e99fbcd10181f9d8e7b50cb1143af6",
}


def test_all_19_topics_registered_without_replacing_existing_three():
    assert len(PROFILES) == 22
    assert set(get_args(KeywordProfileName)) == set(PROFILES)
    assert set(MARKDOWN_PROFILES).isdisjoint(EXISTING_HASHES)
    assert {x["number"] for x in MARKDOWN_PROFILES.values()} == {
        f"{n:02}" for n in range(1, 22) if n not in (8, 16)
    }
    for name, expected in EXISTING_HASHES.items():
        assert hashlib.sha256(PROFILES[name].guidance.encode()).hexdigest() == expected


def test_routing_scopes_are_shared_by_tool_and_argument_schema():
    assert set(KEYWORD_PROFILE_ROUTING_SUMMARIES) == set(PROFILES)
    assert set(KEYWORD_PROFILE_TITLES) == set(PROFILES)
    field = LexiconGenerationRequest.model_json_schema()['properties']['keyword_profile']
    assert field['description'] == KEYWORD_PROFILE_CATALOG_DESCRIPTION
    assert KEYWORD_PROFILE_CATALOG_DESCRIPTION in RESOURCE_DESCRIPTIONS['create_lexicon_edit']
    for name, summary in KEYWORD_PROFILE_ROUTING_SUMMARIES.items():
        assert summary.strip()
        title = KEYWORD_PROFILE_TITLES[name]
        assert title.strip()
        assert f'{name}｜类别标题：{title}｜适用范围：{summary}' in field['description']
    for name, item in MARKDOWN_PROFILES.items():
        assert KEYWORD_PROFILE_TITLES[name] == item['title']


def test_route_index_is_not_injected_into_dedicated_author():
    request = LexiconGenerationRequest(**REQUEST, keyword_profile='public_policy_content')
    messages = generation_messages('lexicon', request)
    serialized = json.dumps(messages, ensure_ascii=False)
    assert KEYWORD_PROFILE_CATALOG_DESCRIPTION not in serialized
    payload = json.loads(messages[1]['content'])
    assert PROFILES['public_policy_content'].guidance in payload['requirements']
    assert PROFILES['traffic_privacy_piracy'].guidance not in payload['requirements']


def test_overlapping_religious_and_efficacy_scopes_explain_selection_boundary():
    religion = KEYWORD_PROFILE_ROUTING_SUMMARIES['religion_content_risk']
    efficacy = KEYWORD_PROFILE_ROUTING_SUMMARIES['false_efficacy_fraud']
    assert '优先本项而不是 false_efficacy_fraud' in religion
    assert '收费或胁迫招募时用 religion_content_risk' in efficacy
    assert '未限定宗教的改运、养生及传统文化功效骗局用本项' in efficacy


@pytest.mark.parametrize("name", MARKDOWN_PROFILES)
def test_new_guidance_is_verbatim_markdown_and_has_version(name):
    sections = re.findall(
        r"### Prompt (\d{2})：([^\n]+)\n[\s\S]*?```text\n([\s\S]*?)\n```",
        DOC.read_text(encoding="utf-8"),
    )
    assert len(sections) == 21
    blocks = {number: (title, body) for number, title, body in sections}
    item = MARKDOWN_PROFILES[name]
    assert (item["title"], item["guidance"]) == blocks[item["number"]]
    assert PROFILES[name].guidance == item["guidance"]
    assert PROFILES[name].version == item["version"] == "1"


def test_sample_based_profile_is_registered_and_uses_same_validation():
    name = "terrorism_content_samples"
    request = LexiconGenerationRequest(**REQUEST, keyword_profile=name)
    payload = json.loads(generation_messages("lexicon", request)[1]["content"])
    assert PROFILES[name].guidance in payload["requirements"]
    assert keyword_profile_metadata(request)["applied_profile"] == name
    gen, client, _ = generator(payload={"title": "无可用候选", "themes": []})
    with pytest.raises(ResourceError) as error:
        gen.generate("lexicon", request)
    assert error.value.code == "RESOURCE_GENERATION_INVALID"
    assert client.chat.completions.create.call_count == 1
