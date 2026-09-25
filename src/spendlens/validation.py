from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from spendlens.models import ReceiptExtraction, TransactionType
from spendlens.normalize import normalize_merchant

VALIDATOR_VERSION = "validator-v1"


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["blocker", "warning"]
    message: str


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_accept: bool
    issues: list[ValidationIssue]

    @property
    def blockers(self) -> list[ValidationIssue]:
        return [
            issue for issue in self.issues if issue.severity == "blocker"
        ]


def _block(
    issues: list[ValidationIssue],
    code: str,
    message: str,
) -> None:
    issues.append(
        ValidationIssue(code=code, severity="blocker", message=message)
    )


def validate_receipt(
    receipt: ReceiptExtraction,
    *,
    possible_duplicate: bool = False,
    today: date | None = None,
    amount_tolerance: Decimal = Decimal("0.02"),
) -> ValidationReport:
    """Apply deterministic auto-acceptance checks."""

    issues: list[ValidationIssue] = []
    current_date = today or date.today()

    if normalize_merchant(receipt.merchant) in {
        "",
        "unknown",
        "n a",
        "na",
        "none",
    }:
        _block(
            issues,
            "merchant_unusable",
            "Merchant is missing or not usable.",
        )

    if receipt.transaction_date is None:
        _block(
            issues,
            "date_missing",
            "Transaction date is required for automatic acceptance.",
        )
    elif receipt.transaction_date > current_date + timedelta(days=1):
        _block(
            issues,
            "future_transaction_date",
            "Transaction date is implausibly in the future.",
        )
    elif receipt.transaction_date < current_date - timedelta(days=365 * 5):
        issues.append(
            ValidationIssue(
                code="old_transaction_date",
                severity="warning",
                message="Transaction date is more than five years old.",
            )
        )

    if receipt.currency is None:
        _block(
            issues,
            "currency_missing",
            "Currency is required for automatic acceptance.",
        )

    if receipt.total is None:
        _block(
            issues,
            "total_missing",
            "Total is required for automatic acceptance.",
        )
    else:
        if (
            receipt.transaction_type == TransactionType.PURCHASE
            and receipt.total <= 0
        ):
            _block(
                issues,
                "purchase_total_sign",
                "Purchase total must be positive.",
            )

        refund_types = {
            TransactionType.REFUND,
            TransactionType.RETURN,
        }
        if receipt.transaction_type in refund_types and receipt.total >= 0:
            _block(
                issues,
                "refund_total_sign",
                "Refund and return totals must be normalized as negative.",
            )

    if receipt.subtotal is None:
        _block(
            issues,
            "insufficient_arithmetic_evidence",
            "Subtotal is required for automatic acceptance in the MVP.",
        )
    elif receipt.total is not None:
        expected_total = (
            receipt.subtotal
            + (receipt.tax or Decimal("0"))
            + (receipt.tip or Decimal("0"))
            + (receipt.fees or Decimal("0"))
            - (receipt.discount or Decimal("0"))
        )
        if abs(expected_total - receipt.total) > amount_tolerance:
            _block(
                issues,
                "receipt_arithmetic_mismatch",
                "Receipt-level arithmetic does not reconcile with total.",
            )

    if possible_duplicate:
        _block(
            issues,
            "possible_duplicate",
            "A likely duplicate transaction requires review.",
        )

    if receipt.line_items and receipt.subtotal is not None:
        line_item_sum = sum(
            (item.amount for item in receipt.line_items),
            Decimal("0"),
        )
        line_tolerance = max(amount_tolerance, Decimal("0.05"))
        if abs(line_item_sum - receipt.subtotal) > line_tolerance:
            issues.append(
                ValidationIssue(
                    code="line_item_sum_mismatch",
                    severity="warning",
                    message=(
                        "Line-item sum does not match subtotal; "
                        "this does not block acceptance."
                    ),
                )
            )

    auto_accept = not any(
        issue.severity == "blocker" for issue in issues
    )
    return ValidationReport(auto_accept=auto_accept, issues=issues)
