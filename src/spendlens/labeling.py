from collections.abc import Callable
from datetime import date, time
from decimal import Decimal, InvalidOperation

from spendlens.models import ReceiptExtraction, TransactionType

InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]


def _required_text(
    prompt: str,
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> str:
    while True:
        value = input_fn(prompt).strip()
        if value:
            return value
        output_fn("Required field. Please enter a value.")


def _required_date(
    prompt: str,
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> date:
    while True:
        raw = _required_text(
            prompt,
            input_fn=input_fn,
            output_fn=output_fn,
        )
        try:
            return date.fromisoformat(raw)
        except ValueError:
            output_fn("Invalid date. Use YYYY-MM-DD, for example 2026-09-26.")


def _optional_time(
    prompt: str,
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> time | None:
    while True:
        raw = input_fn(prompt).strip()
        if not raw:
            return None
        try:
            return time.fromisoformat(raw)
        except ValueError:
            output_fn("Invalid time. Use HH:MM or HH:MM:SS, for example 14:32.")


def _decimal_value(
    prompt: str,
    *,
    required: bool,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> Decimal | None:
    while True:
        raw = input_fn(prompt).strip()
        if not raw:
            if required:
                output_fn("Required field. Please enter a numeric amount.")
                continue
            return None
        try:
            return Decimal(raw)
        except InvalidOperation:
            output_fn("Invalid amount. Enter a number such as 57.82 or -12.50.")


def _currency(
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> str:
    while True:
        value = input_fn("Currency [USD]: ").strip().upper() or "USD"
        if len(value) == 3 and value.isalpha():
            return value
        output_fn("Currency must be a 3-letter code such as USD or EUR.")


def _transaction_type(
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> TransactionType:
    allowed = {item.value: item for item in TransactionType}
    while True:
        raw = input_fn("Transaction type [purchase]: ").strip().casefold()
        value = raw or TransactionType.PURCHASE.value
        if value in allowed:
            return allowed[value]
        output_fn("Transaction type must be purchase, refund, or return.")


def interactive_truth(
    *,
    input_fn: InputFn = input,
    output_fn: OutputFn = print,
) -> ReceiptExtraction:
    output_fn(
        "Enter human-verified receipt truth. "
        "Fields marked REQUIRED must be entered manually."
    )
    output_fn(
        "This is the gold-standard label used to evaluate the model, "
        "so do not copy model output into these fields."
    )

    payload = {
        "merchant": _required_text(
            "Merchant (REQUIRED): ",
            input_fn=input_fn,
            output_fn=output_fn,
        ),
        "transaction_date": _required_date(
            "Transaction date YYYY-MM-DD (REQUIRED): ",
            input_fn=input_fn,
            output_fn=output_fn,
        ),
        "transaction_time": _optional_time(
            "Transaction time HH:MM[:SS] (optional): ",
            input_fn=input_fn,
            output_fn=output_fn,
        ),
        "subtotal": _decimal_value(
            "Subtotal (optional): ",
            required=False,
            input_fn=input_fn,
            output_fn=output_fn,
        ),
        "tax": _decimal_value(
            "Tax (optional): ",
            required=False,
            input_fn=input_fn,
            output_fn=output_fn,
        ),
        "tip": _decimal_value(
            "Tip (optional): ",
            required=False,
            input_fn=input_fn,
            output_fn=output_fn,
        ),
        "fees": _decimal_value(
            "Fees (optional): ",
            required=False,
            input_fn=input_fn,
            output_fn=output_fn,
        ),
        "discount": _decimal_value(
            "Discount (optional): ",
            required=False,
            input_fn=input_fn,
            output_fn=output_fn,
        ),
        "total": _decimal_value(
            "Total (REQUIRED): ",
            required=True,
            input_fn=input_fn,
            output_fn=output_fn,
        ),
        "currency": _currency(
            input_fn=input_fn,
            output_fn=output_fn,
        ),
        "transaction_type": _transaction_type(
            input_fn=input_fn,
            output_fn=output_fn,
        ),
        "line_items": [],
    }
    return ReceiptExtraction.model_validate(payload)
