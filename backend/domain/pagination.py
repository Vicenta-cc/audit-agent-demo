from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class FindingSort(str, Enum):
    RISK_SCORE = "risk_score"
    CREATED_AT = "created_at"
    AUDIT_RESULT_ID = "audit_result_id"


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


class Pagination(BaseModel):
    model_config = ConfigDict(frozen=True)

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    sort_by: FindingSort = FindingSort.AUDIT_RESULT_ID
    sort_order: SortOrder = SortOrder.ASC

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size
