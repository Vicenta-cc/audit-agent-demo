"""Validated frozen request contracts shared by API and durable workers."""

from typing import Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt

from .limits import MAX_SUPPORTED_INVESTIGATION_POSTS


class CrawlRequest(BaseModel):
    platform: str = "xhs"
    display_name: str = ""
    crawl_mode: str = "search"
    keyword: str = "泳装"
    search_sort: Literal["general", "most_liked", "latest"] = "general"
    keyword_source: str = "keyword"
    lexicon_category: str = ""
    library_ids: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    scoring_template: str = "balanced"
    rule_snapshot: dict = Field(default_factory=dict)
    lexicon_keywords: list[str] = []
    creator_url: str = ""
    creator_id: str = ""
    start_page: StrictInt = Field(default=0, ge=0)
    max_notes: StrictInt = Field(
        default=5, ge=1, le=MAX_SUPPORTED_INVESTIGATION_POSTS
    )
    max_total_notes: StrictInt = Field(
        default=5, ge=1, le=MAX_SUPPORTED_INVESTIGATION_POSTS
    )
    max_comments: StrictInt = Field(default=100, ge=0, le=1000)
    max_concurrency: StrictInt = Field(default=1, ge=1, le=3)
    max_items_per_minute: StrictInt = Field(default=5, ge=1, le=5)
    crawler_account_id: str | None = None
    collect_comments: StrictBool = True
    get_sub_comment: StrictBool = False
    collect_media: StrictBool = True
    auto_analyze: StrictBool = True
    analyze_limit: StrictInt = Field(default=0, ge=0)
    run_crawler: StrictBool = True
    source_output_id: str | None = None
    analysis_batch_size: StrictInt = Field(default=5, ge=1, le=20)
    prompt_profile_snapshot: dict = Field(default_factory=dict)
    policy_id: str = ""
    relation_context: dict = Field(default_factory=dict)


def crawl_request_from_job(job: dict) -> CrawlRequest:
    """Rebuild the frozen crawl inputs when the same task is resumed."""
    model_fields = getattr(CrawlRequest, "model_fields", None) or getattr(
        CrawlRequest, "__fields__", {}
    )
    payload = {
        name: job[name]
        for name in model_fields
        if name in job and job[name] is not None
    }
    effective_config = dict(job.get("effective_config") or {})
    for name in ("collect_comments", "collect_media", "search_sort"):
        if name not in payload and name in effective_config:
            payload[name] = effective_config[name]
    return CrawlRequest(**payload)
