from dataclasses import dataclass
from typing import Literal

from spendlens.extraction import (
    ExtractionParseError,
    ExtractionProviderError,
    ExtractionResult,
    GeminiReceiptExtractor,
    MLXOCRExtractor,
    OCRResult,
    OllamaTextStructurer,
    evidence_envelope,
)
from spendlens.models import ReceiptExtraction
from spendlens.validation import ValidationReport, validate_receipt


AttemptStatus = Literal["parsed", "invalid_schema", "provider_error"]


@dataclass(frozen=True)
class PipelineAttempt:
    provider: str
    model_id: str
    prompt_version: str
    status: AttemptStatus
    raw_response: str | None
    extraction: ReceiptExtraction | None
    validation: ValidationReport | None
    error_text: str | None
    cloud: bool = False


@dataclass(frozen=True)
class PipelineResult:
    attempts: list[PipelineAttempt]
    chosen_index: int | None

    @property
    def chosen_attempt(self) -> PipelineAttempt | None:
        if self.chosen_index is None:
            return None
        return self.attempts[self.chosen_index]

    @property
    def used_cloud(self) -> bool:
        attempt = self.chosen_attempt
        return bool(attempt is not None and attempt.cloud)

    def as_extraction_result(self) -> ExtractionResult:
        attempt = self.chosen_attempt
        if attempt is None or attempt.extraction is None:
            raise ExtractionProviderError(
                "No extractor produced a usable structured receipt"
            )
        return ExtractionResult(
            extraction=attempt.extraction,
            raw_response=attempt.raw_response or "",
            provider=attempt.provider,
            model_id=attempt.model_id,
            prompt_version=attempt.prompt_version,
        )


