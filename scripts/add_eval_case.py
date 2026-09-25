import argparse
import json
from pathlib import Path

from spendlens.eval_corpus import SourceVariant, create_case
from spendlens.models import ReceiptExtraction


def _optional(prompt: str) -> str | None:
    value = input(prompt).strip()
    return value or None


def _interactive_truth() -> ReceiptExtraction:
    print("Enter human-verified receipt truth. Blank optional fields are allowed.")
    payload = {
        "merchant": input("Merchant: ").strip(),
        "transaction_date": input("Transaction date (YYYY-MM-DD): ").strip(),
        "transaction_time": _optional("Transaction time (HH:MM[:SS], optional): "),
        "subtotal": _optional("Subtotal (optional): "),
        "tax": _optional("Tax (optional): "),
        "tip": _optional("Tip (optional): "),
        "fees": _optional("Fees (optional): "),
        "discount": _optional("Discount (optional): "),
        "total": input("Total: ").strip(),
        "currency": (input("Currency [USD]: ").strip() or "USD"),
        "transaction_type": (
            input("Transaction type [purchase]: ").strip() or "purchase"
        ),
        "line_items": [],
    }
    return ReceiptExtraction.model_validate(payload)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add a private labeled receipt case to the SpendLens corpus."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--receipt-key", required=True)
    parser.add_argument(
        "--variant",
        required=True,
        choices=[variant.value for variant in SourceVariant],
    )
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=Path("evaluation/private"),
    )
    parser.add_argument(
        "--truth-json",
        type=Path,
        help="Existing human-verified ReceiptExtraction JSON. "
        "If omitted, labels are entered interactively.",
    )
    parser.add_argument("--notes")
    args = parser.parse_args()

    if args.truth_json is not None:
        truth = ReceiptExtraction.model_validate_json(
            args.truth_json.read_text(encoding="utf-8")
        )
    else:
        truth = _interactive_truth()

    case = create_case(
        corpus_root=args.corpus_root,
        case_id=args.case_id,
        receipt_key=args.receipt_key,
        source_path=args.source,
        source_variant=SourceVariant(args.variant),
        truth=truth,
        notes=args.notes,
    )

    print(
        json.dumps(
            {
                "case_id": case.metadata.case_id,
                "receipt_key": case.metadata.receipt_key,
                "source_sha256": case.metadata.source_sha256,
                "source_variant": case.metadata.source_variant.value,
                "case_directory": str(
                    args.corpus_root / case.metadata.case_id
                ),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
