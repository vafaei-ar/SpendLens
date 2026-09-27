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
    total: str = "10.60",
    subtotal: str = "10.00",
    tax: str = "0.60",
    currency: str | None = "USD",
) -> ReceiptExtraction:
    return ReceiptExtraction.model_validate(
        {
            "merchant": "Example Market",
            "transaction_date": "2026-09-26",
            "subtotal": subtotal,
            "tax": tax,
            "total": total,
            "currency": currency,
        }
    )


class FakeGemini:
    model = "gemini-test"

    def __init__(
        self,
        extraction: ReceiptExtraction | None,
        *,
        fail: bool = False,
    ) -> None:
        self.extraction = extraction
        self.fail = fail
        self.image_calls = 0

    def extract_from_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
    ) -> ExtractionResult:
        self.image_calls += 1
        if self.fail:
            raise ExtractionProviderError("synthetic Gemini failure")
        if self.extraction is None:
            raise AssertionError("Missing fake extraction")
        return ExtractionResult(
            extraction=self.extraction,
            raw_response=self.extraction.model_dump_json(),
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


def test_gemini_image_is_primary_and_skips_local_on_success() -> None:
    gemini = FakeGemini(receipt())
    local = FakeOCR("local", "SUBTOTAL 10.00\nTAX 0.60\nTOTAL 10.60")
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

    assert result.chosen_index == 0
    assert result.used_cloud
    assert gemini.image_calls == 1
    assert local.calls == 0
    assert structurer.calls == 0


def test_default_currency_is_applied_to_gemini_result() -> None:
    gemini = FakeGemini(receipt(currency=None))
    pipeline = ReceiptExtractionPipeline(
        gemini=gemini,
        default_currency="USD",
    )

    result = pipeline.run(
        b"image",
        mime_type="image/jpeg",
    )

    assert result.chosen_attempt is not None
    assert result.chosen_attempt.extraction is not None
    assert result.chosen_attempt.extraction.currency == "USD"
    assert result.chosen_attempt.validation is not None
    assert result.chosen_attempt.validation.auto_accept


def test_gemini_review_result_is_returned_when_local_fallback_off() -> None:
    gemini = FakeGemini(receipt(total="11.60"))
    pipeline = ReceiptExtractionPipeline(
        gemini=gemini,
        local_fallback_enabled=False,
    )

    result = pipeline.run(
        b"image",
        mime_type="image/jpeg",
    )

    assert result.chosen_index == 0
    assert result.chosen_attempt is not None
    assert result.chosen_attempt.validation is not None
    assert not result.chosen_attempt.validation.auto_accept


def test_local_fallback_can_replace_failed_gemini_validation() -> None:
    gemini = FakeGemini(receipt(total="11.60"))
    local = FakeOCR(
        "local",
        "SUBTOTAL 10.00\nTAX 0.60\nTOTAL 10.60",
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

    assert result.chosen_index == 1
    assert result.chosen_attempt is not None
    assert not result.chosen_attempt.cloud
    assert result.chosen_attempt.validation is not None
    assert result.chosen_attempt.validation.auto_accept
    assert local.calls == 1


def test_local_fallback_can_run_when_gemini_provider_fails() -> None:
    gemini = FakeGemini(None, fail=True)
    local = FakeOCR(
        "local",
        "SUBTOTAL 10.00\nTAX 0.60\nTOTAL 10.60",
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

    assert len(result.attempts) == 2
    assert result.chosen_index == 1
    assert result.chosen_attempt is not None
    assert result.chosen_attempt.validation is not None
    assert result.chosen_attempt.validation.auto_accept
