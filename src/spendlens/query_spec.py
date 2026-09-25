from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QueryStatus(StrEnum):
    ANSWERABLE = "answerable"
    UNSUPPORTED = "unsupported"
    NEEDS_CLARIFICATION = "needs_clarification"


class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: Literal["total_spend", "transaction_count", "average_transaction"]
    start_date: date | None = None
    end_date: date | None = None
    categories: list[str] = Field(default_factory=list)
    merchants: list[str] = Field(default_factory=list)
    group_by: Literal["day", "week", "month", "category", "merchant"] | None = None


class QueryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: QueryStatus
    query: QuerySpec | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> "QueryDecision":
        if self.status == QueryStatus.ANSWERABLE and self.query is None:
            raise ValueError("answerable decisions require a query")
        if self.status != QueryStatus.ANSWERABLE and not self.reason:
            raise ValueError("unsupported or ambiguous decisions require a reason")
        return self
