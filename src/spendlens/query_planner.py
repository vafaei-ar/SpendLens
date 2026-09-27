from datetime import date
from typing import Any

from pydantic import ValidationError

from spendlens.query_spec import QueryDecision


class QueryPlanningError(RuntimeError):
    pass


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


class GeminiQueryPlanner:
    """Translate natural language into a strict QueryDecision."""

    def __init__(self, *, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def _client(self) -> Any:
        try:
            from google import genai
        except ImportError as exc:
            raise QueryPlanningError(
                "Query planning requires google-genai."
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