class ReceiptExtractionPipeline:
    """Local OCR cascade with optional Gemini fallback."""

    def __init__(
        self,
        *,
        ocr_extractors: list[MLXOCRExtractor],
        structurer: OllamaTextStructurer,
        gemini: GeminiReceiptExtractor | None = None,
        gemini_mode: Literal["ocr_text", "image"] = "ocr_text",
    ) -> None:
        self.ocr_extractors = ocr_extractors
        self.structurer = structurer
        self.gemini = gemini
        self.gemini_mode = gemini_mode

    @property
    def description(self) -> str:
        local_models = " -> ".join(
            extractor.model for extractor in self.ocr_extractors
        )
        cloud = (
            f" -> {self.gemini.model} ({self.gemini_mode})"
            if self.gemini is not None
            else " -> cloud off"
        )
        if local_models:
            return f"{local_models} -> {self.structurer.model}{cloud}"
        return f"{self.structurer.model}{cloud}"

    def _structured_attempt(
        self,
        *,
        ocr: OCRResult,
    ) -> PipelineAttempt:
        try:
            result = self.structurer.extract_from_text(ocr.text)
        except ExtractionParseError as exc:
            return PipelineAttempt(
                provider=f"{ocr.provider}+ollama-text",
                model_id=f"{ocr.model_id} -> {self.structurer.model}",
                prompt_version="receipt-text-v1",
                status="invalid_schema",
                raw_response=evidence_envelope(
                    ocr=ocr,
                    structured_response=exc.raw_response,
                ),
                extraction=None,
                validation=None,
                error_text=str(exc),
            )
        except ExtractionProviderError as exc:
            return PipelineAttempt(
                provider=f"{ocr.provider}+ollama-text",
                model_id=f"{ocr.model_id} -> {self.structurer.model}",
                prompt_version="receipt-text-v1",
                status="provider_error",
                raw_response=evidence_envelope(
                    ocr=ocr,
                    structured_response=None,
                ),
                extraction=None,
                validation=None,
                error_text=str(exc),
            )

        report = validate_receipt(result.extraction)
        return PipelineAttempt(
            provider=f"{ocr.provider}+ollama-text",
            model_id=f"{ocr.model_id} -> {result.model_id}",
            prompt_version=result.prompt_version,
            status="parsed",
            raw_response=evidence_envelope(
                ocr=ocr,
                structured_response=result.raw_response,
            ),
            extraction=result.extraction,
            validation=report,
            error_text=None,
        )

    def _gemini_attempt(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
        evidence: list[OCRResult],
    ) -> PipelineAttempt:
        if self.gemini is None:
            raise RuntimeError("Gemini fallback is not configured")

        try:
            if self.gemini_mode == "image":
                result = self.gemini.extract_from_image(
                    image_bytes,
                    mime_type=mime_type,
                )
                envelope = result.raw_response
            else:
                if not evidence:
                    raise ExtractionProviderError(
                        "No OCR evidence is available for Gemini text fallback"
                    )
                best_ocr = max(evidence, key=lambda item: len(item.text))
                result = self.gemini.extract_from_text(best_ocr.text)
                envelope = evidence_envelope(
                    ocr=best_ocr,
                    structured_response=result.raw_response,
                )
        except ExtractionParseError as exc:
            return PipelineAttempt(
                provider="gemini",
                model_id=self.gemini.model,
                prompt_version="receipt-gemini-v1",
                status="invalid_schema",
                raw_response=exc.raw_response,
                extraction=None,
                validation=None,
                error_text=str(exc),
                cloud=True,
            )
        except ExtractionProviderError as exc:
            return PipelineAttempt(
                provider="gemini",
                model_id=self.gemini.model,
                prompt_version="receipt-gemini-v1",
                status="provider_error",
                raw_response=None,
                extraction=None,
                validation=None,
                error_text=str(exc),
                cloud=True,
            )

        report = validate_receipt(result.extraction)
        return PipelineAttempt(
            provider=result.provider,
            model_id=result.model_id,
            prompt_version=result.prompt_version,
            status="parsed",
            raw_response=envelope,
            extraction=result.extraction,
            validation=report,
            error_text=None,
            cloud=True,
        )

    def run(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
    ) -> PipelineResult:
        attempts: list[PipelineAttempt] = []
        evidence: list[OCRResult] = []
        parsed_indexes: list[int] = []

        for extractor in self.ocr_extractors:
            try:
                ocr = extractor.extract_text(
                    image_bytes,
                    mime_type=mime_type,
                )
            except ExtractionProviderError as exc:
                attempts.append(
                    PipelineAttempt(
                        provider="mlx-ocr",
                        model_id=extractor.model,
                        prompt_version="ocr-v1",
                        status="provider_error",
                        raw_response=None,
                        extraction=None,
                        validation=None,
                        error_text=str(exc),
                    )
                )
                continue

            evidence.append(ocr)
            attempt = self._structured_attempt(ocr=ocr)
            attempts.append(attempt)
            current_index = len(attempts) - 1
            if attempt.status == "parsed":
                parsed_indexes.append(current_index)
                if (
                    attempt.validation is not None
                    and attempt.validation.auto_accept
                ):
                    return PipelineResult(
                        attempts=attempts,
                        chosen_index=current_index,
                    )

        if self.gemini is not None:
            cloud_attempt = self._gemini_attempt(
                image_bytes=image_bytes,
                mime_type=mime_type,
                evidence=evidence,
            )
            attempts.append(cloud_attempt)
            cloud_index = len(attempts) - 1
            if cloud_attempt.status == "parsed":
                return PipelineResult(
                    attempts=attempts,
                    chosen_index=cloud_index,
                )

        if parsed_indexes:
            chosen_index = min(
                parsed_indexes,
                key=lambda index: len(
                    attempts[index].validation.blockers
                    if attempts[index].validation is not None
                    else []
                ),
            )
            return PipelineResult(
                attempts=attempts,
                chosen_index=chosen_index,
            )

        return PipelineResult(attempts=attempts, chosen_index=None)

    def extract(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
    ) -> ExtractionResult:
        return self.run(
            image_bytes,
            mime_type=mime_type,
        ).as_extraction_result()
