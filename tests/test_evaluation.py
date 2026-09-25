from spendlens.evaluation import EvaluationCase, evaluate_cases
from spendlens.models import ReceiptExtraction


def receipt(
    merchant: str,
    total: str,
) -> ReceiptExtraction:
    return ReceiptExtraction.model_validate(
        {
            "merchant": merchant,
            "transaction_date": "2026-09-24",
            "subtotal": total,
            "total": total,
            "currency": "USD",
        }
    )


def test_false_auto_accept_rate_is_measured() -> None:
    truth = receipt("Example Market", "10.00")
    cases = [
        EvaluationCase(
            case_id="correct",
            truth=truth,
            prediction=receipt("Example Market", "10.00"),
            auto_accepted=True,
        ),
        EvaluationCase(
            case_id="wrong-total",
            truth=truth,
            prediction=receipt("Example Market", "11.00"),
            auto_accepted=True,
        ),
    ]

    metrics = evaluate_cases(cases)

    assert metrics.auto_accept_rate == 1.0
    assert metrics.total_accuracy == 0.5
    assert metrics.critical_receipt_accuracy == 0.5
    assert metrics.false_auto_accept_rate == 0.5
