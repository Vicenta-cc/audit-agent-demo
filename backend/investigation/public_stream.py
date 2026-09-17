from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


PUBLIC_STREAM_EVENT_TYPES = frozenset(
    {"activity", "answer_delta", "answer_reset"}
)


class PublicStreamPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PublicActivityPayload(PublicStreamPayload):
    activity_id: str = Field(
        min_length=32,
        max_length=80,
        pattern=r"^public-activity:[0-9a-f]{16,64}$",
    )
    status: Literal["running", "succeeded", "failed", "interrupted"]
    label: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=500)
    result_count: int | None = Field(default=None, ge=0)


class PublicAnswerDeltaPayload(PublicStreamPayload):
    message_id: str = Field(
        min_length=30,
        max_length=78,
        pattern=r"^public-answer:[0-9a-f]{16,64}$",
    )
    revision: int = Field(ge=1)
    delta: str = Field(min_length=1, max_length=4_096)


class PublicAnswerResetPayload(PublicStreamPayload):
    message_id: str = Field(
        min_length=30,
        max_length=78,
        pattern=r"^public-answer:[0-9a-f]{16,64}$",
    )
    revision: int = Field(ge=1)


PUBLIC_STREAM_PAYLOAD_MODELS = {
    "activity": PublicActivityPayload,
    "answer_delta": PublicAnswerDeltaPayload,
    "answer_reset": PublicAnswerResetPayload,
}


def normalize_public_stream_payload(
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    normalized_type = str(event_type or "").strip()
    model = PUBLIC_STREAM_PAYLOAD_MODELS.get(normalized_type)
    if model is None:
        raise ValueError("invalid public stream event type")
    return model.model_validate(payload).model_dump(mode="json")
