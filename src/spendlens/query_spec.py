from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from spendlens.models import ItemCategory, ItemSubcategory


class QueryStatus(StrEnum):
    ANSWERABLE = "answerable"
    UNSUPPORTED = "unsupported"
    NEEDS_CLARIFICATION = "needs_clarification"


class QueryMetric(StrEnum):
    TOTAL_SPEND = "total_spend"
    TRANSACTION_COUNT = "transaction_count"
    AVERAGE_TRANSACTION = "average_transaction"
    ITEM_SPEND = "item_spend"
    ITEM_FREQUENCY = "item_frequency"
    UNUSUAL_ITEMS = "unusual_items"


class QueryGroupBy(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    CATEGORY = "category"
    SUBCATEGORY = "subcategory"
    MERCHANT = "merchant"
    ITEM = "item"


class QuerySpec(BaseModel):
    """Closed query language for deterministic SpendLens analytics."""

    model_config = ConfigDict(extra="forbid")

    metric: QueryMetric
    start_date: date | None = None
    end_date: date | None = None
    categories: list[ItemCategory] = Field(default_factory=list)
    subcategories: list[ItemSubcategory] = Field(default_factory=list)
    merchants: list[str] = Field(default_factory=list)
    item_terms: list[str] = Field(
        default_factory=list,
        description=(
            "Product-name terms to match against normalized/raw item names."
        ),
    )
    group_by: QueryGroupBy | None = None
    limit: int = Field(default=10, ge=1, le=20)
    unusual_max_prior_purchases: int = Field(default=1, ge=0, le=5)

    @model_validator(mode="after")
    def validate_dates(self) -> "QuerySpec":
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date > self.end_date
        ):
            raise ValueError("start_date cannot be after end_date")
        return self


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
            raise ValueError(
                "unsupported or ambiguous decisions require a reason"
            )
        return self
