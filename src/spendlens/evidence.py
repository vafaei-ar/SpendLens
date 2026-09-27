import re
from decimal import Decimal

from spendlens.extraction import OCRResult
from spendlens.models import ReceiptExtraction


_AMOUNT_RE = re.compile(
    r"(?<!\d)[+-]?\d[\d,]*\.\d{2}(?!\d)"
)
_SUBTOTAL_RE = re.compile(r"\bSUB\s*TOTAL\b|\bSUBTOTAL\b", re.IGNORECASE)
_TAX_RE = re.compile(r"\bTAX\d*\b|\bSALES\s+TAX\b", re.IGNORECASE)
_TOTAL_RE = re.compile(r"\bTOTAL\b", re.IGNORECASE)


def combine_ocr_evidence(evidence: list[OCRResult]) -> str:
    parts: list[str] = []
    for index, item in enumerate(evidence, start=1):
        parts.append(
            f"=== OCR SOURCE {index}: {item.model_id} ===\n{item.text.strip()}"
        )
    return "\n\n".join(parts)


def _amount_after_label(
    text: str,
    label_pattern: re.Pattern[str],
    *,
    take_last: bool = False,
) -> Decimal | None:
    for line in text.splitlines():
        match = label_pattern.search(line)
        if match is None:
            continue

        tail = line[match.end() :]
        amounts = _AMOUNT_RE.findall(tail)
        if not amounts:
            continue

        selected = amounts[-1] if take_last else amounts[0]
        return Decimal(selected.replace(",", ""))

    return None


def recover_receipt_fields(
    extraction: ReceiptExtraction,
    *,
    ocr_text: str,
    default_currency: str | None = None,
) -> tuple[ReceiptExtraction, list[str]]:
    updates: dict[str, object] = {}
    recovered_fields: list[str] = []

    if extraction.subtotal is None:
        subtotal = _amount_after_label(ocr_text, _SUBTOTAL_RE)
        if subtotal is not None:
            updates["subtotal"] = subtotal
            recovered_fields.append("subtotal")

    if extraction.tax is None:
        tax = _amount_after_label(
            ocr_text,
            _TAX_RE,
            take_last=True,
        )
        if tax is not None:
            updates["tax"] = tax
            recovered_fields.append("tax")

    if extraction.total is None:
        total = _amount_after_label(ocr_text, _TOTAL_RE)
        if total is not None:
            updates["total"] = total
            recovered_fields.append("total")

    if extraction.currency is None and default_currency:
        updates["currency"] = default_currency.strip().upper()
        recovered_fields.append("currency")

    if not updates:
        return extraction, []

    payload = extraction.model_dump()
    payload.update(updates)
    return ReceiptExtraction.model_validate(payload), recovered_fields
