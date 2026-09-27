from datetime import date

import pytest
from pydantic import ValidationError

from spendlens.query_spec import (
    QueryDecision,
    QueryGroupBy,
    QueryMetric,
    QuerySpec,
    QueryStatus,
)


def test_answerable_query_requires_query_spec() -> None:
    decision = QueryDecision(
        status=QueryStatus.ANSWERABLE,
        query=QuerySpec(
            metric=QueryMetric.TOTAL_SPEND,
            group_by=QueryGroupBy.MONTH,
        ),
    )
    assert decision.query is not None


def test_item_filters_are_schema_constrained() -> None:
    spec = QuerySpec.model_validate(
        {
            "metric": "item_spend",
            "categories": ["groceries"],
            "subcategories": ["fruit"],
            "group_by": "item",
        }
    )
    assert spec.categories[0].value == "groceries"
    assert spec.subcategories[0].value == "fruit"
    assert spec.group_by == QueryGroupBy.ITEM


def test_date_range_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        QuerySpec(
            metric=QueryMetric.TOTAL_SPEND,
            start_date=date(2026, 9, 30),
            end_date=date(2026, 9, 1),
        )


def test_unsupported_query_requires_reason() -> None:
    with pytest.raises(ValidationError):
        QueryDecision(status=QueryStatus.UNSUPPORTED)
