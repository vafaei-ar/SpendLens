from datetime import date, time
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TransactionType(StrEnum):
    PURCHASE = "purchase"
    REFUND = "refund"
    RETURN = "return"


class ItemCategory(StrEnum):
    GROCERIES = "groceries"
    HOUSEHOLD = "household"
    PERSONAL_CARE = "personal_care"
    HEALTH = "health"
    PET = "pet"
    CLOTHING = "clothing"
    ELECTRONICS = "electronics"
    ENTERTAINMENT = "entertainment"
    AUTOMOTIVE = "automotive"
    OFFICE = "office"
    OTHER = "other"


class ItemSubcategory(StrEnum):
    FRUIT = "fruit"
    VEGETABLES = "vegetables"
    MEAT_SEAFOOD = "meat_seafood"
    DAIRY_EGGS = "dairy_eggs"
    BAKERY = "bakery"
    PANTRY = "pantry"
    SNACKS = "snacks"
    BEVERAGES = "beverages"
    FROZEN = "frozen"
    PREPARED_FOOD = "prepared_food"
    BABY = "baby"
    CLEANING = "cleaning"
    PAPER_GOODS = "paper_goods"
    LAUNDRY = "laundry"
    KITCHEN = "kitchen"
    TOILETRIES = "toiletries"
    BEAUTY = "beauty"
    PHARMACY = "pharmacy"
    SUPPLEMENTS = "supplements"
    PET_FOOD = "pet_food"
    PET_SUPPLIES = "pet_supplies"
    APPAREL = "apparel"
    ELECTRONICS = "electronics"
    ENTERTAINMENT = "entertainment"
    AUTOMOTIVE = "automotive"
    OFFICE = "office"
    OTHER = "other"


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description_raw: str = Field(
        min_length=1,
        description="Item description exactly or nearly exactly as printed.",
    )
    sku: str | None = Field(
        default=None,
        description="Printed SKU, item code, UPC fragment, or product code.",
    )
    description_normalized: str | None = Field(
        default=None,
        description=(
            "Human-readable normalized item name. Expand obvious receipt "
            "abbreviations only when well supported."
        ),
    )
    brand: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    discount: Decimal | None = Field(
        default=None,
        description="Positive discount amount applied to this line item.",
    )
    amount: Decimal = Field(
        description="Final amount charged for the line item after discounts.",
    )
    category: ItemCategory | None = None
    subcategory: ItemSubcategory | None = None

    @field_validator(
        "sku",
        "description_normalized",
        "brand",
    )
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class LineItemsExtraction(BaseModel):
    """Dedicated schema for exhaustive line-item extraction."""

    model_config = ConfigDict(extra="forbid")

    item_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Printed item count/items sold when explicitly shown on receipt."
        ),
    )
    line_items: list[LineItem] = Field(
        default_factory=list,
        description="Every readable purchased item from the receipt.",
    )


class ReceiptExtraction(BaseModel):
    """Strict extraction contract.

    Critical receipt fields may be null when the source cannot support a
    reliable value. Purchased line items should be exhaustive when readable.
    The deterministic validator blocks automatic acceptance when purchase
    items are missing or conflict with an explicit printed item count.
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
    item_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Printed item count/items sold when explicitly shown on receipt."
        ),
    )
    line_items: list[LineItem] = Field(
        default_factory=list,
        description=(
            "Every readable purchased item. Do not include subtotal, tax, "
            "payment tender, change, loyalty balances, or receipt metadata."
        ),
    )

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
