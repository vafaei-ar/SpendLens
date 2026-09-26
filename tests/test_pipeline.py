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
) -> ReceiptExtraction:
    return ReceiptExtraction.model_validate(
        {
            "merchant": "Example Market",
            "transaction_date": "2026-09-26",
            "subtotal": subtotal,
            "tax": tax,
            "total": total,
            "currency": "USD",
        }
    )


@dataclass
class FakeOCR:
    model: str
    text: str
    fail: bool = False
    calls: int = 0

    def extract_text(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
    ) -> OCRResult:
        self.calls += 1
        if self.fail:
            raise ExtractionProviderError("synthetic OCR failure")
        return OCRResult(
            text=self.text,
            provider="fake-ocr",
            model_id=self.model,
        )


class FakeStructurer:
    model = "fake-structurer"

    def __init__(
        self,
        mapping: dict[str, ReceiptExtraction],
    ) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    def extract_from_text(self, ocr_text: str) -> ExtractionResult:
        self.calls.append(ocr_text)
        extraction = self.mapping[ocr_text]
        return ExtractionResult(
            extraction=extraction,
            raw_response=extraction.model_dump_json(),
            provider="fake-text",
            model_id=self.model,
            prompt_version="test-text",
        )


class FakeGemini:
    model = "gemini-test"

    def __init__(
        self,
        extraction: ReceiptExtraction,
    ) -> None:
        self.extraction = extraction
        self.text_calls = 0
        self.image_calls = 0

    def extract_from_text(self, ocr_text: str) -> ExtractionResult:
        self.text_calls += 1
        return ExtractionResult(
            extraction=self.extraction,
            raw_response=self.extraction.model_dump_json(),
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
        return self.extract_from_text("image")


def test_primary_local_success_stops_cascade() -> None:
    primary = FakeOCR("primary", "primary text")
    secondary = FakeOCR("secondary", "secondary text")
    structurer = FakeStructurer(
        {
            "primary text": receipt(),
            "secondary text": receipt(),
        }
    )

    pipeline = ReceiptExtractionPipeline(
        ocr_extractors=[primary, secondary],
        structurer=structurer,
    )
    result = pipeline.run(
        b"image",
        mime_type="image/jpeg",
    )

    assert result.chosen_index == 0
    assert primary.calls == 1
    assert secondary.calls == 0
    assert result.used_cloud is False


def test_secondary_local_runs_when_primary_fails_validation() -> None:
    primary = FakeOCR("primary", "bad text")
    secondary = FakeOCR("secondary", "good text")
    structurer = FakeStructurer(
        {
            "bad text": receipt(total="11.60"),
            "good text": receipt(),
        }
    )

    pipeline = ReceiptExtractionPipeline(
        ocr_extractors=[primary, secondary],
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


def test_gemini_text_fallback_runs_after_local_validation_failures() -> None:
    primary = FakeOCR("primary", "short")
    secondary = FakeOCR("secondary", "much longer OCR evidence")
    structurer = FakeStructurer(
        {
            "short": receipt(total="11.60"),
            "much longer OCR evidence": receipt(total="12.60"),
        }
    )
    gemini = FakeGemini(receipt())

    pipeline = ReceiptExtractionPipeline(
        ocr_extractors=[primary, secondary],
        structurer=structurer,
        gemini=gemini,
        gemini_mode="ocr_text",
    )
    result = pipeline.run(
        b"image",
        mime_type="image/jpeg",
    )

    assert result.used_cloud
    assert result.chosen_attempt is not None
    assert result.chosen_attempt.model_id == "gemini-test"
    assert gemini.text_calls == 1
    assert gemini.image_calls == 0


def test_gemini_image_fallback_can_run_without_ocr_evidence() -> None:
    primary = FakeOCR("primary", "", fail=True)
    structurer = FakeStructurer({})
    gemini = FakeGemini(receipt())

    pipeline = ReceiptExtractionPipeline(
        ocr_extractors=[primary],
        structurer=structurer,
        gemini=gemini,
        gemini_mode="image",
    )
    result = pipeline.run(
        b"image",
        mime_type="image/jpeg",
    )

    assert result.used_cloud
    assert gemini.image_calls == 1


def test_best_local_failure_is_returned_for_manual_review() -> None:
    primary = FakeOCR("primary", "bad")
    secondary = FakeOCR("secondary", "less bad")
    structurer = FakeStructurer(
        {
            "bad": ReceiptExtraction.model_validate(
                {
                    "merchant": None,
                    "transaction_date": None,
                    "total": None,
                    "currency": None,
                }
            ),
            "less bad": receipt(total="11.60"),
        }
    )

    pipeline = ReceiptExtractionPipeline(
        ocr_extractors=[primary, secondary],
        structurer=structurer,
    )
    result = pipeline.run(
        b"image",
        mime_type="image/jpeg",
    )

    assert result.chosen_index == 1
    assert result.chosen_attempt is not None
    assert result.chosen_attempt.validation is not None
    assert not result.chosen_attempt.validation.auto_accept
