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
    """Strict extraction contract.

    Critical fields may be null when the source cannot support a reliable value.
    The deterministic validator decides whether the record can be auto-accepted.
    Model self-confidence is intentionally excluded.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    merchant: str | None = None
    transaction_date: date | None = None
    transaction_time: time | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    tip: Decimal | None = None
    fees: Decimal | None = None
    discount: Decimal | None = None
    total: Decimal | None = None
    currency: str | None = None
    transaction_type: TransactionType = TransactionType.PURCHASE
    line_items: list[LineItem] = Field(default_factory=list)

    @field_validator("merchant")
    @classmethod
    def strip_merchant(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = str(value).strip().upper()
        return stripped or None
