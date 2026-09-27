import json
from datetime import date

import pytest

from spendlens.query_planner import GeminiQueryPlanner, QueryPlanningError
from spendlens.query_spec import QueryMetric, QueryStatus


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


def test_query_planner_uses_strict_schema(monkeypatch) -> None:
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
            "group_by": None,
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
        "How much did I spend on fruit?",
        today=date(2026, 9, 27),
    )

    assert decision.status == QueryStatus.ANSWERABLE
    assert decision.query is not None
    assert decision.query.metric == QueryMetric.ITEM_SPEND
    assert decision.query.subcategories[0].value == "fruit"

    call = client.interactions.calls[0]
    response_format = call["response_format"]
    assert response_format["mime_type"] == "application/json"
    assert isinstance(response_format["schema"], dict)

    inputs = call["input"]
    prompt = inputs[0]["text"]
    assert "2026-09-27" in prompt
    assert "How much did I spend on fruit?" in prompt
    assert "never write SQL" in prompt


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
        planner.plan("How much did I spend?")
