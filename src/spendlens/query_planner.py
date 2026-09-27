import calendar
import re
from datetime import date, timedelta
from typing import Any

from pydantic import ValidationError

from spendlens.models import ItemCategory, ItemSubcategory
from spendlens.query_spec import (
    QueryDecision,
    QueryGroupBy,
    QueryMetric,
    QuerySpec,
    QueryStatus,
)


class QueryPlanningError(RuntimeError):
    pass


class QueryRateLimitError(QueryPlanningError):
    pass


_CATEGORY_TERMS: dict[str, ItemCategory] = {
    "groceries": ItemCategory.GROCERIES,
    "grocery": ItemCategory.GROCERIES,
    "household": ItemCategory.HOUSEHOLD,
    "personal care": ItemCategory.PERSONAL_CARE,
    "health": ItemCategory.HEALTH,
    "pet": ItemCategory.PET,
    "pets": ItemCategory.PET,
    "clothing": ItemCategory.CLOTHING,
    "electronics": ItemCategory.ELECTRONICS,
    "entertainment": ItemCategory.ENTERTAINMENT,
    "automotive": ItemCategory.AUTOMOTIVE,
    "office": ItemCategory.OFFICE,
}

_SUBCATEGORY_TERMS: dict[str, ItemSubcategory] = {
    "fruit": ItemSubcategory.FRUIT,
    "fruits": ItemSubcategory.FRUIT,
    "vegetable": ItemSubcategory.VEGETABLES,
    "vegetables": ItemSubcategory.VEGETABLES,
    "veggies": ItemSubcategory.VEGETABLES,
    "meat": ItemSubcategory.MEAT_SEAFOOD,
    "seafood": ItemSubcategory.MEAT_SEAFOOD,
    "meat and seafood": ItemSubcategory.MEAT_SEAFOOD,
    "dairy": ItemSubcategory.DAIRY_EGGS,
    "eggs": ItemSubcategory.DAIRY_EGGS,
    "dairy and eggs": ItemSubcategory.DAIRY_EGGS,
    "bakery": ItemSubcategory.BAKERY,
    "pantry": ItemSubcategory.PANTRY,
    "snack": ItemSubcategory.SNACKS,
    "snacks": ItemSubcategory.SNACKS,
    "beverage": ItemSubcategory.BEVERAGES,
    "beverages": ItemSubcategory.BEVERAGES,
    "drinks": ItemSubcategory.BEVERAGES,
    "frozen": ItemSubcategory.FROZEN,
    "frozen food": ItemSubcategory.FROZEN,
    "prepared food": ItemSubcategory.PREPARED_FOOD,
    "baby": ItemSubcategory.BABY,
    "cleaning": ItemSubcategory.CLEANING,
    "paper goods": ItemSubcategory.PAPER_GOODS,
    "laundry": ItemSubcategory.LAUNDRY,
    "kitchen": ItemSubcategory.KITCHEN,
    "toiletries": ItemSubcategory.TOILETRIES,
    "beauty": ItemSubcategory.BEAUTY,
    "pharmacy": ItemSubcategory.PHARMACY,
    "supplements": ItemSubcategory.SUPPLEMENTS,
    "pet food": ItemSubcategory.PET_FOOD,
    "pet supplies": ItemSubcategory.PET_SUPPLIES,
    "apparel": ItemSubcategory.APPAREL,
}

_DATE_PHRASES = (
    "this month",
    "last month",
    "this year",
    "last year",
    "today",
    "yesterday",
)

