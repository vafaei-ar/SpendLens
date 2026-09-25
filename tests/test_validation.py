from datetime import date
from decimal import Decimal

from spendlens.models import LineItem, ReceiptExtraction, TransactionType
from spendlens.validation import validate_receipt


def base_receipt(**overrides: object) -> ReceiptExtraction:
    data: dict[str, object] = {
        "merchant": "Example Market",
        "transaction_date": "2026-09-24",
        "subtotal": "10.00",
        "tax": "0.60",
        "total": "10.60",
        "currency": "USD",
    }
    data.update(overrides)
    return ReceiptExtraction.model_validate(data)


def test_valid_receipt_auto_accepts() -> None:
    report = validate_receipt(base_receipt(), today=date(2026, 9, 25))
    assert report.auto_accept
    assert not report.blockers


def test_receipt_arithmetic_mismatch_requires_review() -> None:
    report = validate_receipt(base_receipt(total="11.60"), today=date(2026, 9, 25))
    assert not report.auto_accept
    assert "receipt_arithmetic_mismatch" in {issue.code for issue in report.blockers}


def test_line_item_mismatch_is_warning_only() -> None:
    receipt = base_receipt(
        line_items=[
            LineItem(description_raw="ITEM A", amount=Decimal("4.00")),
            LineItem(description_raw="ITEM B", amount=Decimal("4.00")),
        ]
    )
    report = validate_receipt(receipt, today=date(2026, 9, 25))
    assert report.auto_accept
    assert "line_item_sum_mismatch" in {issue.code for issue in report.issues}


def test_possible_duplicate_requires_review() -> None:
    report = validate_receipt(base_receipt(), possible_duplicate=True, today=date(2026, 9, 25))
    assert not report.auto_accept
    assert "possible_duplicate" in {issue.code for issue in report.blockers}


def test_refund_must_be_negative() -> None:
    receipt = base_receipt(
        subtotal="-10.00",
        tax="-0.60",
        total="-10.60",
        transaction_type=TransactionType.REFUND,
    )
    report = validate_receipt(receipt, today=date(2026, 9, 25))
    assert report.auto_accept
