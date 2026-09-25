from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from spendlens.models import ReceiptExtraction, TransactionType
from spendlens.normalize import normalize_merchant


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
        return [issue for issue in self.issues if issue.severity == "blocker"]


def validate_receipt(
    receipt: ReceiptExtraction,
    *,
    possible_duplicate: bool = False,
    today: date | None = None,
    amount_tolerance: Decimal = Decimal("0.02"),
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    current_date = today or date.today()

    if normalize_merchant(receipt.merchant) in {"", "unknown", "n a", "na", "none"}:
        issues.append(ValidationIssue(
            code="merchant_unusable",
            severity="blocker",
            message="Merchant is missing or not usable.",
        ))

    if receipt.transaction_date > current_date + timedelta(days=1):
        issues.append(ValidationIssue(
            code="future_transaction_date",
            severity="blocker",
            message="Transaction date is implausibly in the future.",
        ))
    elif receipt.transaction_date < current_date - timedelta(days=365 * 5):
        issues.append(ValidationIssue(
            code="old_transaction_date",
            severity="warning",
            message="Transaction date is more than five years old.",
        ))

    if receipt.transaction_type == TransactionType.PURCHASE and receipt.total <= 0:
        issues.append(ValidationIssue(
            code="purchase_total_sign",
            severity="blocker",
            message="Purchase total must be positive.",
        ))

    if receipt.transaction_type in {TransactionType.REFUND, TransactionType.RETURN} and receipt.total >= 0:
        issues.append(ValidationIssue(
            code="refund_total_sign",
            severity="blocker",
            message="Refund and return totals must be normalized as negative.",
        ))

    if receipt.subtotal is None:
        issues.append(ValidationIssue(
            code="insufficient_arithmetic_evidence",
            severity="blocker",
            message="Subtotal is required for automatic acceptance in the MVP.",
        ))
    else:
        expected_total = (
            receipt.subtotal
            + (receipt.tax or Decimal("0"))
            + (receipt.tip or Decimal("0"))
            + (receipt.fees or Decimal("0"))
            - (receipt.discount or Decimal("0"))
        )
        if abs(expected_total - receipt.total) > amount_tolerance:
            issues.append(ValidationIssue(
                code="receipt_arithmetic_mismatch",
                severity="blocker",
                message="Receipt-level arithmetic does not reconcile with total.",
            ))

    if possible_duplicate:
        issues.append(ValidationIssue(
            code="possible_duplicate",
            severity="blocker",
            message="A likely duplicate transaction requires review.",
        ))

    if receipt.line_items and receipt.subtotal is not None:
        line_item_sum = sum((item.amount for item in receipt.line_items), Decimal("0"))
        if abs(line_item_sum - receipt.subtotal) > max(amount_tolerance, Decimal("0.05")):
            issues.append(ValidationIssue(
                code="line_item_sum_mismatch",
                severity="warning",
                message="Line-item sum does not match subtotal; this does not block acceptance.",
            ))

    return ValidationReport(
        auto_accept=not any(issue.severity == "blocker" for issue in issues),
        issues=issues,
    )
