from collections import Counter
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

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
    prediction_rate: float
    merchant_accuracy: float
    date_accuracy: float
    total_accuracy: float
    critical_receipt_accuracy: float
    auto_accept_rate: float
    manual_review_rate: float
    false_auto_accept_count: int
    false_auto_accept_rate: float | None


class DeliveryComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paired_receipts: int = 0
    both_correct: int = 0
    photo_only_wrong: int = 0
    file_only_wrong: int = 0
    both_wrong: int = 0


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: EvaluationMetrics
    silent_auto_accept_case_ids: list[str] = Field(default_factory=list)
    critical_error_case_ids: list[str] = Field(default_factory=list)
    no_prediction_case_ids: list[str] = Field(default_factory=list)
    blocker_counts: dict[str, int] = Field(default_factory=dict)
    by_source_variant: dict[str, EvaluationMetrics] = Field(default_factory=dict)
    delivery_comparison: DeliveryComparison = Field(
        default_factory=DeliveryComparison
    )


def critical_fields_correct(
    case: EvaluationCase,
    *,
    total_tolerance: Decimal = Decimal("0.01"),
) -> bool:
    prediction = case.prediction
    if prediction is None:
        return False

    merchant_ok = (
        case.truth.merchant is not None
        and prediction.merchant is not None
        and normalize_merchant(prediction.merchant)
        == normalize_merchant(case.truth.merchant)
    )
    date_ok = (
        case.truth.transaction_date is not None
        and prediction.transaction_date == case.truth.transaction_date
    )
    total_ok = (
        case.truth.total is not None
        and prediction.total is not None
        and abs(prediction.total - case.truth.total) <= total_tolerance
    )
    return bool(merchant_ok and date_ok and total_ok)


def evaluate_cases(
    cases: list[EvaluationCase],
    *,
    total_tolerance: Decimal = Decimal("0.01"),
) -> EvaluationMetrics:
    if not cases:
        raise ValueError("At least one evaluation case is required")

    predicted = 0
    merchant_correct = 0
    date_correct = 0
    total_correct = 0
    critical_correct = 0
    auto_accepted = 0
    false_auto_accepted = 0

    for case in cases:
        prediction = case.prediction
        predicted += int(prediction is not None)

        merchant_ok = (
            prediction is not None
            and case.truth.merchant is not None
            and prediction.merchant is not None
            and normalize_merchant(prediction.merchant)
            == normalize_merchant(case.truth.merchant)
        )
        date_ok = (
            prediction is not None
            and case.truth.transaction_date is not None
            and prediction.transaction_date
            == case.truth.transaction_date
        )
        total_ok = (
            prediction is not None
            and case.truth.total is not None
            and prediction.total is not None
            and abs(prediction.total - case.truth.total)
            <= total_tolerance
        )
        critical_ok = bool(merchant_ok and date_ok and total_ok)

        merchant_correct += int(merchant_ok)
        date_correct += int(date_ok)
        total_correct += int(total_ok)
        critical_correct += int(critical_ok)

        if case.auto_accepted:
            auto_accepted += 1
            false_auto_accepted += int(not critical_ok)

    n = len(cases)
    auto_accept_rate = auto_accepted / n
    false_rate = (
        false_auto_accepted / auto_accepted
        if auto_accepted
        else None
    )
    return EvaluationMetrics(
        n=n,
        prediction_rate=predicted / n,
        merchant_accuracy=merchant_correct / n,
        date_accuracy=date_correct / n,
        total_accuracy=total_correct / n,
        critical_receipt_accuracy=critical_correct / n,
        auto_accept_rate=auto_accept_rate,
        manual_review_rate=1.0 - auto_accept_rate,
        false_auto_accept_count=false_auto_accepted,
        false_auto_accept_rate=false_rate,
    )


def blocker_counts(
    validation_issues: list[list[str]],
) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for case_issues in validation_issues:
        counter.update(case_issues)
    return dict(sorted(counter.items()))
