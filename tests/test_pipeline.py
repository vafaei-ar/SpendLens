from dataclasses import dataclass

from spendlens.extraction import (
    ExtractionProviderError,
    ExtractionResult,
    OCRResult,
)
from spendlens.models import ReceiptExtraction
from spendlens.pipeline import ReceiptExtractionPipeline


def receipt(
    *,
    total: str | None = "10.60",
    subtotal: str | None = "10.00",
    tax: str | None = "0.60",
    currency: str | None = "USD",
    with_item: bool = True,
) -> ReceiptExtraction:
    line_items = []
    if with_item:
        line_items = [
            {
                "description_raw": "APPLE",
                "description_normalized": "Apple",
                "amount": "10.00",
                "category": "groceries",
                "subcategory": "fruit",
            }
        ]

    return ReceiptExtraction.model_validate(
        {
            "merchant": "Example Market",
            "transaction_date": "2026-09-26",
            "subtotal": subtotal,
            "tax": tax,
            "total": total,
            "currency": currency,
            "line_items": line_items,
        }
    )


class FakeGemini:
    model = "gemini-test"

    def __init__(
        self,
        structured: ReceiptExtraction | None,
        *,
        direct: ReceiptExtraction | None = None,
        transcript: str = (
            "EXAMPLE MARKET\n"
            "APPLE 10.00\n"
            "SUBTOTAL 10.00\n"
            "TAX 0.60\n"
            "TOTAL 10.60"
        ),
        fail_transcription: bool = False,
        fail_structure: bool = False,
        fail_direct: bool = False,
    ) -> None:
        self.structured = structured
        self.direct = direct if direct is not None else structured
        self.transcript = transcript
        self.fail_transcription = fail_transcription
        self.fail_structure = fail_structure
        self.fail_direct = fail_direct
        self.transcription_calls = 0
        self.text_calls = 0
        self.image_calls = 0

    def transcribe_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
    ) -> OCRResult:
        self.transcription_calls += 1
        if self.fail_transcription:
            raise ExtractionProviderError(
                "synthetic Gemini transcription failure"
            )
        return OCRResult(
            text=self.transcript,
            provider="gemini-vision-ocr",
            model_id=self.model,
        )

    def extract_from_text(self, ocr_text: str) -> ExtractionResult:
        self.text_calls += 1
        if self.fail_structure:
            raise ExtractionProviderError(
                "synthetic Gemini structuring failure"
            )
        if self.structured is None:
            raise AssertionError("Missing fake structured extraction")
        return ExtractionResult(
            extraction=self.structured,
            raw_response=self.structured.model_dump_json(),
            provider="gemini",
            model_id=self.model,
            prompt_version="test-gemini",
        )

    def extract_from_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
    ) -> ExtractionResult:
        self.image_calls += 1
        if self.fail_direct:
            raise ExtractionProviderError(
                "synthetic Gemini direct failure"
            )
        if self.direct is None:
            raise AssertionError("Missing fake direct extraction")
        return ExtractionResult(
            extraction=self.direct,
            raw_response=self.direct.model_dump_json(),
            provider="gemini",
            model_id=self.model,
            prompt_version="test-gemini",
        )


@dataclass
class FakeOCR:
    model: str
    text: str
    calls: int = 0

    def extract_text(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
    ) -> OCRResult:
        self.calls += 1
        return OCRResult(
            text=self.text,
            provider="fake-ocr",
            model_id=self.model,
        )


class FakeStructurer:
    model = "fake-structurer"

    def __init__(
        self,
        extraction: ReceiptExtraction,
    ) -> None:
        self.extraction = extraction
        self.calls = 0

    def extract_from_text(self, ocr_text: str) -> ExtractionResult:
        self.calls += 1
        return ExtractionResult(
            extraction=self.extraction,
            raw_response=self.extraction.model_dump_json(),
            provider="fake-text",
            model_id=self.model,
            prompt_version="test-text",
        )


