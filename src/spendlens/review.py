from pydantic import ValidationError

from spendlens.models import ReceiptExtraction
from spendlens.validation import ValidationReport

_FIELD_ALIASES = {
    "merchant": "merchant",
    "date": "transaction_date",
    "time": "transaction_time",
    "subtotal": "subtotal",
    "tax": "tax",
    "tip": "tip",
    "fees": "fees",
    "discount": "discount",
    "total": "total",
    "currency": "currency",
    "type": "transaction_type",
}


def apply_correction(
    extraction: ReceiptExtraction,
    text: str,
) -> ReceiptExtraction:
    field_name, separator, raw_value = text.strip().partition(" ")
    if not separator:
        raise ValueError(
            "Use: field value, for example: total 57.82"
        )

    target = _FIELD_ALIASES.get(field_name.casefold())
    if target is None:
        allowed = ", ".join(sorted(_FIELD_ALIASES))
        raise ValueError(f"Unknown field. Allowed fields: {allowed}")

    value: str | None = raw_value.strip()
    if not value:
        raise ValueError("Correction value cannot be empty")
    if value.casefold() == "null":
        value = None

    payload = extraction.model_dump()
    payload[target] = value

    try:
        return ReceiptExtraction.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"Invalid value for {field_name}") from exc


def format_review(
    *,
    review_id: int,
    extraction: ReceiptExtraction,
    report: ValidationReport,
) -> str:
    blockers = [
        issue.message
        for issue in report.issues
        if issue.severity == "blocker"
    ]
    reason_text = "\n".join(f"- {message}" for message in blockers)
    if not reason_text:
        reason_text = "- Manual verification requested."

    return (
        f"⚠️ Review needed #{review_id}\n"
        f"Merchant: {extraction.merchant or 'unknown'}\n"
        f"Date: {extraction.transaction_date or 'unknown'}\n"
        f"Subtotal: {extraction.subtotal if extraction.subtotal is not None else 'unknown'}\n"
        f"Tax: {extraction.tax if extraction.tax is not None else 'unknown'}\n"
        f"Total: {extraction.total if extraction.total is not None else 'unknown'}\n"
        f"Currency: {extraction.currency or 'unknown'}\n"
        f"Type: {extraction.transaction_type.value}\n\n"
        f"Why review is needed:\n{reason_text}\n\n"
        "Reply to this message with a correction such as:\n"
        "total 57.82\n"
        "date 2026-09-25\n"
        "merchant Costco\n\n"
        f"Or use /accept {review_id} after checking the receipt, "
        f"or /discard {review_id}."
    )
