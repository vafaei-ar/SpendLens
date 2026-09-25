from datetime import date, time
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TransactionType(StrEnum):
    PURCHASE = "purchase"
    REFUND = "refund"
    RETURN = "return"


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description_raw: str = Field(min_length=1)
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal
    category: str | None = None


class ReceiptExtraction(BaseModel):
    """Strict model output contract. Model self-confidence is intentionally excluded."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    merchant: str = Field(min_length=1)
    transaction_date: date
    transaction_time: time | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    tip: Decimal | None = None
    fees: Decimal | None = None
    discount: Decimal | None = None
    total: Decimal
    currency: str = Field(min_length=3, max_length=3)
    transaction_type: TransactionType = TransactionType.PURCHASE
    line_items: list[LineItem] = Field(default_factory=list)

    @field_validator("merchant")
    @classmethod
    def strip_merchant(cls, value: str) -> str:
        return value.strip()

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return str(value).strip().upper()
