from pathlib import Path

from spendlens.eval_corpus import SourceVariant, create_case, load_corpus
from spendlens.eval_runner import build_report, run_corpus
from spendlens.extraction import ExtractionResult
from spendlens.models import ReceiptExtraction


def receipt(
    *,
    total: str,
    subtotal: str,
    tax: str = "0.60",
) -> ReceiptExtraction:
    return ReceiptExtraction.model_validate(
        {
            "merchant": "Example Market",
            "transaction_date": "2026-09-24",
            "subtotal": subtotal,
            "tax": tax,
            "total": total,
            "currency": "USD",
        }
    )


class FakeExtractor:
    def extract(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
    ) -> ExtractionResult:
        assert mime_type == "image/jpeg"
        if image_bytes == b"photo":
            prediction = receipt(total="11.60", subtotal="11.00")
        else:
            prediction = receipt(total="10.60", subtotal="10.00")
        return ExtractionResult(
            extraction=prediction,
            raw_response=prediction.model_dump_json(),
            provider="fake",
            model_id="fake-vlm",
            prompt_version="test",
        )


def _add_case(
    tmp_path: Path,
    *,
    case_id: str,
    variant: SourceVariant,
    source_bytes: bytes,
) -> None:
    source = tmp_path / f"{case_id}.jpg"
    source.write_bytes(source_bytes)
    create_case(
        corpus_root=tmp_path / "corpus",
        case_id=case_id,
        receipt_key="r001",
        source_path=source,
        source_variant=variant,
        truth=receipt(total="10.60", subtotal="10.00"),
    )


def test_report_detects_silent_photo_error(tmp_path: Path) -> None:
    _add_case(
        tmp_path,
        case_id="r001-photo",
        variant=SourceVariant.TELEGRAM_PHOTO,
        source_bytes=b"photo",
    )
    _add_case(
        tmp_path,
        case_id="r001-file",
        variant=SourceVariant.TELEGRAM_FILE,
        source_bytes=b"file",
    )

    cases = load_corpus(tmp_path / "corpus")
    records = run_corpus(cases, FakeExtractor())
    report = build_report(records)

    assert report.metrics.n == 2
    assert report.metrics.auto_accept_rate == 1.0
    assert report.metrics.false_auto_accept_count == 1
    assert report.metrics.false_auto_accept_rate == 0.5
    assert report.silent_auto_accept_case_ids == ["r001-photo"]
    assert report.delivery_comparison.paired_receipts == 1
    assert report.delivery_comparison.photo_only_wrong == 1
    assert report.delivery_comparison.file_only_wrong == 0
