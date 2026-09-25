import pytest
from pydantic import ValidationError

from spendlens.query_spec import QueryDecision, QuerySpec, QueryStatus


def test_answerable_query_requires_query_spec() -> None:
    decision = QueryDecision(
        status=QueryStatus.ANSWERABLE,
        query=QuerySpec(metric="total_spend", group_by="month"),
    )
    assert decision.query is not None


def test_unsupported_query_requires_reason() -> None:
    with pytest.raises(ValidationError):
        QueryDecision(status=QueryStatus.UNSUPPORTED)