_PLANNER_PROMPT = """You translate a spending question into a closed SpendLens
query specification. You never write SQL and never calculate an answer.

SpendLens contains only purchases recorded from saved receipts. It is not a
complete bank/account ledger.

Metric meanings:
- total_spend: sum complete receipt totals. Use for merchant/store spending or
  overall recorded receipt spending.
- transaction_count: number of recorded receipts/transactions.
- average_transaction: average complete receipt total.
- item_spend: sum line-item amounts. Use for groceries, fruit, meat, product
  categories, subcategories, or named products.
- item_frequency: count line-item purchase occurrences. Use for questions such
  as "what fruit do I buy most often?". Usually group_by=item.
- unusual_items: items purchased in a target period that were rarely or never
  purchased before that period. If the user gives no period, use the 30 days
  ending on TODAY. Use unusual_max_prior_purchases=1 unless the user clearly
  asks for never-before items, in which case use 0.

Rules:
- Resolve relative dates such as "this month", "last month", and "this year"
  into concrete ISO dates using TODAY.
- If no date range is requested for ordinary spend/frequency questions, leave
  start_date and end_date null so all recorded receipt history is used.
- For "groceries" use category=groceries and metric=item_spend.
- For "fruit" use subcategory=fruit and metric=item_spend unless the user asks
  how often/which fruits, then use item_frequency.
- Named stores belong in merchants.
- Named products belong in item_terms.
- Use group_by only when the user asks for a breakdown/list/trend.
- Return needs_clarification only when a necessary interpretation cannot be
  made safely.
- Return unsupported for questions requiring bank balances, card statements,
  cash transactions not represented by receipts, predictions, or data outside
  the SpendLens receipt database.
- Never infer that recorded receipts represent all of the user's spending.

TODAY: {today}
USER QUESTION:
{question}
"""


def _normalize_question(question: str) -> str:
    value = question.casefold().replace("’", "'")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _month_range(today: date, *, offset: int = 0) -> tuple[date, date]:
    month_index = today.year * 12 + (today.month - 1) + offset
    year, zero_month = divmod(month_index, 12)
    month = zero_month + 1
    first = date(year, month, 1)
    if offset == 0:
        return first, today
    last_day = calendar.monthrange(year, month)[1]
    return first, date(year, month, last_day)


def _date_range(
    text: str,
    *,
    today: date,
) -> tuple[date | None, date | None]:
    if "this month" in text:
        return _month_range(today)
    if "last month" in text:
        return _month_range(today, offset=-1)
    if "this year" in text:
        return date(today.year, 1, 1), today
    if "last year" in text:
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if re.search(r"\btoday\b", text):
        return today, today
    if "yesterday" in text:
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday

    match = re.search(r"\b(?:last|past)\s+(\d{1,3})\s+days?\b", text)
    if match is not None:
        days = max(1, int(match.group(1)))
        return today - timedelta(days=days - 1), today

    return None, None


def _without_date_phrases(text: str) -> str:
    result = text
    for phrase in _DATE_PHRASES:
        result = result.replace(phrase, " ")
    result = re.sub(
        r"\b(?:last|past)\s+\d{1,3}\s+days?\b",
        " ",
        result,
    )
    return re.sub(r"\s+", " ", result).strip(" ?.,!")


