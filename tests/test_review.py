import pytest

from spendlens.models import ReceiptExtraction
from spendlens.review import apply_correction


def extraction() -> ReceiptExtraction:
    return ReceiptExtraction.model_validate(
        {
            "merchant": "Example Market",
            "transaction_date": "2026-09-24",
            "subtotal": "10.00",
            "tax": "0.60",
            "total": "11.60",
            "currency": "USD",
        }
    )


def test_apply_total_correction() -> None:
    corrected = apply_correction(
        extraction(),
        "total 10.60",
    )
    assert corrected.total is not None
    assert str(corrected.total) == "10.60"


def test_apply_merchant_correction_with_spaces() -> None:
    corrected = apply_correction(
        extraction(),
        "merchant Example Market Hershey",
    )
    assert corrected.merchant == "Example Market Hershey"


def test_reject_unknown_correction_field() -> None:
    with pytest.raises(ValueError):
        apply_correction(
            extraction(),
            "banana 5",
        )
