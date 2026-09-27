import json
from datetime import date

import pytest

from spendlens.query_planner import (
    GeminiQueryPlanner,
    QueryPlanningError,
    QueryRateLimitError,
)
from spendlens.query_spec import QueryGroupBy, QueryMetric, QueryStatus


class FakeInteraction:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text


class FakeInteractions:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeInteraction(self.output_text)


class FakeClient:
    def __init__(self, output_text: str) -> None:
        self.interactions = FakeInteractions(output_text)


def test_common_fruit_spend_question_is_planned_locally(
    monkeypatch,
) -> None:
    planner = GeminiQueryPlanner(
        api_key=None,
        model="gemini-test",
    )

    def fail_client():
        raise AssertionError("Gemini should not be called")

    monkeypatch.setattr(planner, "_client", fail_client)

    decision = planner.plan(
        "How much did I spend on fruit?",
        today=date(2026, 9, 27),
    )

    assert decision.status == QueryStatus.ANSWERABLE
    assert decision.query is not None
    assert decision.query.metric == QueryMetric.ITEM_SPEND
    assert decision.query.subcategories[0].value == "fruit"


def test_merchant_spend_question_is_planned_locally() -> None:
    planner = GeminiQueryPlanner(api_key=None, model="gemini-test")

    decision = planner.plan(
        "How much did I spend at Sam's Club?",
        today=date(2026, 9, 27),
    )

    assert decision.query is not None
    assert decision.query.metric == QueryMetric.TOTAL_SPEND
    assert decision.query.merchants == ["sam's club"]


def test_frequency_question_is_planned_locally() -> None:
    planner = GeminiQueryPlanner(api_key=None, model="gemini-test")

    decision = planner.plan(
        "What fruit do I buy most often?",
        today=date(2026, 9, 27),
    )

    assert decision.query is not None
    assert decision.query.metric == QueryMetric.ITEM_FREQUENCY
    assert decision.query.subcategories[0].value == "fruit"
    assert decision.query.group_by == QueryGroupBy.ITEM


def test_unusual_this_month_is_planned_locally() -> None:
    planner = GeminiQueryPlanner(api_key=None, model="gemini-test")

    decision = planner.plan(
        "What items did I buy this month that I do not usually buy?",
        today=date(2026, 9, 27),
    )

    assert decision.query is not None
    assert decision.query.metric == QueryMetric.UNUSUAL_ITEMS
    assert decision.query.start_date == date(2026, 9, 1)
    assert decision.query.end_date == date(2026, 9, 27)
    assert decision.query.unusual_max_prior_purchases == 1


def test_last_month_date_range_is_deterministic() -> None:
    planner = GeminiQueryPlanner(api_key=None, model="gemini-test")

    decision = planner.plan(
        "How much did I spend on groceries last month?",
        today=date(2026, 1, 12),
    )

    assert decision.query is not None
    assert decision.query.start_date == date(2025, 12, 1)
    assert decision.query.end_date == date(2025, 12, 31)
    assert decision.query.categories[0].value == "groceries"


def test_unknown_item_spend_is_planned_as_item_term() -> None:
    planner = GeminiQueryPlanner(api_key=None, model="gemini-test")

    decision = planner.plan(
        "How much did I spend on dragonfruit?",
        today=date(2026, 9, 27),
    )

    assert decision.query is not None
    assert decision.query.metric == QueryMetric.ITEM_SPEND
    assert decision.query.item_terms == ["dragonfruit"]


def test_complex_question_falls_back_to_gemini_schema(
    monkeypatch,
) -> None:
    payload = {
        "status": "answerable",
        "query": {
            "metric": "item_spend",
            "start_date": None,
            "end_date": None,
            "categories": [],
            "subcategories": ["fruit"],
            "merchants": [],
            "item_terms": [],
            "group_by": "month",
            "limit": 10,
            "unusual_max_prior_purchases": 1,
        },
        "reason": None,
    }
    planner = GeminiQueryPlanner(
        api_key="test-key",
        model="gemini-test",
    )
    client = FakeClient(json.dumps(payload))
    monkeypatch.setattr(planner, "_client", lambda: client)

    decision = planner.plan(
        "Show me the temporal pattern in my fruit purchases.",
        today=date(2026, 9, 27),
    )

    assert decision.status == QueryStatus.ANSWERABLE
    assert decision.query is not None
    assert decision.query.metric == QueryMetric.ITEM_SPEND

    call = client.interactions.calls[0]
    response_format = call["response_format"]
    assert response_format["mime_type"] == "application/json"
    assert isinstance(response_format["schema"], dict)

    inputs = call["input"]
    prompt = inputs[0]["text"]
    assert "2026-09-27" in prompt
    assert "temporal pattern" in prompt
    assert "never write SQL" in prompt


def test_rate_limit_error_is_sanitized(monkeypatch) -> None:
    planner = GeminiQueryPlanner(
        api_key="test-key",
        model="gemini-test",
    )

    class RateLimitedInteractions:
        def create(self, **kwargs):
            raise RuntimeError(
                "429 RATE_LIMIT_EXCEEDED too_many_requests secret details"
            )

    class RateLimitedClient:
        interactions = RateLimitedInteractions()

    monkeypatch.setattr(planner, "_client", lambda: RateLimitedClient())

    with pytest.raises(QueryRateLimitError) as exc_info:
        planner.plan(
            "Show me the temporal pattern in my fruit purchases."
        )

    message = str(exc_info.value)
    assert "quota is currently exhausted" in message
    assert "secret details" not in message


def test_question_needing_fallback_without_api_key_is_clear() -> None:
    planner = GeminiQueryPlanner(api_key=None, model="gemini-test")

    with pytest.raises(QueryPlanningError) as exc_info:
        planner.plan("Show me the temporal pattern in my fruit purchases.")

    assert "needs the Gemini fallback" in str(exc_info.value)


def test_query_planner_rejects_invalid_structured_output(
    monkeypatch,
) -> None:
    planner = GeminiQueryPlanner(
        api_key="test-key",
        model="gemini-test",
    )
    client = FakeClient('{"status":"answerable","query":null}')
    monkeypatch.setattr(planner, "_client", lambda: client)

    with pytest.raises(QueryPlanningError):
        planner.plan("Show me a strange pattern in my purchases.")
