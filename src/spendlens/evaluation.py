from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from spendlens.models import ReceiptExtraction
from spendlens.normalize import normalize_merchant


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    truth: ReceiptExtraction
    prediction: ReceiptExtraction | None
    auto_accepted: bool


class EvaluationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n: int
    merchant_accuracy: float
    date_accuracy: float
    total_accuracy: float
    critical_receipt_accuracy: float
    auto_accept_rate: float
    false_auto_accept_rate: float | None


def evaluate_cases(
    cases: list[EvaluationCase],
    *,
    total_tolerance: Decimal = Decimal("0.01"),
) -> EvaluationMetrics:
    if not cases:
        raise ValueError("At least one evaluation case is required")

    merchant_correct = date_correct = total_correct = critical_correct = 0
    auto_accepted = false_auto_accepted = 0

    for case in cases:
        prediction = case.prediction
        merchant_ok = (
            prediction is not None
            and normalize_merchant(prediction.merchant) == normalize_merchant(case.truth.merchant)
        )
        date_ok = prediction is not None and prediction.transaction_date == case.truth.transaction_date
        total_ok = prediction is not None and abs(prediction.total - case.truth.total) <= total_tolerance
        critical_ok = bool(merchant_ok and date_ok and total_ok)

        merchant_correct += int(merchant_ok)
        date_correct += int(date_ok)
        total_correct += int(total_ok)
        critical_correct += int(critical_ok)

        if case.auto_accepted:
            auto_accepted += 1
            false_auto_accepted += int(not critical_ok)

    n = len(cases)
    return EvaluationMetrics(
        n=n,
        merchant_accuracy=merchant_correct / n,
        date_accuracy=date_correct / n,
        total_accuracy=total_correct / n,
        critical_receipt_accuracy=critical_correct / n,
        auto_accept_rate=auto_accepted / n,
        false_auto_accept_rate=(false_auto_accepted / auto_accepted if auto_accepted else None),
    )