def test_two_pass_gemini_success_stops_before_direct_retry() -> None:
    gemini = FakeGemini(receipt())
    pipeline = ReceiptExtractionPipeline(
        gemini=gemini,
        local_fallback_enabled=False,
    )

    result = pipeline.run(
        b"image",
        mime_type="image/jpeg",
    )

    assert result.chosen_index == 0
    assert result.used_cloud
    assert gemini.transcription_calls == 1
    assert gemini.text_calls == 1
    assert gemini.image_calls == 0
    assert result.chosen_attempt is not None
    assert result.chosen_attempt.provider == (
        "gemini-vision-ocr+gemini-structure"
    )


def test_two_pass_recovers_sams_club_summary_from_transcript() -> None:
    incomplete = ReceiptExtraction.model_validate(
        {
            "merchant": "Sam's Club",
            "transaction_date": "2026-09-21",
            "subtotal": None,
            "tax": None,
            "total": None,
            "currency": None,
            "line_items": [
                {
                    "description_raw": "326086 GOLDKIWI 2LF",
                    "description_normalized": "Gold kiwi 2 lb",
                    "amount": "6.87",
                    "category": "groceries",
                    "subcategory": "fruit",
                }
            ],
        }
    )
    transcript = """
    SAM'S CLUB
    09/21/26 09:09
    326086 GOLDKIWI 2LF 6.87
    SUBTOTAL 48.60
    TAX1 6.0000 % 0.60
    TOTAL 49.20
    """
    gemini = FakeGemini(
        incomplete,
        transcript=transcript,
    )
    pipeline = ReceiptExtractionPipeline(
        gemini=gemini,
        default_currency="USD",
    )

    result = pipeline.run(
        b"image",
        mime_type="image/jpeg",
    )

    assert result.chosen_index == 0
    assert result.chosen_attempt is not None
    extraction = result.chosen_attempt.extraction
    assert extraction is not None
    assert str(extraction.subtotal) == "48.60"
    assert str(extraction.tax) == "0.60"
    assert str(extraction.total) == "49.20"
    assert extraction.currency == "USD"
    assert result.chosen_attempt.validation is not None
    assert result.chosen_attempt.validation.auto_accept
    assert gemini.image_calls == 0


def test_autoaccepted_result_without_items_gets_direct_retry() -> None:
    no_items = receipt(with_item=False)
    richer = receipt(with_item=True)
    gemini = FakeGemini(
        no_items,
        direct=richer,
    )
    pipeline = ReceiptExtractionPipeline(gemini=gemini)

    result = pipeline.run(
        b"image",
        mime_type="image/jpeg",
    )

    assert gemini.image_calls == 1
    assert result.chosen_index == 1
    assert result.chosen_attempt is not None
    assert result.chosen_attempt.extraction is not None
    assert len(result.chosen_attempt.extraction.line_items) == 1


def test_direct_gemini_retry_runs_when_transcription_fails() -> None:
    gemini = FakeGemini(
        None,
        direct=receipt(),
        fail_transcription=True,
    )
    pipeline = ReceiptExtractionPipeline(gemini=gemini)

    result = pipeline.run(
        b"image",
        mime_type="image/jpeg",
    )

    assert len(result.attempts) == 2
    assert result.attempts[0].status == "provider_error"
    assert result.chosen_index == 1
    assert gemini.image_calls == 1
    assert result.chosen_attempt is not None
    assert result.chosen_attempt.validation is not None
    assert result.chosen_attempt.validation.auto_accept


def test_local_fallback_can_run_when_both_gemini_paths_fail() -> None:
    gemini = FakeGemini(
        None,
        fail_transcription=True,
        fail_direct=True,
    )
    local = FakeOCR(
        "local",
        "APPLE 10.00\nSUBTOTAL 10.00\nTAX 0.60\nTOTAL 10.60",
    )
    structurer = FakeStructurer(receipt())

    pipeline = ReceiptExtractionPipeline(
        gemini=gemini,
        local_fallback_enabled=True,
        ocr_extractors=[local],
        structurer=structurer,
    )
    result = pipeline.run(
        b"image",
        mime_type="image/jpeg",
    )

    assert len(result.attempts) == 3
    assert result.chosen_index == 2
    assert result.chosen_attempt is not None
    assert not result.chosen_attempt.cloud
    assert result.chosen_attempt.validation is not None
    assert result.chosen_attempt.validation.auto_accept
    assert local.calls == 1