def _find_category(text: str) -> ItemCategory | None:
    for term, category in sorted(
        _CATEGORY_TERMS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if re.search(rf"\b{re.escape(term)}\b", text):
            return category
    return None


def _find_subcategory(text: str) -> ItemSubcategory | None:
    for term, subcategory in sorted(
        _SUBCATEGORY_TERMS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if re.search(rf"\b{re.escape(term)}\b", text):
            return subcategory
    return None


def _merchant_from_text(text: str) -> str | None:
    cleaned = _without_date_phrases(text)
    match = re.search(
        r"\b(?:at|from)\s+(.+?)(?:\s+(?:by|during|in)\s+|$)",
        cleaned,
    )
    if match is None:
        return None
    merchant = match.group(1).strip(" ?.,!")
    if not merchant:
        return None
    return merchant


def _group_by(text: str) -> QueryGroupBy | None:
    mappings = (
        ("subcategory", QueryGroupBy.SUBCATEGORY),
        ("category", QueryGroupBy.CATEGORY),
        ("merchant", QueryGroupBy.MERCHANT),
        ("store", QueryGroupBy.MERCHANT),
        ("month", QueryGroupBy.MONTH),
        ("week", QueryGroupBy.WEEK),
        ("day", QueryGroupBy.DAY),
        ("item", QueryGroupBy.ITEM),
        ("product", QueryGroupBy.ITEM),
    )
    for term, group in mappings:
        if (
            f"by {term}" in text
            or f"breakdown by {term}" in text
            or f"per {term}" in text
        ):
            return group
    return None


def _item_term_from_spend_question(
    text: str,
    *,
    category: ItemCategory | None,
    subcategory: ItemSubcategory | None,
) -> str | None:
    if category is not None or subcategory is not None:
        return None

    cleaned = _without_date_phrases(text)
    match = re.search(
        r"\b(?:spend|spent|spending)\s+on\s+(.+)$",
        cleaned,
    )
    if match is None:
        match = re.search(r"\bhow much.*?\bon\s+(.+)$", cleaned)
    if match is None:
        return None

    term = match.group(1).strip(" ?.,!")
    if not term:
        return None
    return term


def _is_unusual_question(text: str) -> bool:
    phrases = (
        "unusual",
        "do not usually buy",
        "don't usually buy",
        "dont usually buy",
        "rarely buy",
        "not normally buy",
        "never bought before",
        "never buy",
    )
    return any(phrase in text for phrase in phrases)


def _is_frequency_question(text: str) -> bool:
    phrases = (
        "most often",
        "most frequently",
        "how often",
        "buy most",
        "bought most",
        "purchase most",
        "purchased most",
    )
    return any(phrase in text for phrase in phrases)


def _local_plan(
    question: str,
    *,
    today: date,
) -> QueryDecision | None:
    text = _normalize_question(question)
    start_date, end_date = _date_range(text, today=today)
    category = _find_category(text)
    subcategory = _find_subcategory(text)
    merchant = _merchant_from_text(text)
    group_by = _group_by(text)

    if _is_unusual_question(text):
        never_before = (
            "never bought before" in text
            or "never buy" in text
        )
        return QueryDecision(
            status=QueryStatus.ANSWERABLE,
            query=QuerySpec(
                metric=QueryMetric.UNUSUAL_ITEMS,
                start_date=start_date,
                end_date=end_date,
                categories=[category] if category is not None else [],
                subcategories=(
                    [subcategory] if subcategory is not None else []
                ),
                merchants=[merchant] if merchant is not None else [],
                unusual_max_prior_purchases=0 if never_before else 1,
            ),
        )

    if _is_frequency_question(text):
        if category is None and subcategory is None:
            item_term = _item_term_from_spend_question(
                text,
                category=category,
                subcategory=subcategory,
            )
            item_terms = [item_term] if item_term else []
        else:
            item_terms = []

        return QueryDecision(
            status=QueryStatus.ANSWERABLE,
            query=QuerySpec(
                metric=QueryMetric.ITEM_FREQUENCY,
                start_date=start_date,
                end_date=end_date,
                categories=[category] if category is not None else [],
                subcategories=(
                    [subcategory] if subcategory is not None else []
                ),
                merchants=[merchant] if merchant is not None else [],
                item_terms=item_terms,
                group_by=group_by or QueryGroupBy.ITEM,
            ),
        )

    if re.search(
        r"\bhow many\s+(?:receipts?|transactions?|purchases?)\b",
        text,
    ):
        return QueryDecision(
            status=QueryStatus.ANSWERABLE,
            query=QuerySpec(
                metric=QueryMetric.TRANSACTION_COUNT,
                start_date=start_date,
                end_date=end_date,
                merchants=[merchant] if merchant is not None else [],
                group_by=group_by,
            ),
        )

    if (
        "average transaction" in text
        or "average receipt" in text
        or "average order" in text
        or "average spend per" in text
    ):
        return QueryDecision(
            status=QueryStatus.ANSWERABLE,
            query=QuerySpec(
                metric=QueryMetric.AVERAGE_TRANSACTION,
                start_date=start_date,
                end_date=end_date,
                merchants=[merchant] if merchant is not None else [],
                group_by=group_by,
            ),
        )

    spend_language = (
        "how much" in text
        or "spend" in text
        or "spent" in text
        or "spending" in text
        or "total" in text
    )
    if spend_language:
        item_term = _item_term_from_spend_question(
            text,
            category=category,
            subcategory=subcategory,
        )
        use_item_metric = (
            category is not None
            or subcategory is not None
            or item_term is not None
        )
        metric = (
            QueryMetric.ITEM_SPEND
            if use_item_metric
            else QueryMetric.TOTAL_SPEND
        )
        return QueryDecision(
            status=QueryStatus.ANSWERABLE,
            query=QuerySpec(
                metric=metric,
                start_date=start_date,
                end_date=end_date,
                categories=[category] if category is not None else [],
                subcategories=(
                    [subcategory] if subcategory is not None else []
                ),
                merchants=[merchant] if merchant is not None else [],
                item_terms=[item_term] if item_term is not None else [],
                group_by=group_by,
            ),
        )

    if (
        re.search(r"\bwhat\s+(?:items?|products?)\s+did\s+i\s+buy\b", text)
        or re.search(r"\bwhat\s+did\s+i\s+buy\b", text)
    ):
        return QueryDecision(
            status=QueryStatus.ANSWERABLE,
            query=QuerySpec(
                metric=QueryMetric.ITEM_FREQUENCY,
                start_date=start_date,
                end_date=end_date,
                categories=[category] if category is not None else [],
                subcategories=(
                    [subcategory] if subcategory is not None else []
                ),
                merchants=[merchant] if merchant is not None else [],
                group_by=QueryGroupBy.ITEM,
            ),
        )

    return None


class GeminiQueryPlanner:
    """Local-first QuerySpec planner with Gemini fallback."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
    ) -> None:
        self.api_key = api_key
        self.model = model

    def _client(self) -> Any:
        if not self.api_key:
            raise QueryPlanningError(
                "This question needs the Gemini fallback, but no API key "
                "is configured."
            )

        try:
            from google import genai
        except ImportError as exc:
            raise QueryPlanningError(
                "Query planning fallback requires google-genai."
            ) from exc

        try:
            return genai.Client(api_key=self.api_key)
        except Exception as exc:
            raise QueryPlanningError(
                f"Could not initialize Gemini query planner: {exc}"
            ) from exc

    def plan(
        self,
        question: str,
        *,
        today: date | None = None,
    ) -> QueryDecision:
        cleaned = question.strip()
        if not cleaned:
            raise QueryPlanningError("Question is empty")

        current_date = today or date.today()

        local = _local_plan(cleaned, today=current_date)
        if local is not None:
            return local

        client = self._client()

        try:
            interaction = client.interactions.create(
                model=self.model,
                input=[
                    {
                        "type": "text",
                        "text": _PLANNER_PROMPT.format(
                            today=current_date.isoformat(),
                            question=cleaned,
                        ),
                    }
                ],
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": QueryDecision.model_json_schema(),
                },
            )
            raw_response = str(interaction.output_text or "").strip()
        except Exception as exc:
            error_text = str(exc)
            folded = error_text.casefold()
            if (
                "429" in folded
                or "rate limit" in folded
                or "rate_limit_exceeded" in folded
                or "too_many_requests" in folded
            ):
                raise QueryRateLimitError(
                    "Gemini fallback quota is currently exhausted. Common "
                    "SpendLens questions work locally; try rephrasing using "
                    "merchant, category, item, date range, frequency, or "
                    "unusual-item language."
                ) from exc
            raise QueryPlanningError(
                f"Gemini query planning failed: {exc}"
            ) from exc

        if not raw_response:
            raise QueryPlanningError(
                "Gemini query planner returned empty output"
            )

        try:
            return QueryDecision.model_validate_json(raw_response)
        except ValidationError as exc:
            raise QueryPlanningError(
                "Gemini query planner returned invalid structured data"
            ) from exc
